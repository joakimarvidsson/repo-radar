from __future__ import annotations

from datetime import UTC, datetime, timedelta
from subprocess import CompletedProcess, TimeoutExpired

from repo_radar.config import GitHubSettings
from repo_radar.models import GitMetadata, RepoRecord
from repo_radar.reconciliation import (
    GhCliClient,
    GitHubCache,
    GitHubIdentity,
    GitHubRemoteRepository,
    build_github_reconciliation_report,
    parse_github_remote_url,
    reconcile_record,
    reconcile_records_limited,
    render_github_reconciliation_outputs,
)


class FakeGhClient:
    def __init__(self, payload=None, available=True, authenticated=True):
        self.payload = payload
        self.available = available
        self.authenticated = authenticated
        self.queries: list[GitHubIdentity] = []

    def repo_view(self, identity):
        self.queries.append(identity)
        return self.payload


def test_parse_github_remote_url_variants():
    assert parse_github_remote_url("git@github.com:owner/repo.git") == GitHubIdentity(
        host="github.com", owner="owner", name="repo"
    )
    assert parse_github_remote_url("https://github.com/owner/repo") == GitHubIdentity(
        host="github.com", owner="owner", name="repo"
    )
    assert parse_github_remote_url("ssh://git@github.com/owner/repo.git") == GitHubIdentity(
        host="github.com", owner="owner", name="repo"
    )
    assert parse_github_remote_url("https://example.invalid/owner/repo") is None


def test_reconcile_record_detects_existing_github_repo():
    record = RepoRecord(
        path="/tmp/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "git@github.com:owner/app.git"}),
    )
    client = FakeGhClient(
        payload={
            "nameWithOwner": "owner/app",
            "visibility": "PRIVATE",
            "url": "https://github.com/owner/app",
            "sshUrl": "git@github.com:owner/app.git",
            "defaultBranchRef": {"name": "main"},
        }
    )

    reconciled = reconcile_record(record, client)

    assert reconciled.github is not None
    assert reconciled.github.exists is True
    assert reconciled.github.visibility == "PRIVATE"
    assert reconciled.github.remote_matches is True
    assert reconciled.github.default_branch == "main"


def test_reconcile_record_flags_orphan_when_no_remote():
    record = RepoRecord(path="/tmp/app", name="app", is_git=True, git=GitMetadata(remotes={}))
    reconciled = reconcile_record(record, FakeGhClient())

    assert reconciled.github is not None
    assert reconciled.github.exists is False
    assert reconciled.github.orphan_candidate is True
    assert "No GitHub remote" in reconciled.github.mismatch_reason


def test_reconcile_record_notes_rename_or_drift():
    record = RepoRecord(
        path="/tmp/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "https://github.com/old/app.git"}),
    )
    client = FakeGhClient(payload={"nameWithOwner": "new/app", "visibility": "PUBLIC"})

    reconciled = reconcile_record(record, client)

    assert reconciled.github is not None
    assert reconciled.github.remote_matches is False
    assert "returned new/app" in reconciled.github.mismatch_reason


def test_reconcile_record_uses_fresh_cache_without_querying_client(tmp_path):
    identity = GitHubIdentity(host="github.com", owner="owner", name="app")
    cache = GitHubCache(tmp_path / "github_cache.json", ttl_seconds=3600)
    cache.set(
        identity,
        {
            "nameWithOwner": "owner/app",
            "visibility": "PRIVATE",
            "url": "https://github.com/owner/app",
            "sshUrl": "git@github.com:owner/app.git",
            "defaultBranchRef": {"name": "main"},
        },
    )
    record = RepoRecord(
        path="/tmp/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "git@github.com:owner/app.git"}),
    )
    client = FakeGhClient(payload=None)

    reconciled = reconcile_record(record, client, cache=cache)

    assert client.queries == []
    assert reconciled.github is not None
    assert reconciled.github.checked_with == "gh-cache"
    assert reconciled.github.exists is True


def test_reconcile_record_refreshes_stale_cache(tmp_path):
    identity = GitHubIdentity(host="github.com", owner="owner", name="app")
    cache = GitHubCache(tmp_path / "github_cache.json", ttl_seconds=1)
    cache.set(
        identity,
        {"nameWithOwner": "owner/old", "visibility": "PRIVATE"},
        checked_at=datetime.now(UTC) - timedelta(days=1),
    )
    record = RepoRecord(
        path="/tmp/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "git@github.com:owner/app.git"}),
    )
    client = FakeGhClient(payload={"nameWithOwner": "owner/app", "visibility": "PUBLIC"})

    reconciled = reconcile_record(record, client, cache=cache)

    assert len(client.queries) == 1
    assert reconciled.github is not None
    assert reconciled.github.checked_with == "gh"
    assert reconciled.github.visibility == "PUBLIC"


