from __future__ import annotations

from repo_radar.duplicates import assign_duplicate_clusters, normalize_remote_url
from repo_radar.models import GitMetadata, RepoRecord


def test_normalize_remote_url_equates_github_ssh_and_https():
    assert normalize_remote_url("git@github.com:Owner/Repo.git") == (
        normalize_remote_url("https://github.com/owner/repo")
    )


def test_duplicate_clustering_uses_remote_manifest_and_readme_signals():
    records = [
        RepoRecord(
            path="/workspace/one/repo",
            name="repo",
            is_git=True,
            git=GitMetadata(remotes={"origin": "git@github.com:owner/repo.git"}),
            project_type="python",
            key_directories=["src", "tests"],
            markers=["pyproject.toml", "README.md", "src/", "tests/"],
            manifest_names=["repo"],
            readme_hash="abc123",
            top_level_signature="README.md|pyproject.toml|src|tests",
        ),
        RepoRecord(
            path="/workspace/two/repo-copy",
            name="repo-copy",
            is_git=True,
            git=GitMetadata(remotes={"origin": "https://github.com/owner/repo"}),
            project_type="python",
            key_directories=["src", "tests"],
            markers=["pyproject.toml", "README.md", "src/", "tests/"],
            manifest_names=["repo"],
            readme_hash="abc123",
            top_level_signature="README.md|pyproject.toml|src|tests",
        ),
        RepoRecord(
            path="/workspace/other/tool",
            name="tool",
            project_type="node",
            key_directories=["src"],
            markers=["package.json", "src/"],
            manifest_names=["tool"],
        ),
    ]

    clustered, clusters = assign_duplicate_clusters(records)

    cluster_ids = {record.path: record.duplicate_cluster_id for record in clustered}
    assert cluster_ids["/workspace/one/repo"] == cluster_ids["/workspace/two/repo-copy"]
    assert cluster_ids["/workspace/other/tool"] is None
    assert clusters[0].id == "dup-001"
    assert "normalized-remote" in clusters[0].reasons
    assert "manifest-name" in clusters[0].reasons
    assert "readme-hash" in clusters[0].reasons
    assert "normalized-remote" in clustered[0].duplicate_signals


def test_duplicate_clustering_detects_structural_similarity_for_same_basename():
    records = [
        RepoRecord(
            path="/workspace/a/service",
            name="service",
            project_type="python",
            key_directories=["src", "tests", "docs"],
            markers=["pyproject.toml", "README.md", "src/", "tests/", "docs/"],
        ),
        RepoRecord(
            path="/workspace/b/service",
            name="service",
            project_type="python",
            key_directories=["src", "tests"],
            markers=["pyproject.toml", "README.md", "src/", "tests/"],
        ),
    ]

    clustered, clusters = assign_duplicate_clusters(records)

    assert len(clusters) == 1
    assert {record.duplicate_cluster_id for record in clustered} == {"dup-001"}
    assert "basename-structural-similarity" in clusters[0].reasons
