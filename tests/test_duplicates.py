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
    assert clusters[0].confidence >= 90
    assert clusters[0].canonical_path == "/workspace/one/repo"
    assert clustered[0].likely_canonical is True
    assert clustered[1].likely_canonical is False
    assert clustered[1].duplicate_confidence == clusters[0].confidence


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


def test_duplicate_clustering_detects_suffix_variants_and_readme_titles():
    records = [
        RepoRecord(
            path="/workspace/service",
            name="service",
            project_type="node",
            maturity_score=80,
            classification_confidence=90,
            markers=["package.json", "README.md", "src/"],
            key_directories=["src"],
            manifest_names=["service"],
            readme_title="Service",
            top_level_signature="README.md|package.json|src",
        ),
        RepoRecord(
            path="/workspace/service-old",
            name="service-old",
            project_type="node",
            maturity_score=30,
            classification_confidence=80,
            markers=["package.json", "README.md", "src/"],
            key_directories=["src"],
            manifest_names=["service"],
            readme_title="Service",
            top_level_signature="README.md|package.json|src",
        ),
    ]

    clustered, clusters = assign_duplicate_clusters(records)

    assert len(clusters) == 1
    assert clusters[0].canonical_path == "/workspace/service"
    assert "basename-variant" in clusters[0].reasons
    assert "readme-title" in clusters[0].reasons
    assert clustered[0].likely_canonical is True
    assert clustered[1].likely_canonical is False


def test_duplicate_clustering_skips_related_monorepo_siblings_with_same_remote():
    records = [
        RepoRecord(
            path="/workspace/mono/apps/api",
            name="api",
            git=GitMetadata(
                git_root="/workspace/mono", remotes={"origin": "git@example.com:org/mono.git"}
            ),
            relationship_labels=["MONOREPO_SUBPROJECT"],
            monorepo_root_path="/workspace/mono",
            markers=["package.json", "src/"],
            key_directories=["src"],
        ),
        RepoRecord(
            path="/workspace/mono/apps/web",
            name="web",
            git=GitMetadata(
                git_root="/workspace/mono", remotes={"origin": "git@example.com:org/mono.git"}
            ),
            relationship_labels=["MONOREPO_SUBPROJECT"],
            monorepo_root_path="/workspace/mono",
            markers=["package.json", "src/"],
            key_directories=["src"],
        ),
    ]

    clustered, clusters = assign_duplicate_clusters(records)

    assert clusters == []
    assert all(record.duplicate_cluster_id is None for record in clustered)


def test_duplicate_clustering_does_not_group_monorepo_subproject_on_weak_manifest_similarity():
    records = [
        RepoRecord(
            path="/workspace/mono/apps/api",
            name="api",
            relationship_labels=["MONOREPO_SUBPROJECT"],
            monorepo_root_path="/workspace/mono",
            markers=["package.json"],
            git=GitMetadata(git_root="/workspace/mono"),
        ),
        RepoRecord(
            path="/workspace/other/example",
            name="example",
            markers=["package.json"],
            git=GitMetadata(git_root="/workspace/other"),
        ),
    ]

    clustered, clusters = assign_duplicate_clusters(records)

    assert clusters == []
    assert all(record.duplicate_cluster_id is None for record in clustered)