def test_reconcile_record_reports_gh_timeout_without_raising(monkeypatch):
    record = RepoRecord(
        path="/tmp/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "git@github.com:owner/app.git"}),
    )

    def fake_which(name: str):
        return "/usr/bin/gh" if name == "gh" else None

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "auth", "status"]:
            return CompletedProcess(args=args, returncode=0)
        raise TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr("repo_radar.reconciliation.shutil.which", fake_which)
    monkeypatch.setattr("repo_radar.reconciliation.subprocess.run", fake_run)

    reconciled = reconcile_record(record, GhCliClient(GitHubSettings(timeout_seconds=1)))

    assert reconciled.github is not None
    assert reconciled.github.exists is False
    assert "timed out" in (reconciled.github.error or "")


def test_gh_client_clears_last_error_after_success(monkeypatch):
    calls = {"repo_view": 0}

    def fake_which(name: str):
        return "/usr/bin/gh" if name == "gh" else None

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "auth", "status"]:
            return CompletedProcess(args=args, returncode=0)
        calls["repo_view"] += 1
        if calls["repo_view"] == 1:
            return CompletedProcess(args=args, returncode=1, stderr="not found")
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"nameWithOwner":"owner/app","visibility":"PRIVATE"}',
        )

    monkeypatch.setattr("repo_radar.reconciliation.shutil.which", fake_which)
    monkeypatch.setattr("repo_radar.reconciliation.subprocess.run", fake_run)
    client = GhCliClient(GitHubSettings(timeout_seconds=1))

    assert (
        client.repo_view(GitHubIdentity(host="github.com", owner="owner", name="missing")) is None
    )
    assert client.last_error == "not found"

    payload = client.repo_view(GitHubIdentity(host="github.com", owner="owner", name="app"))

    assert payload == {"nameWithOwner": "owner/app", "visibility": "PRIVATE"}
    assert client.last_error is None


def test_gh_client_list_repos_reports_timeout(monkeypatch):
    def fake_which(name: str):
        return "/usr/bin/gh" if name == "gh" else None

    def fake_run(args, **kwargs):
        raise TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr("repo_radar.reconciliation.shutil.which", fake_which)
    monkeypatch.setattr("repo_radar.reconciliation.subprocess.run", fake_run)
    client = GhCliClient(GitHubSettings(timeout_seconds=1))

    assert client.list_repos("acme") == []
    assert "gh repo list acme timed out" in (client.last_error or "")


def test_reconcile_records_limited_caps_live_checks_and_tracks_skips():
    records = [
        RepoRecord(
            path="/workspace/one",
            name="one",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/acme/one.git"}),
        ),
        RepoRecord(
            path="/workspace/two",
            name="two",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/acme/two.git"}),
        ),
        RepoRecord(
            path="/workspace/three",
            name="three",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/acme/three.git"}),
        ),
    ]

    class LimitedFakeGhClient(FakeGhClient):
        def repo_view(self, identity):
            self.queries.append(identity)
            return {"nameWithOwner": identity.full_name, "visibility": "PRIVATE"}

    reconciled, performed, skipped = reconcile_records_limited(
        records,
        GitHubSettings(),
        limit=2,
        client=LimitedFakeGhClient(),
    )

    assert performed == 2
    assert skipped == 1
    assert [record.github.github_repo if record.github else None for record in reconciled] == [
        "acme/one",
        "acme/two",
        None,
    ]


def test_build_github_reconciliation_report_classifies_core_buckets():
    records = [
        RepoRecord(
            path="/workspace/app",
            name="app",
            is_git=True,
            git=GitMetadata(remotes={"origin": "git@github.com:acme/app.git"}),
        ),
        RepoRecord(
            path="/workspace/app-copy",
            name="app-copy",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/acme/app.git"}),
        ),
        RepoRecord(
            path="/workspace/local-tool",
            name="local-tool",
            is_git=True,
            maturity_score=70,
            git=GitMetadata(remotes={}),
        ),
        RepoRecord(
            path="/workspace/renamed",
            name="renamed",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/acme/old-name.git"}),
        ),
        RepoRecord(
            path="/workspace/mono/apps/api",
            name="api",
            is_git=True,
            relationship_labels=["MONOREPO_SUBPROJECT"],
            monorepo_root_path="/workspace/mono",
            git=GitMetadata(
                git_root="/workspace/mono",
                remotes={"origin": "https://github.com/acme/mono.git"},
            ),
        ),
        RepoRecord(
            path="/workspace/container",
            name="container",
            relationship_labels=["CONTAINER_DIRECTORY"],
        ),
    ]
    records[3].github = reconcile_record(
        records[3],
        FakeGhClient(
            payload={"nameWithOwner": "acme/new-name", "url": "https://github.com/acme/new-name"}
        ),
    ).github
    github_repos = [
        GitHubRemoteRepository(
            name_with_owner="acme/app",
            visibility="PRIVATE",
            url="https://github.com/acme/app",
            ssh_url="git@github.com:acme/app.git",
            default_branch="main",
        ),
        GitHubRemoteRepository(
            name_with_owner="acme/github-only",
            visibility="PRIVATE",
            url="https://github.com/acme/github-only",
            ssh_url="git@github.com:acme/github-only.git",
            default_branch="main",
        ),
    ]

    report = build_github_reconciliation_report(records, owner="acme", github_repos=github_repos)

    assert report.summary.total_local_records == 6
    assert report.summary.missing_remotes == 1
    assert report.summary.github_only == 1
    assert report.missing_remotes[0].path == "/workspace/local-tool"
    assert report.likely_unpublished_local[0].path == "/workspace/local-tool"
    assert report.github_only[0].name_with_owner == "acme/github-only"
    assert report.remote_drift[0].path == "/workspace/renamed"
    assert report.duplicate_local_clones_by_remote[0].remote == "github.com/acme/app"
    assert set(report.duplicate_local_clones_by_remote[0].paths) == {
        "/workspace/app",
        "/workspace/app-copy",
    }
    assert any(item.path == "/workspace/mono/apps/api" for item in report.human_review)
    assert all(item.path != "/workspace/container" for item in report.local_only)
    assert any(
        "git -C /workspace/local-tool status -sb" in action.commands for action in report.actions
    )
    assert all(" rm " not in " ".join(action.commands) for action in report.actions)


