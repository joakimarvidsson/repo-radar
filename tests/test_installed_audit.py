from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repo_radar.installed_audit import (
    LIVE_REFRESH_TIMEOUT_SECONDS,
    build_installed_audit_report,
    classify_installed_repo,
    refresh_record_for_live_check,
)
from repo_radar.installed_audit_rendering import render_installed_audit_outputs
from repo_radar.models import GitMetadata, RepoRecord


def _record(
    path: str,
    *,
    remotes: dict[str, str] | None = None,
    remote: str | None = "https://github.com/acme/tool.git",
    current_branch: str | None = "main",
    upstream_branch: str | None = "origin/main",
    upstream_remote: str | None = None,
    ahead: int | None = 0,
    behind: int | None = 0,
    divergence_status: str | None = "in_sync",
    has_uncommitted_changes: bool = False,
    changed_files: int = 0,
    untracked_files: int = 0,
) -> RepoRecord:
    return RepoRecord(
        path=path,
        name=Path(path).name,
        source_type="local",
        source_name="local",
        is_git=True,
        git=GitMetadata(
            remotes=(
                remotes if remotes is not None else ({} if remote is None else {"origin": remote})
            ),
            git_root=path,
            current_branch=current_branch,
            upstream_branch=upstream_branch,
            upstream_remote=upstream_remote,
            ahead=ahead,
            behind=behind,
            divergence_status=divergence_status,
            has_uncommitted_changes=has_uncommitted_changes,
            changed_files=changed_files,
            untracked_files=untracked_files,
        ),
    )


def test_installed_repo_classification_covers_clean_behind_ahead_and_diverged_states():
    clean = classify_installed_repo(_record("/workspace/clean"))
    behind = classify_installed_repo(
        _record("/workspace/behind", behind=3, divergence_status="behind")
    )
    ahead = classify_installed_repo(_record("/workspace/ahead", ahead=2, divergence_status="ahead"))
    diverged = classify_installed_repo(
        _record("/workspace/diverged", ahead=1, behind=2, divergence_status="diverged")
    )

    assert clean.sync_status == "in_sync"
    assert clean.likely_safe_to_update is False
    assert clean.needs_manual_review is False

    assert behind.sync_status == "behind"
    assert behind.likely_safe_to_update is True
    assert behind.needs_manual_review is False
    assert any("pull --ff-only" in command for command in behind.suggested_commands)

    assert ahead.sync_status == "ahead"
    assert ahead.likely_safe_to_update is False
    assert ahead.needs_manual_review is True

    assert diverged.sync_status == "diverged"
    assert diverged.needs_manual_review is True


def test_installed_repo_classification_flags_non_github_remote_for_manual_review():
    record = classify_installed_repo(
        _record(
            "/workspace/internal-tool",
            remote="git@example.invalid:tools/internal-tool.git",
            behind=2,
            divergence_status="behind",
        )
    )

    assert record.remote_category == "non_github_remote"
    assert record.likely_safe_to_update is False
    assert record.needs_manual_review is True
    assert not any("pull --ff-only" in command for command in record.suggested_commands)


def test_installed_repo_classification_tracks_detached_and_no_upstream_as_manual_review():
    detached = classify_installed_repo(
        _record(
            "/workspace/detached",
            current_branch=None,
        )
    )
    no_upstream = classify_installed_repo(
        _record(
            "/workspace/no-upstream",
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        )
    )

    assert detached.sync_status == "detached"
    assert detached.needs_manual_review is True
    assert no_upstream.sync_status == "no_upstream"
    assert no_upstream.needs_manual_review is True


def test_installed_repo_classification_uses_upstream_remote_when_present():
    record = classify_installed_repo(
        _record(
            "/workspace/multi-remote",
            remotes={
                "origin": "https://github.com/acme/tool.git",
                "upstream": "git@example.invalid:tools/internal-tool.git",
            },
            upstream_branch="upstream/main",
            upstream_remote="upstream",
            behind=2,
            divergence_status="behind",
        )
    )

    assert record.primary_remote is not None
    assert record.primary_remote.name == "upstream"
    assert record.remote_category == "non_github_remote"
    assert record.likely_safe_to_update is False
    assert record.needs_manual_review is True
    assert not any("git remote show" in command for command in record.suggested_commands)


def test_installed_repo_classification_falls_back_when_upstream_remote_is_missing():
    record = classify_installed_repo(
        _record(
            "/workspace/stale-upstream",
            remotes={"origin": "https://github.com/acme/tool.git"},
            upstream_branch="upstream/main",
            upstream_remote="upstream",
            behind=1,
            divergence_status="behind",
        )
    )

    assert record.primary_remote is not None
    assert record.primary_remote.name == "origin"
    assert record.remote_category == "github_remote"
    assert record.likely_safe_to_update is True
    assert record.needs_manual_review is False


def test_installed_repo_classification_handles_missing_remote():
    record = classify_installed_repo(
        _record(
            "/workspace/local-only",
            remote=None,
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        )
    )

    assert record.remote_category == "no_remote"
    assert record.sync_status == "no_remote"
    assert record.needs_manual_review is True


