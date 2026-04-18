from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repo_radar.models import GitMetadata, RepoRecord
from repo_radar.reconciliation import (
    GitHubCache,
    GitHubIdentity,
    parse_github_remote_url,
    reconcile_record,
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
