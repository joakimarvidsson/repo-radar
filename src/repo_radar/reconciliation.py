from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

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


def reconcile_record(record: RepoRecord, client: Any | None = None) -> RepoRecord:
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
        checked_with="gh",
    )
    return updated


def reconcile_records(records: list[RepoRecord], settings: GitHubSettings) -> list[RepoRecord]:
    if not settings.enabled:
        return records
    client = GhCliClient(settings)
    return [reconcile_record(record, client) for record in records]


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
