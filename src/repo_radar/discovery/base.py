from __future__ import annotations

import fnmatch
from pathlib import Path

PROJECT_MARKER_FILES = {
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "Makefile",
    "README.md",
}

PROJECT_MARKER_DIRS = {
    ".git",
    "src",
    "notebooks",
    "notebook",
    "tests",
    "docs",
    ".github",
    "terraform",
}

DEFAULT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".cache",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "target",
    ".tox",
    "Library",
    "Downloads",
    "Movies",
    "Music",
    "Pictures",
    "Applications",
    "Trash",
    ".Trash",
    "outputs",
}

BROAD_NOISE_SKIP_DIRS = {
    ".antigravity",
    ".bun",
    ".cargo",
    ".claude",
    ".clawdbot",
    ".codex",
    ".config",
    ".continue",
    ".cursor",
    ".gemini",
    ".gemini-backups",
    ".hermes",
    ".local",
    ".npm",
    ".openclaw",
    ".opencode",
    ".pnpm-store",
    ".rustup",
    ".vscode",
    ".cache",
    "Library",
    "Downloads",
    "Movies",
    "Music",
    "Pictures",
    "Applications",
    "Trash",
    ".Trash",
    "outputs",
}

BROAD_NOISE_SKIP_PATTERNS = [
    ".cursor/extensions/**",
    ".vscode/extensions/**",
    ".antigravity/extensions/**",
    "go/pkg/mod/**",
    "plugins/marketplaces/**",
    "plugin/marketplaces/**",
    "**/.ipynb_checkpoints/**",
    "**/.virtual_documents/**",
]


def posix_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def matches_patterns(value: str, patterns: list[str]) -> bool:
    normalized = value.strip("/")
    for pattern in patterns:
        clean = pattern.strip("/")
        if fnmatch.fnmatch(normalized, clean):
            return True
        if clean.endswith("/**") and (
            normalized == clean[:-3] or normalized.startswith(clean[:-2])
        ):
            return True
        if "/" not in clean and fnmatch.fnmatch(Path(normalized).name, clean):
            return True
    return False


def should_skip_dir(
    path: Path,
    root: Path,
    ignore_patterns: list[str],
    broad_scan: bool = False,
    include_noise: bool = False,
) -> bool:
    if path.name in DEFAULT_SKIP_DIRS:
        return True
    rel = posix_relative(path, root)
    if broad_scan and not include_noise:
        if path.parent == root and path.name.startswith("."):
            return True
        if path.name in BROAD_NOISE_SKIP_DIRS:
            return True
        if matches_patterns(rel, BROAD_NOISE_SKIP_PATTERNS):
            return True
    return matches_patterns(rel, ignore_patterns)


def detect_markers(path: Path) -> list[str]:
    markers: list[str] = []
    for marker in sorted(PROJECT_MARKER_FILES):
        if (path / marker).is_file():
            markers.append(marker)
    for marker in sorted(PROJECT_MARKER_DIRS):
        if (path / marker).is_dir():
            markers.append(marker + "/")
    if any(child.suffix == ".ipynb" for child in path.glob("*.ipynb")):
        markers.append("*.ipynb")
    return markers


def is_repo_like_path(path: Path) -> bool:
    markers = detect_markers(path)
    return bool(markers and markers != ["README.md"])
