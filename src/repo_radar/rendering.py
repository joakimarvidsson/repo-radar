from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from repo_radar.models import PriorityQueueItem, RepoRecord


def render_inventory(records: list[RepoRecord], outputs_dir: Path) -> tuple[Path, Path]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    sorted_records = sorted(records, key=lambda record: ((record.name or "").lower(), record.path))
    payload = {
        "schema_version": "1.0",
        "repository_count": len(sorted_records),
        "repositories": [record.model_dump(mode="json") for record in sorted_records],
    }
    json_path = outputs_dir / "repo_inventory.json"
    md_path = outputs_dir / "repo_inventory.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_inventory_markdown(sorted_records), encoding="utf-8")
    return json_path, md_path


def render_groups(records: list[RepoRecord], outputs_dir: Path) -> dict[str, object]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    by_type: dict[str, dict[str, object]] = {}
    type_groups: dict[str, list[RepoRecord]] = defaultdict(list)
    name_groups: dict[str, list[RepoRecord]] = defaultdict(list)
    github_groups: dict[str, list[RepoRecord]] = defaultdict(list)

    for record in records:
        type_groups[record.project_type].append(record)
        name_groups[(record.name or "").lower()].append(record)
        if record.github and record.github.github_repo:
            github_groups[record.github.github_repo.lower()].append(record)

    for project_type, group in sorted(type_groups.items()):
        by_type[project_type] = {
            "count": len(group),
            "paths": sorted(record.path for record in group),
        }

    duplicates = [
        {
            "key": key,
            "paths": sorted(record.path for record in group),
        }
        for key, group in sorted({**name_groups, **github_groups}.items())
        if key and len(group) > 1
    ]

    payload: dict[str, object] = {
        "schema_version": "1.0",
        "by_project_type": by_type,
        "duplicates": duplicates,
    }
    (outputs_dir / "repo_groups.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def render_agent_brief(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem],
    groups: dict[str, object],
    outputs_dir: Path,
) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in queue if item.selected]
    duplicates = groups.get("duplicates", []) if isinstance(groups, dict) else []
    drift = [
        record
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    ]
    lines = [
        "# repo-radar Agent Brief",
        "",
        "## What was found",
        "",
        f"- Repositories and repo-like folders: {len(records)}",
        f"- Selected for first AI inspection: {len(selected)}",
        "",
        "## Inspect first",
        "",
    ]
    if selected:
        for item in selected[:10]:
            lines.append(
                f"- {item.name} ({item.project_type}) at `{item.path}`: score {item.score}; "
                f"{', '.join(item.reasons)}"
            )
    else:
        lines.append("- No repositories were selected within the current token budget.")

    lines.extend(["", "## Likely duplicates", ""])
    if duplicates:
        for duplicate in duplicates[:10]:
            paths = duplicate.get("paths", []) if isinstance(duplicate, dict) else []
            lines.append(f"- {duplicate.get('key', 'unknown')}: {', '.join(paths)}")
    else:
        lines.append("- No likely duplicates were detected.")

    lines.extend(["", "## GitHub drift and orphan candidates", ""])
    if drift:
        for record in drift[:10]:
            reason = record.github.mismatch_reason if record.github else "unknown"
            lines.append(f"- {record.name} at `{record.path}`: {reason}")
    else:
        lines.append("- No GitHub drift or orphan local repositories were detected.")

    lines.extend(["", "## Next AI actions", ""])
    if selected:
        lines.append("- Read compressed digests before requesting a full pack.")
        lines.append("- Use full packs only when source-level inspection is still needed.")
    else:
        lines.append("- Increase the token budget or inspect the inventory before packing.")

    path = outputs_dir / "agent_brief.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _inventory_markdown(records: list[RepoRecord]) -> str:
    lines = [
        "# repo-radar Inventory",
        "",
        f"{len(records)} repositories and repo-like folders found.",
        "",
        "| Name | Type | Git | Maturity | Files | Path |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for record in records:
        lines.append(
            f"| {record.name} | {record.project_type} | {'yes' if record.is_git else 'no'} | "
            f"{record.maturity_score} | {record.file_count} | `{record.path}` |"
        )
    return "\n".join(lines) + "\n"
