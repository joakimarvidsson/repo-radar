from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repo_radar.models import PriorityQueueItem, RepoRecord


def apply_recommendations(
    records: list[RepoRecord],
    scores_by_path: dict[str, int] | None = None,
) -> list[RepoRecord]:
    scores_by_path = scores_by_path or {}
    return [_recommend(record, scores_by_path.get(record.path)) for record in records]


def annotate_queue_with_recommendations(
    queue: list[PriorityQueueItem],
    records: list[RepoRecord],
) -> list[PriorityQueueItem]:
    by_path = {record.path: record for record in records}
    annotated: list[PriorityQueueItem] = []
    for item in queue:
        record = by_path.get(item.path)
        if record is None:
            annotated.append(item)
            continue
        annotated.append(
            item.model_copy(
                update={
                    "recommendation_labels": record.recommendation_labels,
                    "recommendation_reasons": record.recommendation_reasons,
                }
            )
        )
    return annotated


def _recommend(record: RepoRecord, score: int | None) -> RepoRecord:
    updated = record.model_copy(deep=True)
    labels: list[str] = []
    reasons: list[str] = []

    def add(label: str, reason: str) -> None:
        if label not in labels:
            labels.append(label)
        if reason not in reasons:
            reasons.append(reason)

    if updated.duplicate_cluster_id:
        if updated.likely_canonical:
            add("KEEP", f"Likely canonical for {updated.duplicate_cluster_id}")
        else:
            canonical = updated.duplicate_canonical_path or "cluster canonical"
            add("DUPLICATE_OF", f"Likely duplicate of {canonical}")
            add(
                "MERGE_CANDIDATE",
                f"Review changes before archiving duplicate cluster {updated.duplicate_cluster_id}",
            )

    if updated.github and (
        updated.github.orphan_candidate or updated.github.remote_matches is False
    ):
        add("NEEDS_RECONCILIATION", updated.github.mismatch_reason or "GitHub state needs review")
        add("INSPECT", "Reconciliation issue needs human or agent review")

    if _is_archive_candidate(updated):
        add("ARCHIVE_CANDIDATE", "Low maturity, stale, or sparse project signals")

    if _is_packable(updated):
        if (score or 0) >= 80 or updated.maturity_score >= 60 or updated.key_directories:
            add("INSPECT", "Good candidate for AI inspection")

    if updated.maturity_score >= 70 and not _has_label(labels, "DUPLICATE_OF"):
        add("KEEP", "Mature project signals")

    if not labels:
        add("INSPECT", "Repo-like folder needs initial review")

    updated.recommendation_labels = labels
    updated.recommendation_reasons = reasons
    return updated


def _has_label(labels: list[str], label: str) -> bool:
    return label in labels


def _is_archive_candidate(record: RepoRecord) -> bool:
    if record.maturity_score < 15 and record.file_count <= 3:
        return True
    if record.git and record.git.last_commit_date:
        parsed = _parse_date(record.git.last_commit_date)
        if parsed and (datetime.now(UTC) - parsed).days > 1095 and record.maturity_score < 45:
            return True
    return False


def _is_packable(record: RepoRecord) -> bool:
    return record.source_type == "ssh" or Path(record.path).exists()


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
