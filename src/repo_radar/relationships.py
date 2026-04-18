from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from repo_radar.models import RepoRecord

CONTAINER_NAMES = {
    "",
    "Projects",
    "projects",
    "Project",
    "project",
    "Documents",
    "My Drive",
    "Google Drive",
    "Colab Notebooks",
    "workspace",
    "workspaces",
    "repos",
    "Repositories",
}


def apply_project_relationships(records: list[RepoRecord]) -> list[RepoRecord]:
    updated = [record.model_copy(deep=True) for record in records]
    _apply_monorepo_relationships(updated)
    _apply_container_relationships(updated)
    for record in updated:
        if not record.relationship_labels:
            record.relationship_labels = ["STANDALONE_PROJECT"]
    return updated


def _apply_monorepo_relationships(records: list[RepoRecord]) -> None:
    by_git_root: dict[str, list[RepoRecord]] = defaultdict(list)
    for record in records:
        if record.git and record.git.git_root:
            by_git_root[record.git.git_root].append(record)

    by_path = {record.path: record for record in records}
    for git_root, group in sorted(by_git_root.items()):
        child_records = [
            record
            for record in group
            if record.path != git_root and _is_direct_project_child(git_root, record.path)
        ]
        if len(child_records) < 2:
            continue
        root_record = by_path.get(git_root)
        if root_record:
            _add_label(root_record, "MONOREPO_ROOT")
            root_record.monorepo_subproject_count = len(child_records)
        for child in child_records:
            _add_label(child, "MONOREPO_SUBPROJECT")
            child.monorepo_root_path = git_root


def _apply_container_relationships(records: list[RepoRecord]) -> None:
    for record in records:
        if "MONOREPO_ROOT" in record.relationship_labels:
            continue
        descendants = [
            other
            for other in records
            if other.path != record.path and _is_descendant(record.path, other.path)
        ]
        if _looks_like_container(record, descendants):
            _add_label(record, "CONTAINER_DIRECTORY")


def _looks_like_container(record: RepoRecord, descendants: list[RepoRecord]) -> bool:
    name = record.name or Path(record.path).name
    if len(descendants) >= 2 and record.maturity_score < 20 and not record.manifest_names:
        return True
    if name in CONTAINER_NAMES and descendants:
        return True
    if name in CONTAINER_NAMES and record.maturity_score < 20:
        return True
    return False


def _is_direct_project_child(git_root: str, path: str) -> bool:
    try:
        rel = Path(path).resolve().relative_to(Path(git_root).resolve())
    except (OSError, ValueError):
        try:
            rel = Path(path).relative_to(Path(git_root))
        except ValueError:
            return False
    if len(rel.parts) == 0:
        return False
    return (
        rel.parts[0] in {"apps", "packages", "services", "examples", "tools", "crates"}
        or len(rel.parts) <= 3
    )


def _is_descendant(parent: str, child: str) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        try:
            Path(child).relative_to(Path(parent))
            return True
        except ValueError:
            return False


def _add_label(record: RepoRecord, label: str) -> None:
    if label not in record.relationship_labels:
        record.relationship_labels.append(label)
