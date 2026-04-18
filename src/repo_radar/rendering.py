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
    duplicate_clusters: dict[str, list[RepoRecord]] = defaultdict(list)

    for record in records:
        type_groups[record.project_type].append(record)
        name_groups[(record.name or "").lower()].append(record)
        if record.github and record.github.github_repo:
            github_groups[record.github.github_repo.lower()].append(record)
        if record.duplicate_cluster_id:
            duplicate_clusters[record.duplicate_cluster_id].append(record)

    for project_type, group in sorted(type_groups.items()):
        by_type[project_type] = {
            "count": len(group),
            "paths": sorted(record.path for record in group),
        }

    heuristic_duplicates = [
        {
            "key": key,
            "paths": sorted(record.path for record in group),
        }
        for key, group in sorted({**name_groups, **github_groups}.items())
        if key and len(group) > 1
    ]
    duplicates = [
        {
            "id": cluster_id,
            "names": sorted({record.name or Path(record.path).name for record in group}),
            "paths": sorted(record.path for record in group),
            "reasons": sorted({signal for record in group for signal in record.duplicate_signals}),
        }
        for cluster_id, group in sorted(duplicate_clusters.items())
    ]

    payload: dict[str, object] = {
        "schema_version": "1.0",
        "by_project_type": by_type,
        "duplicates": duplicates,
        "heuristic_duplicates": heuristic_duplicates,
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
    needs_reconciliation = [
        record
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    ]
    stale = _stale_records(records)
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
            key = duplicate.get("id") or duplicate.get("key") or "unknown"
            reasons = ", ".join(duplicate.get("reasons", []))
            suffix = f" ({reasons})" if reasons else ""
            lines.append(f"- {key}{suffix}: {', '.join(paths)}")
    else:
        lines.append("- No likely duplicates were detected.")

    lines.extend(["", "## GitHub drift and orphan candidates", ""])
    if needs_reconciliation:
        for record in needs_reconciliation[:10]:
            reason = record.github.mismatch_reason if record.github else "unknown"
            lines.append(f"- {record.name} at `{record.path}`: {reason}")
    else:
        lines.append("- No GitHub drift or orphan local repositories were detected.")

    lines.extend(["", "## Likely archive or stale repos", ""])
    if stale:
        for record in stale[:10]:
            lines.append(f"- {record.name} at `{record.path}`: maturity {record.maturity_score}")
    else:
        lines.append("- No obvious archive candidates were detected.")

    lines.extend(["", "## Next AI actions", ""])
    if selected:
        lines.append("- Read compressed digests before requesting a full pack.")
        lines.append("- Use full packs only when source-level inspection is still needed.")
    else:
        lines.append("- Increase the token budget or inspect the inventory before packing.")

    path = outputs_dir / "agent_brief.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_agent_handoff(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem],
    groups: dict[str, object],
    outputs_dir: Path,
) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in queue if item.selected]
    duplicates = groups.get("duplicates", []) if isinstance(groups, dict) else []
    needs_reconciliation = [
        record
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    ]
    stale = _stale_records(records)

    lines = [
        "# repo-radar Agent Handoff",
        "",
        f"Inventory: {len(records)} repos. Shortlisted: {len(selected)}.",
        "",
        "## Inspect first",
    ]
    if selected:
        for item in selected[:8]:
            lines.append(f"- {item.rank}. `{item.path}` ({item.project_type}) score {item.score}")
            if item.reasons:
                lines.append(f"  Reason: {', '.join(item.reasons[:4])}")
    else:
        lines.append("- No repositories selected under the current token budget.")

    lines.extend(["", "## Probable duplicate clusters"])
    if duplicates:
        for duplicate in duplicates[:6]:
            if not isinstance(duplicate, dict):
                continue
            cluster_id = duplicate.get("id") or duplicate.get("key", "duplicate")
            reasons = ", ".join(duplicate.get("reasons", []))
            paths = duplicate.get("paths", [])
            lines.append(f"- {cluster_id}: {reasons}")
            for path in paths[:4]:
                lines.append(f"  - `{path}`")
    else:
        lines.append("- None detected.")

    lines.extend(["", "## Likely archive or stale repos"])
    if stale:
        for record in stale[:8]:
            lines.append(f"- `{record.path}` maturity {record.maturity_score}")
    else:
        lines.append("- None obvious.")

    lines.extend(["", "## Needs reconciliation"])
    if needs_reconciliation:
        for record in needs_reconciliation[:8]:
            reason = record.github.mismatch_reason if record.github else "unknown"
            lines.append(f"- `{record.path}`: {reason}")
    else:
        lines.append("- No obvious GitHub reconciliation blockers.")

    lines.extend(
        [
            "",
            "## Recommended next commands",
            "- `repo-radar digest --config repo_radar.yaml`",
            "- `repo-radar shortlist --config repo_radar.yaml`",
            "- `repo-radar pack --config repo_radar.yaml`",
            "- `repo-radar brief --config repo_radar.yaml`",
        ]
    )

    path = outputs_dir / "agent_handoff.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _inventory_markdown(records: list[RepoRecord]) -> str:
    lines = [
        "# repo-radar Inventory",
        "",
        f"{len(records)} repositories and repo-like folders found.",
        "",
        "| Name | Type | Git | Maturity | Duplicate | Signals | Path |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for record in records:
        duplicate = record.duplicate_cluster_id or ""
        signals = ", ".join(record.duplicate_signals[:3])
        lines.append(
            f"| {record.name} | {record.project_type} | {'yes' if record.is_git else 'no'} | "
            f"{record.maturity_score} | {duplicate} | {signals} | `{record.path}` |"
        )
    return "\n".join(lines) + "\n"


def _stale_records(records: list[RepoRecord]) -> list[RepoRecord]:
    return sorted(
        [
            record
            for record in records
            if record.maturity_score < 20
            or (record.git is not None and record.git.last_commit_date is None)
        ],
        key=lambda record: (record.maturity_score, record.name or "", record.path),
    )
