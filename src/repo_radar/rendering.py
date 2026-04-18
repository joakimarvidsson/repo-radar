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
    action_groups: dict[str, list[RepoRecord]] = defaultdict(list)
    noise_groups: dict[str, list[RepoRecord]] = defaultdict(list)
    monorepo_children: dict[str, list[RepoRecord]] = defaultdict(list)

    for record in records:
        type_groups[record.project_type].append(record)
        name_groups[(record.name or "").lower()].append(record)
        if record.github and record.github.github_repo:
            github_groups[record.github.github_repo.lower()].append(record)
        if record.duplicate_cluster_id:
            duplicate_clusters[record.duplicate_cluster_id].append(record)
        for label in record.recommendation_labels:
            action_groups[label].append(record)
        noise_groups[record.noise_class].append(record)
        if "MONOREPO_SUBPROJECT" in record.relationship_labels and record.monorepo_root_path:
            monorepo_children[record.monorepo_root_path].append(record)

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
            "confidence": max((record.duplicate_confidence for record in group), default=0),
            "canonical_path": next(
                (record.path for record in group if record.likely_canonical),
                None,
            ),
        }
        for cluster_id, group in sorted(duplicate_clusters.items())
    ]
    action_payload = {
        label: {"count": len(group), "paths": sorted(record.path for record in group)}
        for label, group in sorted(action_groups.items())
    }
    canonical = sorted(record.path for record in records if record.likely_canonical)
    orphan_local = sorted(
        record.path for record in records if record.github and record.github.orphan_candidate
    )
    needs_reconciliation = sorted(
        record.path
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    )
    container_paths = sorted(
        record.path for record in records if "CONTAINER_DIRECTORY" in record.relationship_labels
    )
    monorepo_families = [
        {
            "root_path": root,
            "subproject_count": len(children),
            "subprojects": sorted(child.path for child in children),
        }
        for root, children in sorted(monorepo_children.items())
    ]

    payload: dict[str, object] = {
        "schema_version": "1.1",
        "by_project_type": by_type,
        "duplicates": duplicates,
        "heuristic_duplicates": heuristic_duplicates,
        "action_groups": action_payload,
        "canonical_repos": {"count": len(canonical), "paths": canonical},
        "orphan_local_repos": {"count": len(orphan_local), "paths": orphan_local},
        "repos_needing_reconciliation": {
            "count": len(needs_reconciliation),
            "paths": needs_reconciliation,
        },
        "merge_candidates": action_payload.get("MERGE_CANDIDATE", {"count": 0, "paths": []}),
        "by_noise_class": {
            label: {"count": len(group)} for label, group in sorted(noise_groups.items())
        },
        "suppressed_noise": {
            "count": sum(1 for record in records if record.suppressed),
        },
        "monorepo_families": monorepo_families,
        "container_directories": {"count": len(container_paths), "paths": container_paths},
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
    source_summary: dict[str, object] | None = None,
) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in queue if item.selected and not item.suppressed]
    duplicates = groups.get("duplicates", []) if isinstance(groups, dict) else []
    needs_reconciliation = [
        record
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    ]
    stale = _stale_records(records)
    noise_counts = _noise_counts(records)
    lines = [
        "# repo-radar Agent Brief",
        "",
        "## What was found",
        "",
        f"- Repositories and repo-like folders: {len(records)}",
        f"- Selected for first AI inspection: {len(selected)}",
        "",
        *_source_lines(source_summary),
        "",
        "## Inspect first",
        "",
    ]
    if selected:
        for item in selected[:10]:
            lines.append(
                f"- {item.name} ({item.project_type}) at `{item.path}`: score {item.score}; "
                f"{', '.join(item.reasons)}"
                f"{_recommendation_suffix(item.recommendation_labels)}"
            )
    else:
        lines.append("- No repositories were selected within the current token budget.")

    lines.extend(["", "## Likely duplicates", ""])
    if duplicates:
        for duplicate in duplicates[:10]:
            paths = duplicate.get("paths", []) if isinstance(duplicate, dict) else []
            key = duplicate.get("id") or duplicate.get("key") or "unknown"
            reasons = ", ".join(duplicate.get("reasons", []))
            confidence = duplicate.get("confidence")
            confidence_text = f", confidence {confidence}" if confidence else ""
            suffix = f" ({reasons}{confidence_text})" if reasons or confidence else ""
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

    lines.extend(["", "## Merge candidates", ""])
    merge_candidates = _records_with_label(records, "MERGE_CANDIDATE")
    if merge_candidates:
        for record in merge_candidates[:10]:
            reasons = ", ".join(record.recommendation_reasons[:2])
            lines.append(f"- {record.name} at `{record.path}`: {reasons}")
    else:
        lines.append("- No merge candidates were detected.")

    lines.extend(["", "## Monorepo families", ""])
    monorepo_roots = [record for record in records if "MONOREPO_ROOT" in record.relationship_labels]
    if monorepo_roots:
        for record in sorted(monorepo_roots, key=lambda item: item.path)[:10]:
            count = record.monorepo_subproject_count
            lines.append(f"- {record.name} at `{record.path}`: {count} subprojects")
    else:
        lines.append("- No monorepo families were detected.")

    lines.extend(["", "## Suppressed noise summary", ""])
    if noise_counts:
        for label, count in noise_counts.items():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- No suppressed noise records are included in this output.")

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
    source_summary: dict[str, object] | None = None,
) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in queue if item.selected and not item.suppressed]
    duplicates = groups.get("duplicates", []) if isinstance(groups, dict) else []
    needs_reconciliation = [
        record
        for record in records
        if record.github
        and (record.github.orphan_candidate or record.github.remote_matches is False)
    ]
    stale = _stale_records(records)
    noise_counts = _noise_counts(records)

    lines = [
        "# repo-radar Agent Handoff",
        "",
        f"Inventory: {len(records)} repos. Shortlisted: {len(selected)}.",
        "",
        *_source_lines(source_summary),
        "",
        "## Inspect first",
    ]
    if selected:
        for item in selected[:8]:
            lines.append(f"- {item.rank}. `{item.path}` ({item.project_type}) score {item.score}")
            if item.reasons:
                lines.append(f"  Reason: {', '.join(item.reasons[:4])}")
            if item.recommendation_labels:
                lines.append(f"  Action: {', '.join(item.recommendation_labels)}")
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
            confidence = duplicate.get("confidence")
            confidence_text = f" confidence {confidence}" if confidence else ""
            lines.append(f"- {cluster_id}:{confidence_text} {reasons}".rstrip())
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

    lines.extend(["", "## Likely primary / canonical repos"])
    canonical = [record for record in records if record.likely_canonical]
    if canonical:
        for record in canonical[:8]:
            lines.append(f"- `{record.path}` {', '.join(record.recommendation_labels)}")
    else:
        lines.append("- None identified.")

    lines.extend(["", "## Merge candidates"])
    merge_candidates = _records_with_label(records, "MERGE_CANDIDATE")
    if merge_candidates:
        for record in merge_candidates[:8]:
            labels = ", ".join(record.recommendation_labels)
            lines.append(f"- `{record.path}` {labels}")
    else:
        lines.append("- None detected.")

    lines.extend(["", "## Monorepo families"])
    monorepo_roots = [record for record in records if "MONOREPO_ROOT" in record.relationship_labels]
    if monorepo_roots:
        for record in sorted(monorepo_roots, key=lambda item: item.path)[:6]:
            lines.append(f"- `{record.path}` with {record.monorepo_subproject_count} subprojects")
    else:
        lines.append("- None detected.")

    lines.extend(["", "## Suppressed noise summary"])
    if noise_counts:
        for label, count in noise_counts.items():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- None included.")

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
            "- `repo-radar digest`",
            "- `repo-radar shortlist`",
            "- `repo-radar pack`",
            "- `repo-radar brief`",
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
        "| Name | Type | Noise | Git | Maturity | Duplicate | Signals | Path |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for record in records:
        duplicate = record.duplicate_cluster_id or ""
        labels = ", ".join(record.recommendation_labels[:3])
        signals = ", ".join([*record.duplicate_signals[:2], *([labels] if labels else [])])
        lines.append(
            f"| {record.name} | {record.project_type} | {record.noise_class} | "
            f"{'yes' if record.is_git else 'no'} | "
            f"{record.maturity_score} | {duplicate} | {signals} | `{record.path}` |"
        )
    lines.extend(["", "## Action Groups", ""])
    action_groups: dict[str, list[RepoRecord]] = defaultdict(list)
    for record in records:
        for label in record.recommendation_labels:
            action_groups[label].append(record)
    if action_groups:
        for label, group in sorted(action_groups.items()):
            lines.append(f"- {label}: {len(group)}")
    else:
        lines.append("- No recommendation labels have been assigned.")
    canonical = [record for record in records if record.likely_canonical]
    lines.extend(["", "## Likely primary / canonical repos", ""])
    if canonical:
        for record in canonical[:20]:
            lines.append(f"- {record.name}: `{record.path}`")
    else:
        lines.append("- None identified.")
    monorepo_roots = [record for record in records if "MONOREPO_ROOT" in record.relationship_labels]
    lines.extend(["", "## Monorepo families", ""])
    if monorepo_roots:
        for record in sorted(monorepo_roots, key=lambda item: item.path)[:20]:
            lines.append(
                f"- {record.name}: `{record.path}` ({record.monorepo_subproject_count} subprojects)"
            )
    else:
        lines.append("- None detected.")
    container_dirs = [
        record for record in records if "CONTAINER_DIRECTORY" in record.relationship_labels
    ]
    lines.extend(["", "## Container directories", ""])
    if container_dirs:
        for record in sorted(container_dirs, key=lambda item: item.path)[:20]:
            lines.append(f"- {record.name}: `{record.path}`")
    else:
        lines.append("- None detected.")
    return "\n".join(lines) + "\n"


