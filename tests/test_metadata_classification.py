from __future__ import annotations

from repo_radar.classification import classify_repo
from repo_radar.metadata import extract_local_metadata
from repo_radar.models import DiscoveredProject

from .conftest import git_commit, git_init


def test_metadata_extracts_git_state_languages_and_counts(tmp_path):
    repo = tmp_path / "app"
    git_init(repo)
    git_commit(repo)
    (repo / "pyproject.toml").write_text("[project]\nname='app'\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "notebooks").mkdir()
    (repo / "notebooks" / "demo.ipynb").write_text("{}", encoding="utf-8")

    record = extract_local_metadata(DiscoveredProject(path=str(repo), source_name="local"))

    assert record.is_git is True
    assert record.git is not None
    assert record.git.current_branch == "main"
    assert record.git.last_commit_date == "2025-01-02T03:04:05+00:00"
    assert record.git.has_uncommitted_changes is True
    assert "Python" in record.primary_languages
    assert "notebooks" in record.key_directories
    assert record.file_count >= 3
    assert record.estimated_size_bytes > 0


def test_classification_scores_common_project_types(tmp_path):
    py_repo = tmp_path / "py"
    py_repo.mkdir()
    for name in ["pyproject.toml", "README.md", "LICENSE"]:
        (py_repo / name).write_text("x", encoding="utf-8")
    (py_repo / "tests").mkdir()
    (py_repo / ".github" / "workflows").mkdir(parents=True)

    node_repo = tmp_path / "node"
    node_repo.mkdir()
    (node_repo / "package.json").write_text("{}", encoding="utf-8")

    rust_repo = tmp_path / "rust"
    rust_repo.mkdir()
    (rust_repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    notebook_repo = tmp_path / "notebook"
    notebook_repo.mkdir()
    (notebook_repo / "analysis.ipynb").write_text("{}", encoding="utf-8")

    py_record = classify_repo(extract_local_metadata(DiscoveredProject(path=str(py_repo))))
    node_record = classify_repo(extract_local_metadata(DiscoveredProject(path=str(node_repo))))
    rust_record = classify_repo(extract_local_metadata(DiscoveredProject(path=str(rust_repo))))
    nb_record = classify_repo(extract_local_metadata(DiscoveredProject(path=str(notebook_repo))))

    assert py_record.project_type == "python"
    assert py_record.maturity_score >= 60
    assert node_record.project_type == "node"
    assert rust_record.project_type == "rust"
    assert nb_record.project_type == "notebooks"
