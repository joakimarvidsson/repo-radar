from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field

from repo_radar.metadata import extract_git_metadata
from repo_radar.models import RadarBaseModel, RepoRecord
from repo_radar.reconciliation import parse_github_remote_url

LIVE_REFRESH_TIMEOUT_SECONDS = 10
_LIVE_REFRESH_ENV_OVERRIDES = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "true",
    "SSH_ASKPASS": "true",
    "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
}

RemoteCategory = Literal["github_remote", "non_github_remote", "no_remote"]
SyncStatus = Literal[
    "in_sync",
    "behind",
    "ahead",
    "diverged",
    "detached",
    "no_upstream",
    "no_remote",
    "manual_review",
]
LiveCheckStatus = Literal["not_requested", "succeeded", "failed", "skipped", "capped"]
LiveRefresher = Callable[[RepoRecord], tuple[RepoRecord, str | None]]


class InstalledAuditRemote(RadarBaseModel):
    name: str
    url: str
    category: Literal["github", "non_github"]
    host: str | None = None
    github_repo: str | None = None


class InstalledAuditRepo(RadarBaseModel):
    path: str
    name: str | None = None
    git_root: str | None = None
    current_branch: str | None = None
    upstream_branch: str | None = None
    default_branch: str | None = None
    remote_category: RemoteCategory = "no_remote"
    primary_remote: InstalledAuditRemote | None = None
    remotes: list[InstalledAuditRemote] = Field(default_factory=list)
    sync_status: SyncStatus = "manual_review"
    ahead: int | None = None
    behind: int | None = None
    has_uncommitted_changes: bool = False
    changed_files: int = 0
    untracked_files: int = 0
    likely_safe_to_update: bool = False
    needs_manual_review: bool = False
    reasons: list[str] = Field(default_factory=list)
    suggested_commands: list[str] = Field(default_factory=list)
    live_check_status: LiveCheckStatus = "not_requested"
    live_check_error: str | None = None


class InstalledAuditSummary(RadarBaseModel):
    total_git_repositories: int = 0
    github_remote: int = 0
    non_github_remote: int = 0
    no_remote: int = 0
    safe_to_update: int = 0
    behind_remote: int = 0
    dirty_worktrees: int = 0
    ahead_of_remote: int = 0
    diverged: int = 0
    detached_or_unusual: int = 0
    manual_review: int = 0
    live_checks_attempted: int = 0
    live_checks_succeeded: int = 0
    live_checks_failed: int = 0
    live_checks_capped: int = 0
    live_checks_performed: int = 0
    live_checks_skipped: int = 0
    live_check_limit: int | None = None


class InstalledAuditReport(RadarBaseModel):
    schema_version: str = "1.0"
    generated_at: str
    advisory_only: bool = True
    live_mode: bool = False
    summary: InstalledAuditSummary
    warnings: list[str] = Field(default_factory=list)
    repositories: list[InstalledAuditRepo] = Field(default_factory=list)
    safe_to_update: list[InstalledAuditRepo] = Field(default_factory=list)
    behind_remote: list[InstalledAuditRepo] = Field(default_factory=list)
    dirty_worktrees: list[InstalledAuditRepo] = Field(default_factory=list)
    no_remote: list[InstalledAuditRepo] = Field(default_factory=list)
    non_github_remotes: list[InstalledAuditRepo] = Field(default_factory=list)
    manual_review: list[InstalledAuditRepo] = Field(default_factory=list)


