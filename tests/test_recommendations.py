from __future__ import annotations

from repo_radar.models import GitHubReconciliation, RepoRecord
from repo_radar.recommendations import apply_recommendations


def test_apply_recommendations_labels_canonical_and_duplicate_repos():
    records = [
        RepoRecord(
            path="/workspace/service",
            name="service",
            maturity_score=85,
            key_directories=["src", "tests"],
            duplicate_cluster_id="dup-001",
            duplicate_confidence=95,
            likely_canonical=True,
        ),
        RepoRecord(
            path="/workspace/service-old",
            name="service-old",
            maturity_score=25,
            duplicate_cluster_id="dup-001",
            duplicate_confidence=95,
            duplicate_canonical_path="/workspace/service",
            likely_canonical=False,
        ),
    ]

    recommended = apply_recommendations(records, scores_by_path={"/workspace/service": 120})

    canonical = recommended[0]
    duplicate = recommended[1]
    assert "KEEP" in canonical.recommendation_labels
    assert "DUPLICATE_OF" in duplicate.recommendation_labels
    assert "MERGE_CANDIDATE" in duplicate.recommendation_labels
    assert any("/workspace/service" in reason for reason in duplicate.recommendation_reasons)


def test_apply_recommendations_labels_archive_and_reconciliation_cases():
    records = [
        RepoRecord(path="/workspace/stale", name="stale", maturity_score=5, file_count=1),
        RepoRecord(
            path="/workspace/orphan",
            name="orphan",
            maturity_score=40,
            github=GitHubReconciliation(orphan_candidate=True, exists=False),
        ),
    ]

    recommended = apply_recommendations(records)

    assert "ARCHIVE_CANDIDATE" in recommended[0].recommendation_labels
    assert "NEEDS_RECONCILIATION" in recommended[1].recommendation_labels
    assert "INSPECT" in recommended[1].recommendation_labels
