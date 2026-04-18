from __future__ import annotations

from pathlib import Path

from repo_radar.config import LocalSourceConfig
from repo_radar.discovery.base import (
    detect_markers,
    matches_patterns,
    posix_relative,
    should_skip_dir,
)
from repo_radar.models import DiscoveredProject


class LocalFilesystemAdapter:
    def __init__(
        self, ignore_patterns: list[str] | None = None, include_patterns: list[str] | None = None
    ):
        self.ignore_patterns = ignore_patterns or []
        self.include_patterns = include_patterns or []

    def discover(self, source: LocalSourceConfig) -> list[DiscoveredProject]:
        if not source.enabled:
            return []

        discovered: dict[str, DiscoveredProject] = {}
        max_depth = source.max_depth if source.max_depth is not None else 5
        for root in source.roots:
            root_path = root.expanduser().resolve()
            if not root_path.exists() or not root_path.is_dir():
                continue
            for path in self._walk_dirs(root_path, max_depth):
                rel = posix_relative(path, root_path)
                if (
                    rel != "."
                    and self.include_patterns
                    and not matches_patterns(rel, self.include_patterns)
                ):
                    continue
                markers = detect_markers(path)
                if not markers:
                    continue
                is_git = ".git/" in markers
                is_repo_like = is_git or bool(set(markers) - {"README.md"})
                if not is_repo_like:
                    continue
                discovered[str(path)] = DiscoveredProject(
                    path=str(path),
                    name=path.name,
                    source_type="local",
                    source_name=source.name,
                    is_git=is_git,
                    is_repo_like=is_repo_like,
                    markers=markers,
                )
        return sorted(discovered.values(), key=lambda item: item.path)

    def _walk_dirs(self, root: Path, max_depth: int) -> list[Path]:
        result: list[Path] = []
        stack = [root]
        while stack:
            current = stack.pop()
            depth = 0 if current == root else len(current.relative_to(root).parts)
            if depth > max_depth:
                continue
            result.append(current)
            if depth == max_depth:
                continue
            try:
                children = sorted(child for child in current.iterdir() if child.is_dir())
            except OSError:
                continue
            for child in reversed(children):
                if should_skip_dir(child, root, self.ignore_patterns):
                    continue
                stack.append(child)
        return result
