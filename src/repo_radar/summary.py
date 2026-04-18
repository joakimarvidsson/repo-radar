from __future__ import annotations

from pydantic import BaseModel

from repo_radar.models import PriorityQueueItem, RepoRecord


class ScanSummary(BaseModel):
    total: int = 0
    shortlisted: int = 0
    duplicate_clusters: int = 0
    archive_candidates: int = 0
    orphan_repos: int = 0
    reconciliation_issues: int = 0
    merge_candidates: int = 0
    suppressed_noise: int = 0
    user_projects: int = 0
    monorepo_roots: int = 0
    monorepo_subprojects: int = 0
    container_directories: int = 0


def build_scan_summary(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem] | None = None,
) -> ScanSummary:
    queue = queue or []
    duplicate_clusters = {
        record.duplicate_cluster_id for record in records if record.duplicate_cluster_id
    }
    return ScanSummary(
        total=len(records),
        shortlisted=sum(1 for item in queue if item.selected),
        duplicate_clusters=len(duplicate_clusters),
        archive_candidates=sum(
            1 for record in records if "ARCHIVE_CANDIDATE" in record.recommendation_labels
        ),
        orphan_repos=sum(
            1 for record in records if record.github and record.github.orphan_candidate
        ),
        reconciliation_issues=sum(
            1
            for record in records
            if record.github
            and (record.github.orphan_candidate or record.github.remote_matches is False)
        ),
        merge_candidates=sum(
            1 for record in records if "MERGE_CANDIDATE" in record.recommendation_labels
        ),
        suppressed_noise=sum(1 for record in records if record.suppressed),
        user_projects=sum(1 for record in records if record.noise_class == "USER_PROJECT"),
        monorepo_roots=sum(
            1 for record in records if "MONOREPO_ROOT" in record.relationship_labels
        ),
        monorepo_subprojects=sum(
            1 for record in records if "MONOREPO_SUBPROJECT" in record.relationship_labels
        ),
        container_directories=sum(
            1 for record in records if "CONTAINER_DIRECTORY" in record.relationship_labels
        ),
    )


def format_scan_summary(summary: ScanSummary) -> str:
    return "\n".join(
        [
            "summary:",
            f"- total discovered: {summary.total}",
            f"- shortlisted: {summary.shortlisted}",
            f"- duplicate clusters: {summary.duplicate_clusters}",
            f"- archive candidates: {summary.archive_candidates}",
            f"- orphan repos: {summary.orphan_repos}",
            f"- reconciliation issues: {summary.reconciliation_issues}",
            f"- merge candidates: {summary.merge_candidates}",
            f"- suppressed noise: {summary.suppressed_noise}",
            f"- likely user projects: {summary.user_projects}",
            f"- monorepo roots: {summary.monorepo_roots}",
            f"- monorepo subprojects: {summary.monorepo_subprojects}",
            f"- container directories: {summary.container_directories}",
        ]
    )