def test_build_github_reconciliation_report_distinguishes_remote_types():
    no_remote = RepoRecord(
        path="/workspace/no-remote",
        name="no-remote",
        is_git=True,
        maturity_score=60,
        project_type="python",
        git=GitMetadata(remotes={}),
    )
    github_remote = RepoRecord(
        path="/workspace/github",
        name="github",
        is_git=True,
        git=GitMetadata(remotes={"origin": "https://github.com/acme/github.git"}),
    )
    gitlab_remote = RepoRecord(
        path="/workspace/gitlab",
        name="gitlab",
        is_git=True,
        maturity_score=60,
        project_type="python",
        git=GitMetadata(remotes={"origin": "https://gitlab.com/acme/gitlab.git"}),
    )

    report = build_github_reconciliation_report(
        [no_remote, github_remote, gitlab_remote],
        owner="acme",
        github_repos=[],
    )

    assert [item.path for item in report.missing_remotes] == ["/workspace/no-remote"]
    assert [item.path for item in report.likely_unpublished_local] == ["/workspace/no-remote"]
    assert [item.path for item in report.non_github_remotes] == ["/workspace/gitlab"]
    all_commands = "\n".join(command for action in report.actions for command in action.commands)
    assert "gh repo create acme/no-remote" in all_commands
    assert "gh repo create acme/gitlab" not in all_commands
    assert "https://gitlab.com/acme/gitlab.git" in report.non_github_remotes[0].remote


def test_build_github_reconciliation_report_gracefully_notes_unavailable_github_owner_scan():
    record = RepoRecord(
        path="/workspace/app",
        name="app",
        is_git=True,
        git=GitMetadata(remotes={"origin": "git@github.com:acme/app.git"}),
    )

    report = build_github_reconciliation_report(
        [record],
        owner="acme",
        github_repos=None,
        github_error="gh CLI is not authenticated",
    )

    assert report.github_access_error == "gh CLI is not authenticated"
    assert report.summary.github_only == 0
    assert any("GitHub-only" in item.reason for item in report.human_review)


def test_build_github_reconciliation_report_includes_live_check_accounting():
    report = build_github_reconciliation_report(
        [],
        live_checks_performed=2,
        live_checks_skipped=3,
        live_check_limit=2,
    )

    assert report.summary.live_checks_performed == 2
    assert report.summary.live_checks_skipped == 3
    assert report.summary.live_check_limit == 2
    assert any("Live GitHub checks were partial" in warning for warning in report.warnings)


def test_render_github_reconciliation_outputs_writes_json_markdown_and_plan(tmp_path):
    record = RepoRecord(
        path="/workspace/local-tool",
        name="local-tool",
        is_git=True,
        maturity_score=70,
        git=GitMetadata(remotes={}),
    )
    report = build_github_reconciliation_report([record], owner="acme", github_repos=[])

    outputs = render_github_reconciliation_outputs(report, tmp_path, write_plan=True)

    assert outputs["json"].name == "github_reconciliation.json"
    assert outputs["markdown"].name == "github_reconciliation.md"
    assert outputs["plan"].name == "consolidation_plan.md"
    payload = outputs["json"].read_text(encoding="utf-8")
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    plan = outputs["plan"].read_text(encoding="utf-8")
    assert '"schema_version": "1.0"' in payload
    assert "## Missing remotes" in markdown
    assert "gh repo create acme/local-tool" in markdown
    assert "Safe Consolidation Plan" in plan
    assert "Never execute destructive actions automatically" in plan
    assert plan.count("## Safe next commands") == 0
    assert "### Fix missing or drifted remotes" in plan