def build_installed_audit_report(
    records: list[RepoRecord],
    *,
    live: bool = False,
    live_limit: int = 25,
    refresher: LiveRefresher | None = None,
) -> InstalledAuditReport:
    warnings = [
        "Advisory only: repo-radar does not pull, merge, rebase, delete, or push repositories."
    ]
    refresher = refresher or refresh_record_for_live_check
    report_items: list[InstalledAuditRepo] = []
    live_checks_attempted = 0
    live_checks_succeeded = 0
    live_checks_failed = 0
    live_checks_skipped = 0
    live_checks_capped = 0
    git_records = [
        record.model_copy(deep=True) for record in records if record.is_git and record.git
    ]

    if not live:
        warnings.append(
            "Results are local-first and based on local tracking refs. "
            "Re-run with --live to refresh a bounded number of remotes."
        )

    for record in git_records:
        live_status: LiveCheckStatus = "not_requested"
        live_error: str | None = None
        current = record
        if live and _live_eligible(record):
            if live_checks_attempted < live_limit:
                current, live_error = refresher(record)
                live_checks_attempted += 1
                if live_error:
                    live_status = "failed"
                    live_checks_failed += 1
                else:
                    live_status = "succeeded"
                    live_checks_succeeded += 1
            else:
                live_status = "capped"
                live_checks_capped += 1
        elif live:
            live_status = "skipped"
            live_checks_skipped += 1
        report_items.append(
            classify_installed_repo(
                current,
                live_check_status=live_status,
                live_check_error=live_error,
            )
        )

    if live and live_checks_capped:
        warnings.append(
            f"Live remote refresh was capped at {live_limit} repositories; "
            f"{live_checks_capped} repositories remained local-only."
        )
    if live and live_checks_failed:
        warnings.append(
            f"Live remote refresh failed for {live_checks_failed} repositories; "
            "those results remained based on existing local tracking refs."
        )

    summary = InstalledAuditSummary(
        total_git_repositories=len(report_items),
        github_remote=sum(1 for item in report_items if item.remote_category == "github_remote"),
        non_github_remote=sum(
            1 for item in report_items if item.remote_category == "non_github_remote"
        ),
        no_remote=sum(1 for item in report_items if item.remote_category == "no_remote"),
        safe_to_update=sum(1 for item in report_items if item.likely_safe_to_update),
        behind_remote=sum(1 for item in report_items if item.sync_status == "behind"),
        dirty_worktrees=sum(1 for item in report_items if item.has_uncommitted_changes),
        ahead_of_remote=sum(1 for item in report_items if item.sync_status == "ahead"),
        diverged=sum(1 for item in report_items if item.sync_status == "diverged"),
        detached_or_unusual=sum(
            1
            for item in report_items
            if item.sync_status in {"detached", "no_upstream", "manual_review"}
        ),
        manual_review=sum(1 for item in report_items if item.needs_manual_review),
        live_checks_attempted=live_checks_attempted,
        live_checks_succeeded=live_checks_succeeded,
        live_checks_failed=live_checks_failed,
        live_checks_capped=live_checks_capped,
        live_checks_performed=live_checks_succeeded,
        live_checks_skipped=live_checks_skipped,
        live_check_limit=live_limit if live else None,
    )
    ordered_items = _sort_report_items(report_items)
    return InstalledAuditReport(
        generated_at=datetime.now(UTC).isoformat(),
        live_mode=live,
        summary=summary,
        warnings=warnings,
        repositories=ordered_items,
        safe_to_update=_sort_report_items(
            [item for item in report_items if item.likely_safe_to_update],
            sort_by_behind=True,
        ),
        behind_remote=_sort_report_items(
            [item for item in report_items if item.sync_status == "behind"],
            sort_by_behind=True,
        ),
        dirty_worktrees=_sort_report_items(
            [item for item in report_items if item.has_uncommitted_changes]
        ),
        no_remote=_sort_report_items(
            [item for item in report_items if item.remote_category == "no_remote"]
        ),
        non_github_remotes=_sort_report_items(
            [item for item in report_items if item.remote_category == "non_github_remote"]
        ),
        manual_review=_sort_report_items(
            [item for item in report_items if item.needs_manual_review]
        ),
    )


