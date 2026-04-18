from __future__ import annotations

from repo_radar.duplicates import assign_duplicate_clusters
from repo_radar.models import GitMetadata, RepoRecord
from repo_radar.recommendations import apply_recommendations
from repo_radar.relationships import apply_project_relationships


def test_monorepo_root_and_subprojects_are_labeled_without_duplicate_cluster():
    records = [
        RepoRecord(
            path="/workspace/mono",
            name="mono",
            git=GitMetadata(
                git_root="/workspace/mono", remotes={"origin": "git@example.com:org/mono.git"}
            ),
            is_git=True,
            markers=["package.json"],
            manifest_names=["mono"],
            maturity_score=75,
        ),
        RepoRecord(
            path="/workspace/mono/apps/api",
            name="api",
            git=GitMetadata(
                git_root="/workspace/mono", remotes={"origin": "git@example.com:org/mono.git"}
            ),
            is_git=True,
            markers=["package.json", "src/"],
            manifest_names=["api"],
            key_directories=["src"],
            maturity_score=45,
        ),
        RepoRecord(
            path="/workspace/mono/apps/web",
            name="web",
            git=GitMetadata(
                git_root="/workspace/mono", remotes={"origin": "git@example.com:org/mono.git"}
            ),
            is_git=True,
            markers=["package.json", "src/"],
            manifest_names=["web"],
            key_directories=["src"],
            maturity_score=45,
        ),
    ]

    related = apply_project_relationships(records)
    clustered, clusters = assign_duplicate_clusters(related)
    recommended = apply_recommendations(clustered)

    by_path = {record.path: record for record in recommended}
    assert "MONOREPO_ROOT" in by_path["/workspace/mono"].relationship_labels
    assert "MONOREPO_SUBPROJECT" in by_path["/workspace/mono/apps/api"].relationship_labels
    assert by_path["/workspace/mono/apps/api"].monorepo_root_path == "/workspace/mono"
    assert clusters == []
    assert "MERGE_CANDIDATE" not in by_path["/workspace/mono/apps/api"].recommendation_labels
    assert "DUPLICATE_OF" not in by_path["/workspace/mono/apps/api"].recommendation_labels


def test_container_directories_are_labeled_and_deprioritized():
    records = [
        RepoRecord(path="/workspace/Projects", name="Projects", maturity_score=0),
        RepoRecord(path="/workspace/Projects/app-one", name="app-one", maturity_score=50),
        RepoRecord(path="/workspace/Projects/app-two", name="app-two", maturity_score=50),
    ]

    related = apply_project_relationships(records)
    recommended = apply_recommendations(related)
    container = next(record for record in recommended if record.path == "/workspace/Projects")

    assert "CONTAINER_DIRECTORY" in container.relationship_labels
    assert "CONTAINER_DIRECTORY" in container.recommendation_labels
    assert "INSPECT" not in container.recommendation_labels
