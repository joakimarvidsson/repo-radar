from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repo_radar.config import GitHubSettings
from repo_radar.models import GitHubReconciliation, RepoRecord


class GitHubIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubCache(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path
    ttl_seconds: int = 86_400
    read_only: bool = False
    entries: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def __init__(
        self,
        path: Path | str,
        ttl_seconds: int = 86_400,
        read_only: bool = False,
        entries: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            path=Path(path),
            ttl_seconds=ttl_seconds,
            read_only=read_only,
            entries=entries or {},
        )

    def model_post_init(self, __context: Any) -> None:
        self._load()

    def get(self, identity: GitHubIdentity) -> dict[str, Any] | None:
        entry = self.entries.get(_cache_key(identity))
        if not entry:
            return None
        checked_at = _parse_datetime(entry.get("checked_at"))
        if checked_at is None:
            return None
        age_seconds = (datetime.now(UTC) - checked_at).total_seconds()
        if self.ttl_seconds <= 0 or age_seconds > self.ttl_seconds:
            return None
        payload = entry.get("payload")
        return payload if isinstance(payload, dict) else None

    def set(
        self,
        identity: GitHubIdentity,
        payload: dict[str, Any],
        checked_at: datetime | None = None,
    ) -> None:
        self.entries[_cache_key(identity)] = {
            "checked_at": (checked_at or datetime.now(UTC)).isoformat(),
            "payload": payload,
        }
        if not self.read_only:
            self.write()

    def write(self) -> None:
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "entries": self.entries,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        entries = payload.get("entries")
        if isinstance(entries, dict):
            self.entries = entries


@dataclass
class GhCliClient:
    settings: GitHubSettings

    @property
    def available(self) -> bool:
        return shutil.which("gh") is not None

    @property
    def authenticated(self) -> bool:
        if not self.available:
            return False
        result = subprocess.run(
            ["gh", "auth", "status"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=self.settings.timeout_seconds,
        )
        return result.returncode == 0

    def repo_view(self, identity: GitHubIdentity) -> dict[str, Any] | None:
        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                identity.full_name,
                "--json",
                "nameWithOwner,visibility,url,sshUrl,defaultBranchRef",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.settings.timeout_seconds,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)


def parse_github_remote_url(url: str) -> GitHubIdentity | None:
    patterns = [
        r"^git@(?P<host>github\.com):(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$",
        r"^ssh://git@(?P<host>github\.com)/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?$",
        r"^https://(?P<host>github\.com)/(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return GitHubIdentity(**match.groupdict())
    return None


def reconcile_record(
    record: RepoRecord,
    client: Any | None = None,
    cache: GitHubCache | None = None,
) -> RepoRecord:
    updated = record.model_copy(deep=True)
    identity = _github_identity_for_record(record)
    if identity is None:
        updated.github = GitHubReconciliation(
            exists=False,
            orphan_candidate=bool(record.is_git),
            mismatch_reason="No GitHub remote detected"
            if record.is_git
            else "Not a git repository",
            checked_with="remote-parse",
        )
        return updated

    cached_payload = cache.get(identity) if cache else None
    if cached_payload is not None:
        return _apply_repo_payload(updated, identity, cached_payload, checked_with="gh-cache")

    if client is None:
        client = GhCliClient(GitHubSettings())
    if not getattr(client, "available", False):
        updated.github = GitHubReconciliation(
            github_repo=identity.full_name,
            exists=False,
            remote_matches=None,
            checked_with="gh",
            error="gh CLI is not available",
        )
        return updated
    if not getattr(client, "authenticated", False):
        updated.github = GitHubReconciliation(
            github_repo=identity.full_name,
            exists=False,
            remote_matches=None,
            checked_with="gh",
            error="gh CLI is not authenticated",
        )
        return updated

    payload = client.repo_view(identity)
    if not payload:
        updated.github = GitHubReconciliation(
            github_repo=identity.full_name,
            exists=False,
            orphan_candidate=True,
            remote_matches=False,
            checked_with="gh",
            mismatch_reason="GitHub repository was not found",
        )
        return updated

    if cache:
        cache.set(identity, payload)
    return _apply_repo_payload(updated, identity, payload, checked_with="gh")


def _apply_repo_payload(
    record: RepoRecord,
    identity: GitHubIdentity,
    payload: dict[str, Any],
    checked_with: str,
) -> RepoRecord:
    updated = record.model_copy(deep=True)
    returned = payload.get("nameWithOwner") or identity.full_name
    matches = returned.lower() == identity.full_name.lower()
    default_branch_ref = payload.get("defaultBranchRef") or {}
    updated.github = GitHubReconciliation(
        github_repo=returned,
        exists=True,
        visibility=payload.get("visibility"),
        url=payload.get("url"),
        ssh_url=payload.get("sshUrl"),
        default_branch=default_branch_ref.get("name")
        if isinstance(default_branch_ref, dict)
        else None,
        remote_matches=matches,
        mismatch_reason=None
        if matches
        else f"GitHub returned {returned} for remote {identity.full_name}",
        checked_with=checked_with,
    )
    return updated


def reconcile_records(
    records: list[RepoRecord],
    settings: GitHubSettings,
    cache: GitHubCache | None = None,
) -> list[RepoRecord]:
    if not settings.enabled:
        return records
    client = GhCliClient(settings)
    return [reconcile_record(record, client, cache=cache) for record in records]


def _github_identity_for_record(record: RepoRecord) -> GitHubIdentity | None:
    if not record.git:
        return None
    ordered_urls = []
    if "origin" in record.git.remotes:
        ordered_urls.append(record.git.remotes["origin"])
    ordered_urls.extend(url for name, url in record.git.remotes.items() if name != "origin")
    for url in ordered_urls:
        identity = parse_github_remote_url(url)
        if identity:
            return identity
    return None


def _cache_key(identity: GitHubIdentity) -> str:
    return f"{identity.host.lower()}/{identity.owner.lower()}/{identity.name.lower()}"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