def test_installed_audit_live_mode_respects_limit_and_tracks_skips():
    records = [
        _record("/workspace/one"),
        _record("/workspace/two"),
        _record("/workspace/three"),
    ]
    refreshed: list[str] = []

    def fake_refresher(record: RepoRecord) -> tuple[RepoRecord, str | None]:
        refreshed.append(record.path)
        updated_git = record.git.model_copy(update={"behind": 1, "divergence_status": "behind"})
        return record.model_copy(update={"git": updated_git}), None

    report = build_installed_audit_report(
        records,
        live=True,
        live_limit=2,
        refresher=fake_refresher,
    )

    assert refreshed == ["/workspace/one", "/workspace/two"]
    assert report.summary.live_checks_attempted == 2
    assert report.summary.live_checks_succeeded == 2
    assert report.summary.live_checks_capped == 1
    assert report.summary.live_check_limit == 2
    assert any("capped" in warning for warning in report.warnings)


def test_installed_audit_live_mode_tracks_attempts_success_failures_skips_and_caps(tmp_path):
    records = [
        _record("/workspace/success"),
        _record("/workspace/failure"),
        _record(
            "/workspace/no-remote",
            remote=None,
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        ),
        _record("/workspace/capped"),
    ]

    def fake_refresher(record: RepoRecord) -> tuple[RepoRecord, str | None]:
        if record.path.endswith("failure"):
            return record, "timed out after 10s"
        updated_git = record.git.model_copy(update={"behind": 1, "divergence_status": "behind"})
        return record.model_copy(update={"git": updated_git}), None

    report = build_installed_audit_report(
        records,
        live=True,
        live_limit=2,
        refresher=fake_refresher,
    )

    statuses = {repo.path: repo.live_check_status for repo in report.repositories}
    assert report.summary.live_checks_attempted == 2
    assert report.summary.live_checks_succeeded == 1
    assert report.summary.live_checks_failed == 1
    assert report.summary.live_checks_skipped == 1
    assert report.summary.live_checks_capped == 1
    assert statuses["/workspace/success"] == "succeeded"
    assert statuses["/workspace/failure"] == "failed"
    assert statuses["/workspace/no-remote"] == "skipped"
    assert statuses["/workspace/capped"] == "capped"

    outputs = render_installed_audit_outputs(report, tmp_path)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert "Live checks attempted" in markdown
    assert payload["summary"]["live_checks_attempted"] == 2


def test_refresh_record_for_live_check_uses_timeout_and_non_interactive_env(monkeypatch):
    record = _record("/workspace/tool")
    calls: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("repo_radar.installed_audit.subprocess.run", fake_run)
    monkeypatch.setattr(
        "repo_radar.installed_audit.extract_git_metadata",
        lambda path: record.git.model_copy(),
    )

    updated, error = refresh_record_for_live_check(record)

    assert error is None
    assert updated.git is not None
    assert calls["kwargs"]["timeout"] == LIVE_REFRESH_TIMEOUT_SECONDS
    assert calls["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert calls["kwargs"]["env"]["GCM_INTERACTIVE"] == "never"


def test_refresh_record_for_live_check_reports_timeout(monkeypatch):
    record = _record("/workspace/tool")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=LIVE_REFRESH_TIMEOUT_SECONDS)

    monkeypatch.setattr("repo_radar.installed_audit.subprocess.run", fake_run)

    updated, error = refresh_record_for_live_check(record)

    assert updated == record
    assert error == f"git fetch timed out after {LIVE_REFRESH_TIMEOUT_SECONDS}s"


def test_installed_audit_rendering_writes_markdown_json_and_optional_plan(tmp_path):
    records = [
        _record("/workspace/behind", behind=2, divergence_status="behind"),
        _record(
            "/workspace/dirty",
            has_uncommitted_changes=True,
            changed_files=2,
            untracked_files=1,
        ),
        _record(
            "/workspace/no-remote",
            remote=None,
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        ),
    ]
    report = build_installed_audit_report(records, live=False)

    outputs = render_installed_audit_outputs(report, tmp_path, write_plan=True)

    assert outputs["markdown"] == tmp_path / "installed_audit.md"
    assert outputs["json"] == tmp_path / "installed_audit.json"
    assert outputs["plan"] == tmp_path / "update_plan.md"

    markdown = (tmp_path / "installed_audit.md").read_text(encoding="utf-8")
    assert "Installed Tools Freshness Audit" in markdown
    assert "Advisory only" in markdown
    assert "Likely Safe To Update" in markdown
    assert "Behind Remote" in markdown
    assert "Dirty Working Trees" in markdown
    assert "No Remote" in markdown
    assert "Manual Review" in markdown

    payload = json.loads((tmp_path / "installed_audit.json").read_text(encoding="utf-8"))
    assert payload["summary"]["behind_remote"] == 1
    assert payload["summary"]["dirty_worktrees"] == 1
    assert payload["summary"]["no_remote"] == 1
    assert payload["safe_to_update"][0]["path"] == "/workspace/behind"