def classify_installed_repo(
    record: RepoRecord,
    *,
    live_check_status: LiveCheckStatus = "not_requested",
    live_check_error: str | None = None,
) -> InstalledAuditRepo:
    if record.git is None:
        raise ValueError("installed audit requires git metadata")

    remotes = _remote_descriptors(record)
    primary_remote = _selected_remote(record, remotes)
    remote_category: RemoteCategory = "no_remote"
    if primary_remote is not None:
        remote_category = (
            "github_remote" if primary_remote.category == "github" else "non_github_remote"
        )

    sync_status = _sync_status(record)
    reasons: list[str] = []
    if primary_remote is None:
        reasons.append("No remote configured")
    else:
        if primary_remote.category == "github" and primary_remote.github_repo:
            reasons.append(f"GitHub remote: {primary_remote.github_repo}")
        else:
            reasons.append(f"Non-GitHub remote: {primary_remote.url}")
    if len(remotes) > 1:
        reasons.append("Multiple remotes configured")
    if record.git.has_uncommitted_changes:
        reasons.append(
            "Working tree dirty "
            f"({record.git.changed_files} changed, "
            f"{record.git.untracked_files} untracked)"
        )
    if sync_status == "behind" and record.git.behind is not None:
        reasons.append(f"Behind upstream by {record.git.behind} commit(s)")
    if sync_status == "ahead" and record.git.ahead is not None:
        reasons.append(f"Ahead of upstream by {record.git.ahead} commit(s)")
    if sync_status == "diverged":
        reasons.append("Branch has diverged from its upstream")
    if sync_status == "detached":
        reasons.append("Detached HEAD or unusual checkout")
    if sync_status == "no_upstream":
        reasons.append("No upstream tracking branch configured")
    if sync_status == "manual_review":
        reasons.append("Tracking state could not be classified confidently")
    if live_check_status == "succeeded":
        reasons.append("Live remote refresh succeeded")
    if live_check_status == "skipped":
        reasons.append("Live remote refresh skipped because no remote was available to refresh")
    if live_check_status == "capped":
        reasons.append("Live remote refresh skipped because the configured cap was reached")
    if live_check_error:
        reasons.append(f"Live remote refresh failed: {live_check_error}")

    likely_safe_to_update = bool(
        remote_category == "github_remote"
        and sync_status == "behind"
        and not record.git.has_uncommitted_changes
        and len(remotes) == 1
        and live_check_error is None
    )
    needs_manual_review = bool(
        live_check_error
        or remote_category != "github_remote"
        or len(remotes) > 1
        or record.git.has_uncommitted_changes
        or sync_status
        in {"ahead", "diverged", "detached", "no_upstream", "manual_review", "no_remote"}
    )

    return InstalledAuditRepo(
        path=record.path,
        name=record.name,
        git_root=record.git.git_root,
        current_branch=record.git.current_branch,
        upstream_branch=record.git.upstream_branch,
        default_branch=record.git.default_branch,
        remote_category=remote_category,
        primary_remote=primary_remote,
        remotes=remotes,
        sync_status=sync_status,
        ahead=record.git.ahead,
        behind=record.git.behind,
        has_uncommitted_changes=record.git.has_uncommitted_changes,
        changed_files=record.git.changed_files,
        untracked_files=record.git.untracked_files,
        likely_safe_to_update=likely_safe_to_update,
        needs_manual_review=needs_manual_review,
        reasons=reasons,
        suggested_commands=_suggested_commands(
            record,
            remote_category=remote_category,
            sync_status=sync_status,
            likely_safe_to_update=likely_safe_to_update,
        ),
        live_check_status=live_check_status,
        live_check_error=live_check_error,
    )


def refresh_record_for_live_check(record: RepoRecord) -> tuple[RepoRecord, str | None]:
    if record.git is None:
        return record, "git metadata unavailable"
    remote_name = _selected_remote_name(record)
    if remote_name is None:
        return record, "no remote configured"
    repo_path = Path(record.git.git_root or record.path)
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "--quiet", "--no-tags", remote_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=LIVE_REFRESH_TIMEOUT_SECONDS,
            env=_live_refresh_env(),
        )
    except subprocess.TimeoutExpired:
        return record, f"git fetch timed out after {LIVE_REFRESH_TIMEOUT_SECONDS}s"
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "git fetch failed"
        return record, error
    updated = record.model_copy(deep=True)
    updated.git = extract_git_metadata(repo_path)
    return updated, None


def _live_eligible(record: RepoRecord) -> bool:
    return _selected_remote_name(record) is not None


def _preferred_remote_name(record: RepoRecord) -> str | None:
    if record.git is None or not record.git.remotes:
        return None
    if "origin" in record.git.remotes:
        return "origin"
    return sorted(record.git.remotes)[0]


