from __future__ import annotations

from repo_radar.models import GitMetadata, RepoRecord
from repo_radar.reconciliation import GitHubIdentity, parse_github_remote_url, reconcile_record


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
