from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repo_radar.models import RepoRecord


def classify_repo(record: RepoRecord) -> RepoRecord:
    updated = record.model_copy(deep=True)
    markers = set(updated.markers)
    path = Path(updated.path)

    ecosystems = []
    if {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"} & markers:
        ecosystems.append("python")
    if {"package.json", "pnpm-lock.yaml", "yarn.lock"} & markers:
        ecosystems.append("node")
    if "Cargo.toml" in markers:
        ecosystems.append("rust")
    if "go.mod" in markers:
        ecosystems.append("go")

    has_notebooks = "*.ipynb" in markers or "notebooks/" in markers or "notebook/" in markers
    if len(ecosystems) > 1:
        updated.project_type = "mixed"
    elif ecosystems:
        updated.project_type = ecosystems[0]
    elif has_notebooks:
        updated.project_type = "notebooks"
    elif "terraform/" in markers or any(
        (path / name).exists() for name in ["main.tf", "Dockerfile"]
    ):
        updated.project_type = "infra"
    elif "docs/" in markers and updated.file_count <= 20:
        updated.project_type = "docs"
    elif updated.is_git:
        updated.project_type = "git-only"
    else:
        updated.project_type = "repo-like"

    updated.maturity_score, updated.maturity_signals = _maturity(path, updated)
    updated.classification_confidence = _classification_confidence(updated)
    return updated


def _maturity(path: Path, record: RepoRecord) -> tuple[int, list[str]]:
    score = 0
    signals: list[str] = []

    def add(points: int, label: str) -> None:
        nonlocal score
        score += points
        signals.append(label)

    if any((path / name).is_file() for name in ["README.md", "README.rst", "README.txt"]):
        add(15, "readme")
    if any((path / name).is_file() for name in ["LICENSE", "LICENSE.md", "COPYING"]):
        add(10, "license")
    if any((path / name).is_dir() for name in ["tests", "test"]) or any(path.glob("test_*.py")):
        add(15, "tests")
    if (path / ".github" / "workflows").is_dir() or any(path.glob(".gitlab-ci.*")):
        add(10, "ci")
    if set(record.markers) & {
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
    }:
        add(15, "manifest")
    if "docs/" in record.markers:
        add(5, "docs")
    if record.git and record.git.remotes:
        add(5, "remote")
    if record.git and not record.git.has_uncommitted_changes:
        add(10, "clean-worktree")
    if _recent_commit(record):
        add(10, "recent-commit")
    if len(record.key_directories) >= 2:
        add(5, "structured")

    return min(score, 100), signals


def _recent_commit(record: RepoRecord) -> bool:
    if not record.git or not record.git.last_commit_date:
        return False
    try:
        date = datetime.fromisoformat(record.git.last_commit_date.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - date).days <= 540


def _classification_confidence(record: RepoRecord) -> int:
    score = 20
    if record.project_type not in {"unknown", "repo-like"}:
        score += 30
    if record.markers:
        score += min(25, len(record.markers) * 5)
    if record.primary_languages:
        score += 10
    if record.key_directories:
        score += min(15, len(record.key_directories) * 5)
    return min(score, 100)
