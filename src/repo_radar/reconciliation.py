from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repo_radar.config import GitHubSettings
from repo_radar.models import GitHubReconciliation, RepoRecord

GH_REPO_VIEW_FIELDS = "nameWithOwner,visibility,url,sshUrl,defaultBranchRef"
GH_REPO_LIST_FIELDS = f"{GH_REPO_VIEW_FIELDS},isArchived,isFork"


class GitHubIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubRemoteRepository(BaseModel):
    model_config = ConfigDict(frozen=True)

    name_with_owner: str
    visibility: str | None = None
    url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    is_archived: bool | None = None
    is_fork: bool | None = None

    @property
    def key(self) -> str:
        return self.name_with_owner.lower()


class ReconciliationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_local_records: int = 0
    local_git_repos: int = 0
    github_remote_repos: int = 0
    local_only: int = 0
    github_only: int = 0
    missing_remotes: int = 0
    remote_drift: int = 0
    canonical_mismatches: int = 0
    duplicate_local_clone_sets: int = 0
    likely_unpublished_local: int = 0
    human_review: int = 0
    action_count: int = 0


class ReconciliationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    name: str | None = None
    reason: str
    remote: str | None = None
    github_repo: str | None = None
    git_root: str | None = None
    commands: list[str] = Field(default_factory=list)


class DuplicateLocalCloneGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    remote: str
    paths: list[str] = Field(default_factory=list)
    canonical_path: str | None = None
    commands: list[str] = Field(default_factory=list)


class ReconciliationAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    summary: str
    path: str | None = None
    commands: list[str] = Field(default_factory=list)
    risk: str = "low"
    requires_review: bool = True


class GitHubReconciliationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    owner: str | None = None
    github_access_error: str | None = None
    summary: ReconciliationSummary
    local_only: list[ReconciliationItem] = Field(default_factory=list)
    github_only: list[GitHubRemoteRepository] = Field(default_factory=list)
    missing_remotes: list[ReconciliationItem] = Field(default_factory=list)
    remote_drift: list[ReconciliationItem] = Field(default_factory=list)
    canonical_mismatches: list[ReconciliationItem] = Field(default_factory=list)
    likely_unpublished_local: list[ReconciliationItem] = Field(default_factory=list)
    duplicate_local_clones_by_remote: list[DuplicateLocalCloneGroup] = Field(default_factory=list)
    canonical_recommendations: list[ReconciliationItem] = Field(default_factory=list)
    human_review: list[ReconciliationItem] = Field(default_factory=list)
    actions: list[ReconciliationAction] = Field(default_factory=list)


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
    last_error: str | None = None

    @property
    def available(self) -> bool:
        return shutil.which("gh") is not None

    @property
    def authenticated(self) -> bool:
        self.last_error = None
        if not self.available:
            return False
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self.last_error = f"gh auth status timed out after {exc.timeout} seconds"
            return False
        return result.returncode == 0

    def repo_view(self, identity: GitHubIdentity) -> dict[str, Any] | None:
        self.last_error = None
        try:
            result = subprocess.run(
                [
                    "gh",
                    "repo",
                    "view",
                    identity.full_name,
                    "--json",
                    GH_REPO_VIEW_FIELDS,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self.last_error = (
                f"gh repo view {identity.full_name} timed out after {exc.timeout} seconds"
            )
            return None
        if result.returncode != 0:
            self.last_error = (
                result.stderr.strip() or f"gh repo view failed for {identity.full_name}"
            )
            return None
        return json.loads(result.stdout)

    def list_repos(self, owner: str) -> list[GitHubRemoteRepository]:
        self.last_error = None
        try:
            result = subprocess.run(
                [
                    "gh",
                    "repo",
                    "list",
                    owner,
                    "--limit",
                    str(self.settings.repo_list_limit),
                    "--json",
                    GH_REPO_LIST_FIELDS,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self.last_error = f"gh repo list {owner} timed out after {exc.timeout} seconds"
            return []
        if result.returncode != 0:
            self.last_error = result.stderr.strip() or f"gh repo list failed for {owner}"
            return []
        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            return []
        return [_remote_repository_from_payload(item) for item in payload if isinstance(item, dict)]


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
        client_error = getattr(client, "last_error", None)
        if client_error:
            updated.github = GitHubReconciliation(
                github_repo=identity.full_name,
                exists=False,
                remote_matches=None,
                checked_with="gh",
                error=client_error,
            )
            return updated
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


def build_github_reconciliation_report(
    records: list[RepoRecord],
    owner: str | None = None,
    github_repos: list[GitHubRemoteRepository] | None = None,
    github_error: str | None = None,
) -> GitHubReconciliationReport:
    considered = [
        record for record in sorted(records, key=lambda item: item.path) if not record.suppressed
    ]
    containers = {
        record.path for record in considered if "CONTAINER_DIRECTORY" in record.relationship_labels
    }
    identities_by_path = {
        record.path: _github_identity_for_record(record)
        for record in considered
        if record.path not in containers
    }
    local_remote_keys = {
        identity.full_name.lower()
        for identity in identities_by_path.values()
        if identity is not None
    }

    local_only: list[ReconciliationItem] = []
    missing_remotes: list[ReconciliationItem] = []
    remote_drift: list[ReconciliationItem] = []
    canonical_mismatches: list[ReconciliationItem] = []
    unpublished: list[ReconciliationItem] = []
    human_review: list[ReconciliationItem] = []
    canonical_recommendations: list[ReconciliationItem] = []
    actions: list[ReconciliationAction] = []

    for record in considered:
        if record.path in containers:
            continue
        identity = identities_by_path.get(record.path)
        if identity is None:
            item = _item_for_record(record, "No GitHub remote detected")
            local_only.append(item)
            if record.is_git:
                missing_item = _item_for_record(
                    record,
                    "Git repository has no GitHub remote",
                    commands=_missing_remote_commands(record, owner),
                )
                missing_remotes.append(missing_item)
                actions.append(
                    ReconciliationAction(
                        category="missing_remote",
                        summary=f"Review and add a GitHub remote for {record.name}",
                        path=record.path,
                        commands=missing_item.commands,
                        risk="medium" if owner else "low",
                    )
                )
                if _likely_unpublished(record):
                    unpublished.append(
                        _item_for_record(
                            record,
                            "Mature local git repository without a GitHub remote",
                            commands=missing_item.commands,
                        )
                    )
        elif owner and identity.owner.lower() != owner.lower():
            item = _item_for_record(
                record,
                f"Remote owner {identity.owner} differs from requested owner {owner}",
                remote=_identity_key(identity),
                commands=_remote_expectation_commands(record, identity),
            )
            canonical_mismatches.append(item)
            human_review.append(item)

        if record.github and _is_remote_drift(record.github):
            drift_item = _item_for_record(
                record,
                record.github.mismatch_reason or "GitHub remote did not match live repository data",
                remote=_identity_key(identity) if identity else None,
                github_repo=record.github.github_repo,
                commands=_remote_drift_commands(record, identity),
            )
            remote_drift.append(drift_item)
            actions.append(
                ReconciliationAction(
                    category="remote_drift",
                    summary=f"Review remote drift for {record.name}",
                    path=record.path,
                    commands=drift_item.commands,
                    risk="medium",
                )
            )

        if "MONOREPO_SUBPROJECT" in record.relationship_labels:
            human_review.append(
                _item_for_record(
                    record,
                    "Monorepo subproject; reconcile at the monorepo root before "
                    "treating it as a separate repo",
                    git_root=record.monorepo_root_path
                    or (record.git.git_root if record.git else None),
                )
            )
        if record.likely_canonical or "KEEP" in record.recommendation_labels:
            canonical_recommendations.append(
                _item_for_record(record, "Existing inventory marks this path as canonical")
            )

    duplicate_groups = _duplicate_clone_groups(considered)
    for group in duplicate_groups:
        actions.append(
            ReconciliationAction(
                category="duplicate_local_clone",
                summary=f"Review duplicate local clones for {group.remote}",
                path=group.canonical_path,
                commands=group.commands,
                risk="high",
            )
        )

    github_only = _github_only_repos(github_repos, local_remote_keys)
    if owner and github_repos is None:
        human_review.append(
            ReconciliationItem(
                path="(github)",
                name=owner,
                reason=(
                    "GitHub-only repository detection unavailable: "
                    f"{github_error or 'owner scan was not run'}"
                ),
            )
        )

    summary = ReconciliationSummary(
        total_local_records=len(considered),
        local_git_repos=sum(1 for record in considered if record.is_git),
        github_remote_repos=len(github_repos or []),
        local_only=len(local_only),
        github_only=len(github_only),
        missing_remotes=len(missing_remotes),
        remote_drift=len(remote_drift),
        canonical_mismatches=len(canonical_mismatches),
        duplicate_local_clone_sets=len(duplicate_groups),
        likely_unpublished_local=len(unpublished),
        human_review=len(human_review),
        action_count=len(actions),
    )
    return GitHubReconciliationReport(
        owner=owner,
        github_access_error=github_error,
        summary=summary,
        local_only=local_only,
        github_only=github_only,
        missing_remotes=missing_remotes,
        remote_drift=remote_drift,
        canonical_mismatches=canonical_mismatches,
        likely_unpublished_local=unpublished,
        duplicate_local_clones_by_remote=duplicate_groups,
        canonical_recommendations=canonical_recommendations,
        human_review=human_review,
        actions=actions,
    )


def render_github_reconciliation_outputs(
    report: GitHubReconciliationReport,
    outputs_dir: Path,
    write_plan: bool = False,
) -> dict[str, Path]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    json_path = outputs_dir / "github_reconciliation.json"
    markdown_path = outputs_dir / "github_reconciliation.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_github_reconciliation_markdown(report), encoding="utf-8")
    paths = {"json": json_path, "markdown": markdown_path}
    if write_plan:
        plan_path = outputs_dir / "consolidation_plan.md"
        plan_path.write_text(_consolidation_plan_markdown(report), encoding="utf-8")
        paths["plan"] = plan_path
    return paths


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


def _remote_repository_from_payload(payload: dict[str, Any]) -> GitHubRemoteRepository:
    default_branch_ref = payload.get("defaultBranchRef") or {}
    default_branch = (
        default_branch_ref.get("name") if isinstance(default_branch_ref, dict) else None
    )
    return GitHubRemoteRepository(
        name_with_owner=payload.get("nameWithOwner") or "",
        visibility=payload.get("visibility"),
        url=payload.get("url"),
        ssh_url=payload.get("sshUrl"),
        default_branch=default_branch,
        is_archived=payload.get("isArchived"),
        is_fork=payload.get("isFork"),
    )


def _identity_key(identity: GitHubIdentity) -> str:
    return f"{identity.host.lower()}/{identity.owner.lower()}/{identity.name.lower()}"


def _item_for_record(
    record: RepoRecord,
    reason: str,
    remote: str | None = None,
    github_repo: str | None = None,
    git_root: str | None = None,
    commands: list[str] | None = None,
) -> ReconciliationItem:
    return ReconciliationItem(
        path=record.path,
        name=record.name,
        reason=reason,
        remote=remote,
        github_repo=github_repo,
        git_root=git_root or (record.git.git_root if record.git else None),
        commands=commands or [],
    )


def _likely_unpublished(record: RepoRecord) -> bool:
    if not record.is_git:
        return False
    if "CONTAINER_DIRECTORY" in record.relationship_labels:
        return False
    return record.maturity_score >= 30 or record.project_type not in {"unknown", "repo-like"}


def _is_remote_drift(github: Any) -> bool:
    if getattr(github, "remote_matches", None) is False:
        return True
    if getattr(github, "orphan_candidate", False) and getattr(github, "github_repo", None):
        return True
    return False


def _missing_remote_commands(record: RepoRecord, owner: str | None) -> list[str]:
    path = _quote(record.path)
    commands = [
        f"git -C {path} status -sb",
        f"git -C {path} remote -v",
        f"git -C {path} branch --show-current",
    ]
    if owner:
        repo_name = _repo_slug(record.name or Path(record.path).name)
        commands.append(
            f"gh repo create {owner}/{repo_name} --private --source {path} --remote origin --push"
        )
    return commands


def _remote_expectation_commands(record: RepoRecord, identity: GitHubIdentity) -> list[str]:
    path = _quote(record.path)
    return [
        f"git -C {path} remote -v",
        f"gh repo view {identity.full_name} --json {GH_REPO_VIEW_FIELDS}",
    ]


def _remote_drift_commands(
    record: RepoRecord,
    identity: GitHubIdentity | None,
) -> list[str]:
    path = _quote(record.path)
    commands = [
        f"git -C {path} remote -v",
        f"git -C {path} fetch --all --prune",
    ]
    if identity:
        commands.append(f"gh repo view {identity.full_name} --json {GH_REPO_VIEW_FIELDS}")
    if record.github and record.github.ssh_url:
        commands.append(f"git -C {path} remote set-url origin {_quote(record.github.ssh_url)}")
    elif record.github and record.github.url:
        commands.append(f"git -C {path} remote set-url origin {_quote(record.github.url)}")
    return commands


def _duplicate_clone_groups(records: list[RepoRecord]) -> list[DuplicateLocalCloneGroup]:
    by_remote: dict[str, dict[str, RepoRecord]] = defaultdict(dict)
    for record in records:
        if not record.is_git or not record.git:
            continue
        if "CONTAINER_DIRECTORY" in record.relationship_labels:
            continue
        identity = _github_identity_for_record(record)
        if identity is None:
            continue
        key = _identity_key(identity)
        clone_key = record.git.git_root or record.path
        by_remote[key][clone_key] = record

    groups: list[DuplicateLocalCloneGroup] = []
    for remote, clone_map in sorted(by_remote.items()):
        clones = sorted(clone_map.values(), key=lambda item: item.path)
        if len(clones) < 2:
            continue
        canonical = _canonical_clone(clones)
        commands: list[str] = []
        for duplicate in clones:
            if duplicate.path == canonical.path:
                continue
            commands.extend(
                [
                    f"git -C {_quote(canonical.path)} status -sb",
                    f"git -C {_quote(duplicate.path)} status -sb",
                    f"diff -qr {_quote(canonical.path)} {_quote(duplicate.path)} || true",
                    'mkdir -p "$HOME/repo-archive-staging"',
                    (
                        f"mv {_quote(duplicate.path)} "
                        f'"$HOME/repo-archive-staging/{Path(duplicate.path).name}"'
                    ),
                ]
            )
        groups.append(
            DuplicateLocalCloneGroup(
                remote=remote,
                paths=[record.path for record in clones],
                canonical_path=canonical.path,
                commands=commands,
            )
        )
    return groups


def _canonical_clone(records: list[RepoRecord]) -> RepoRecord:
    return sorted(
        records,
        key=lambda record: (
            0 if record.likely_canonical or "KEEP" in record.recommendation_labels else 1,
            0 if record.git and not record.git.has_uncommitted_changes else 1,
            -(record.maturity_score or 0),
            record.path,
        ),
    )[0]


def _github_only_repos(
    github_repos: list[GitHubRemoteRepository] | None,
    local_remote_keys: set[str],
) -> list[GitHubRemoteRepository]:
    if github_repos is None:
        return []
    return sorted(
        [repo for repo in github_repos if repo.key and repo.key not in local_remote_keys],
        key=lambda repo: repo.name_with_owner.lower(),
    )


def _github_reconciliation_markdown(report: GitHubReconciliationReport) -> str:
    lines = [
        "# GitHub Reconciliation",
        "",
        "This report is a dry, reviewable plan. It does not execute repository changes.",
        "",
        "## Summary",
        "",
        f"- Local records: {report.summary.total_local_records}",
        f"- Local git repos: {report.summary.local_git_repos}",
        f"- GitHub repos checked: {report.summary.github_remote_repos}",
        f"- Local-only repositories: {report.summary.local_only}",
        f"- GitHub-only repositories: {report.summary.github_only}",
        f"- Missing GitHub remotes: {report.summary.missing_remotes}",
        f"- Remote drift / rename drift: {report.summary.remote_drift}",
        f"- Duplicate local clone sets: {report.summary.duplicate_local_clone_sets}",
        f"- Human-review items: {report.summary.human_review}",
    ]
    if report.owner:
        lines.append(f"- Requested GitHub owner: `{report.owner}`")
    if report.github_access_error:
        lines.append(f"- GitHub access: {report.github_access_error}")

    _append_items(lines, "Local-only repositories", report.local_only)
    _append_github_only(lines, report.github_only)
    _append_items(lines, "Missing remotes", report.missing_remotes)
    _append_items(lines, "Remote drift / rename drift", report.remote_drift)
    _append_items(lines, "Canonical expectation mismatches", report.canonical_mismatches)
    _append_duplicate_groups(lines, report.duplicate_local_clones_by_remote)
    _append_items(lines, "Canonical repo recommendations", report.canonical_recommendations)
    _append_actions(lines, report.actions)
    _append_items(lines, "Risks / assumptions / manual-review items", report.human_review)
    return "\n".join(lines) + "\n"


def _consolidation_plan_markdown(report: GitHubReconciliationReport) -> str:
    lines = [
        "# Safe Consolidation Plan",
        "",
        "Never execute destructive actions automatically. Run these commands only after reviewing "
        "the reconciliation report, checking dirty worktrees, and confirming the intended "
        "canonical repo.",
        "",
        "## Phase 1: Snapshot current state",
        "",
        "```bash",
        'mkdir -p "$HOME/repo-radar-review"',
        "```",
        "",
        "## Phase 2: Fix missing or drifted remotes",
        "",
    ]
    _append_actions(
        lines,
        [action for action in report.actions if action.category != "duplicate_local_clone"],
    )
    lines.extend(
        [
            "",
            "## Phase 3: Review duplicate local clones",
            "",
        ]
    )
    _append_actions(
        lines,
        [action for action in report.actions if action.category == "duplicate_local_clone"],
    )
    lines.extend(
        [
            "",
            "## Phase 4: Re-run repo-radar",
            "",
            "```bash",
            "repo-radar reconcile github --write-plan",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _append_items(lines: list[str], title: str, items: list[ReconciliationItem]) -> None:
    lines.extend(["", f"## {title}", ""])
    if not items:
        lines.append("- None detected.")
        return
    for item in items:
        lines.append(f"- `{item.path}`: {item.reason}")
        if item.remote:
            lines.append(f"  Remote: `{item.remote}`")
        if item.github_repo:
            lines.append(f"  GitHub: `{item.github_repo}`")
        if item.git_root and item.git_root != item.path:
            lines.append(f"  Git root: `{item.git_root}`")
        if item.commands:
            lines.append("")
            lines.append("  ```bash")
            for command in item.commands:
                lines.append(f"  {command}")
            lines.append("  ```")


def _append_github_only(lines: list[str], items: list[GitHubRemoteRepository]) -> None:
    lines.extend(["", "## GitHub-only repositories", ""])
    if not items:
        lines.append("- None detected.")
        return
    for item in items:
        visibility = f" ({item.visibility})" if item.visibility else ""
        lines.append(f"- `{item.name_with_owner}`{visibility}: {item.url or 'no URL available'}")


def _append_duplicate_groups(lines: list[str], groups: list[DuplicateLocalCloneGroup]) -> None:
    lines.extend(["", "## Duplicate local clones by remote", ""])
    if not groups:
        lines.append("- None detected.")
        return
    for group in groups:
        lines.append(f"- `{group.remote}`")
        lines.append(f"  Canonical candidate: `{group.canonical_path}`")
        for path in group.paths:
            lines.append(f"  - `{path}`")
        if group.commands:
            lines.append("")
            lines.append("  ```bash")
            for command in group.commands:
                lines.append(f"  {command}")
            lines.append("  ```")


def _append_actions(lines: list[str], actions: list[ReconciliationAction]) -> None:
    lines.extend(["", "## Safe next commands", ""])
    if not actions:
        lines.append("- None generated.")
        return
    for action in actions:
        review = "review required" if action.requires_review else "low-risk"
        lines.append(f"- {action.summary} ({action.category}, {action.risk} risk, {review})")
        lines.append("")
        lines.append("  ```bash")
        for command in action.commands:
            lines.append(f"  {command}")
        lines.append("  ```")


def _repo_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug.lower() or "repo"


def _quote(value: str) -> str:
    return shlex.quote(value)


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
