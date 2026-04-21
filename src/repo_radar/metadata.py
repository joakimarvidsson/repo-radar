from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

from repo_radar.discovery.base import DEFAULT_SKIP_DIRS, detect_markers, matches_patterns
from repo_radar.models import DiscoveredProject, GitMetadata, RepoRecord

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".sh": "Shell",
    ".md": "Markdown",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".tf": "Terraform",
    ".ipynb": "Jupyter Notebook",
}

KEY_DIRECTORIES = ["src", "tests", "test", "docs", "notebooks", ".github", "infra", "terraform"]


def extract_local_metadata(
    project: DiscoveredProject,
    ignore_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    max_file_depth: int | None = None,
) -> RepoRecord:
    ignore_patterns = ignore_patterns or []
    include_patterns = include_patterns or []
    path = Path(project.path)
    markers = detect_markers(path)
    is_git = (path / ".git").exists() or _git_is_repo(path)
    file_count, size_bytes, languages = _file_stats(
        path,
        ignore_patterns,
        include_patterns,
        max_depth=max_file_depth,
    )
    key_dirs = [name for name in KEY_DIRECTORIES if (path / name).is_dir()]
    manifest_names = _manifest_names(path)

    return RepoRecord(
        path=str(path),
        name=project.name or path.name,
        source_type=project.source_type,
        source_name=project.source_name,
        is_git=is_git,
        is_repo_like=project.is_repo_like or bool(markers),
        markers=markers or project.markers,
        git=extract_git_metadata(path) if is_git else None,
        language_file_counts=dict(languages),
        primary_languages=[name for name, _count in languages.most_common(5)],
        file_count=file_count,
        estimated_size_bytes=size_bytes or project.estimated_size_bytes,
        key_directories=key_dirs,
        manifest_names=manifest_names,
        readme_hash=_readme_hash(path),
        readme_title=_readme_title(path),
        top_level_signature=_top_level_signature(path),
    )


def extract_git_metadata(path: Path) -> GitMetadata:
    remotes = _git_remotes(path)
    status_lines = _git(path, ["status", "--porcelain=v1"]).splitlines()
    untracked = sum(1 for line in status_lines if line.startswith("??"))
    changed = len(status_lines) - untracked
    ahead, behind = _ahead_behind(path)

    last_commit_date = _git(path, ["log", "-1", "--format=%cI"]) or None
    if last_commit_date and last_commit_date.endswith("Z"):
        last_commit_date = last_commit_date[:-1] + "+00:00"

    return GitMetadata(
        remotes=remotes,
        git_root=_git(path, ["rev-parse", "--show-toplevel"]) or None,
        current_branch=_git(path, ["branch", "--show-current"]) or None,
        upstream_branch=_git(
            path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
        )
        or None,
        default_branch=_default_branch(path),
        last_commit_date=last_commit_date,
        ahead=ahead,
        behind=behind,
        divergence_status=_divergence_status(ahead, behind),
        has_uncommitted_changes=bool(status_lines),
        changed_files=changed,
        untracked_files=untracked,
    )


def _git_is_repo(path: Path) -> bool:
    return _git(path, ["rev-parse", "--is-inside-work-tree"]) == "true"


def _git(path: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_remotes(path: Path) -> dict[str, str]:
    output = _git(path, ["remote", "-v"])
    remotes: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)":
            remotes[parts[0]] = parts[1]
    return remotes


def _default_branch(path: Path) -> str | None:
    origin_head = _git(path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if origin_head.startswith("origin/"):
        return origin_head.split("/", 1)[1]
    return None


def _ahead_behind(path: Path) -> tuple[int | None, int | None]:
    output = _git(path, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    if not output:
        return None, None
    parts = output.split()
    if len(parts) != 2:
        return None, None
    return int(parts[0]), int(parts[1])


def _divergence_status(ahead: int | None, behind: int | None) -> str | None:
    if ahead is None or behind is None:
        return None
    if ahead == 0 and behind == 0:
        return "in_sync"
    if ahead > 0 and behind > 0:
        return "diverged"
    if ahead > 0:
        return "ahead"
    return "behind"


def _file_stats(
    path: Path,
    ignore_patterns: list[str],
    include_patterns: list[str],
    max_depth: int | None = None,
) -> tuple[int, int, Counter[str]]:
    file_count = 0
    size_bytes = 0
    languages: Counter[str] = Counter()
    stack = [path]

    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda child: child.name)
        except OSError:
            continue
        for child in children:
            rel = child.relative_to(path).as_posix()
            if child.is_dir():
                if max_depth is not None and len(child.relative_to(path).parts) > max_depth:
                    continue
                if child.name in DEFAULT_SKIP_DIRS or matches_patterns(rel, ignore_patterns):
                    continue
                stack.append(child)
                continue
            if matches_patterns(rel, ignore_patterns):
                continue
            if any(part in DEFAULT_SKIP_DIRS for part in child.relative_to(path).parts):
                continue
            if include_patterns and not matches_patterns(rel, include_patterns):
                continue
            file_count += 1
            try:
                size_bytes += child.stat().st_size
            except OSError:
                pass
            language = LANGUAGE_BY_EXTENSION.get(child.suffix.lower())
            if language:
                languages[language] += 1
    return file_count, size_bytes, languages


def _manifest_names(path: Path) -> list[str]:
    names: set[str] = set()
    pyproject = path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project_name = data.get("project", {}).get("name")
            poetry_name = data.get("tool", {}).get("poetry", {}).get("name")
            for name in [project_name, poetry_name]:
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())
        except (OSError, tomllib.TOMLDecodeError):
            pass

    package_json = path / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            package_name = data.get("name")
            if isinstance(package_name, str) and package_name.strip():
                names.add(package_name.strip())
        except (OSError, json.JSONDecodeError):
            pass

    cargo = path / "Cargo.toml"
    if cargo.is_file():
        try:
            data = tomllib.loads(cargo.read_text(encoding="utf-8"))
            package_name = data.get("package", {}).get("name")
            if isinstance(package_name, str) and package_name.strip():
                names.add(package_name.strip())
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return sorted(names)


def _readme_hash(path: Path) -> str | None:
    for name in ["README.md", "README.rst", "README.txt"]:
        readme = path / name
        if readme.is_file():
            try:
                content = readme.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
            normalized = " ".join(content.lower().split())
            if not normalized:
                return None
            return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return None


def _readme_title(path: Path) -> str | None:
    for name in ["README.md", "README.rst", "README.txt"]:
        readme = path / name
        if not readme.is_file():
            continue
        try:
            lines = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        for line in lines[:20]:
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return " ".join(stripped.lower().split())
    return None


def _top_level_signature(path: Path) -> str | None:
    try:
        names = [
            child.name
            for child in path.iterdir()
            if child.name not in DEFAULT_SKIP_DIRS and not child.name.startswith(".repo-radar")
        ]
    except OSError:
        return None
    if not names:
        return None
    return "|".join(sorted(names))
