from __future__ import annotations

from repo_radar.models import GitHubReconciliation, PriorityQueueItem, RepoRecord
from repo_radar.summary import build_scan_summary, format_scan_summary


def test_build_scan_summary_counts_actionable_groups():
    records = [
        RepoRecord(
            path="/workspace/a",
            name="a",
            duplicate_cluster_id="dup-001",
            recommendation_labels=["KEEP"],
            likely_canonical=True,
        ),
        RepoRecord(
            path="/workspace/b",
            name="b",
            duplicate_cluster_id="dup-001",
            recommendation_labels=["DUPLICATE_OF", "MERGE_CANDIDATE"],
        ),
        RepoRecord(
            path="/workspace/stale",
            name="stale",
            recommendation_labels=["ARCHIVE_CANDIDATE"],
        ),
        RepoRecord(
            path="/workspace/orphan",
            name="orphan",
            github=GitHubReconciliation(orphan_candidate=True, exists=False),
            recommendation_labels=["NEEDS_RECONCILIATION"],
        ),
    ]
    queue = [
        PriorityQueueItem(
            rank=1,
            name="a",
            path="/workspace/a",
            score=100,
            selected=True,
        )
    ]

    summary = build_scan_summary(records, queue)
    text = format_scan_summary(summary)

    assert summary.total == 4
    assert summary.shortlisted == 1
    assert summary.duplicate_clusters == 1
    assert summary.archive_candidates == 1
    assert summary.orphan_repos == 1
    assert summary.reconciliation_issues == 1
    assert "total discovered: 4" in text
    assert "duplicate clusters: 1" in text
