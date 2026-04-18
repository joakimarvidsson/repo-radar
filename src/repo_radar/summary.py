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
        ]
    )