def _selected_remote_name(record: RepoRecord) -> str | None:
    upstream_remote = _upstream_remote_name(record)
    if upstream_remote and record.git and upstream_remote in record.git.remotes:
        return upstream_remote
    return _preferred_remote_name(record)


def _selected_remote(
    record: RepoRecord,
    remotes: list[InstalledAuditRemote],
) -> InstalledAuditRemote | None:
    name = _selected_remote_name(record)
    if name is None:
        return None
    for remote in remotes:
        if remote.name == name:
            return remote
    return None


def _remote_descriptors(record: RepoRecord) -> list[InstalledAuditRemote]:
    if record.git is None:
        return []
    remotes: list[InstalledAuditRemote] = []
    for name, url in sorted(record.git.remotes.items()):
        github_identity = parse_github_remote_url(url)
        if github_identity is not None:
            remotes.append(
                InstalledAuditRemote(
                    name=name,
                    url=url,
                    category="github",
                    host=github_identity.host,
                    github_repo=github_identity.full_name,
                )
            )
            continue
        remotes.append(
            InstalledAuditRemote(
                name=name,
                url=url,
                category="non_github",
                host=_remote_host(url),
            )
        )
    return remotes


def _upstream_remote_name(record: RepoRecord) -> str | None:
    if record.git is None:
        return None
    if record.git.upstream_remote:
        return record.git.upstream_remote
    upstream_branch = record.git.upstream_branch or ""
    if upstream_branch.startswith("refs/remotes/"):
        upstream_branch = upstream_branch[len("refs/remotes/") :]
    if "/" not in upstream_branch:
        return None
    return upstream_branch.split("/", 1)[0]


def _remote_host(url: str) -> str | None:
    if "://" in url:
        parsed = urlparse(url)
        return parsed.hostname
    if "@" in url and ":" in url:
        return url.split("@", 1)[1].split(":", 1)[0]
    return None


def _sync_status(record: RepoRecord) -> SyncStatus:
    if record.git is None or not record.git.remotes:
        return "no_remote"
    if not record.git.current_branch:
        return "detached"
    if not record.git.upstream_branch:
        return "no_upstream"
    if record.git.divergence_status in {"in_sync", "behind", "ahead", "diverged"}:
        return record.git.divergence_status
    return "manual_review"


def _suggested_commands(
    record: RepoRecord,
    *,
    remote_category: RemoteCategory,
    sync_status: SyncStatus,
    likely_safe_to_update: bool,
) -> list[str]:
    if record.git is None:
        return []
    repo_path = shlex.quote(record.git.git_root or record.path)
    commands: list[str] = []

    def add(command: str) -> None:
        if command not in commands:
            commands.append(command)

    add(f"git -C {repo_path} status -sb")
    if record.git.has_uncommitted_changes:
        add(f"git -C {repo_path} diff --stat")

    if sync_status == "behind":
        add(f"git -C {repo_path} log --oneline --decorate HEAD..@{{upstream}}")
        if likely_safe_to_update:
            add(f"git -C {repo_path} pull --ff-only")
    elif sync_status == "ahead":
        add(f"git -C {repo_path} log --oneline --decorate @{{upstream}}..HEAD")
    elif sync_status == "diverged":
        add(f"git -C {repo_path} log --oneline --decorate --left-right HEAD...@{{upstream}}")

    if sync_status in {"detached", "no_upstream", "manual_review", "no_remote"}:
        add(f"git -C {repo_path} branch -vv --all")
    if remote_category != "no_remote" or sync_status in {"no_upstream", "manual_review"}:
        add(f"git -C {repo_path} remote -v")
    return commands


def _live_refresh_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_LIVE_REFRESH_ENV_OVERRIDES)
    return env


def _sort_report_items(
    items: list[InstalledAuditRepo],
    *,
    sort_by_behind: bool = False,
) -> list[InstalledAuditRepo]:
    if sort_by_behind:
        return sorted(
            items,
            key=lambda item: (-(item.behind or 0), (item.name or "").lower(), item.path),
        )
    return sorted(items, key=lambda item: ((item.name or "").lower(), item.path))