def _source_lines(source_summary: dict[str, object] | None) -> list[str]:
    if not source_summary:
        return []
    lines = [
        "## Scan sources",
        "",
        f"- Source mode: {source_summary.get('mode', 'unknown')}",
    ]
    local_roots = source_summary.get("local_roots") or []
    if isinstance(local_roots, list) and local_roots:
        lines.append("- Local roots:")
        lines.extend(f"  - `{root}`" for root in local_roots[:8])
    ssh_sources = source_summary.get("ssh_sources") or []
    if isinstance(ssh_sources, list) and ssh_sources:
        lines.append("- SSH sources:")
        lines.extend(f"  - `{source}`" for source in ssh_sources[:8])
    warnings = source_summary.get("warnings") or []
    if isinstance(warnings, list) and warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - {warning}" for warning in warnings[:5])
    return lines


def _stale_records(records: list[RepoRecord]) -> list[RepoRecord]:
    return sorted(
        [
            record
            for record in records
            if not record.suppressed
            and (
                record.maturity_score < 20
                or (record.git is not None and record.git.last_commit_date is None)
            )
        ],
        key=lambda record: (record.maturity_score, record.name or "", record.path),
    )


def _records_with_label(records: list[RepoRecord], label: str) -> list[RepoRecord]:
    return sorted(
        [
            record
            for record in records
            if label in record.recommendation_labels and not record.suppressed
        ],
        key=lambda record: (record.name or "", record.path),
    )


def _recommendation_suffix(labels: list[str]) -> str:
    return f"; action {', '.join(labels)}" if labels else ""


def _noise_counts(records: list[RepoRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.suppressed:
            counts[record.noise_class] = counts.get(record.noise_class, 0) + 1
    return dict(sorted(counts.items()))
