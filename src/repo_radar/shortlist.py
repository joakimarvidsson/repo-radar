from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from repo_radar.models import PriorityQueueItem, RepoRecord


def build_priority_queue(
    records: list[RepoRecord],
    token_budget: int,
    max_repos: int,
) -> list[PriorityQueueItem]:
    ranked = [_score_record(record) for record in records]
    ranked.sort(key=lambda item: (-item.score, item.name.lower(), item.path))

    spent = 0
    selected = 0
    queue: list[PriorityQueueItem] = []
    for index, item in enumerate(ranked, start=1):
        can_select = (
            selected < max_repos
            and item.estimated_tokens > 0
            and spent + item.estimated_tokens <= token_budget
            and not item.suppressed
            and "CONTAINER_DIRECTORY" not in item.relationship_labels
        )
        item.rank = index
        item.selected = can_select
        if can_select:
            spent += item.estimated_tokens
            selected += 1
        queue.append(item)
    return queue


def render_priority_queue(queue: list[PriorityQueueItem], outputs_dir: Path) -> Path:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    path = outputs_dir / "repo_priority_queue.json"
    payload = {
        "schema_version": "1.0",
        "selected_count": sum(1 for item in queue if item.selected),
        "estimated_selected_tokens": sum(item.estimated_tokens for item in queue if item.selected),
        "items": [item.model_dump(mode="json") for item in queue],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _score_record(record: RepoRecord) -> PriorityQueueItem:
    positive: dict[str, int] = {}
    negative: dict[str, int] = {}

    _add(positive, "maturity", record.maturity_score)
    _add(positive, "classification_confidence", record.classification_confidence // 5)
    if _has_recent_activity(record):
        _add(positive, "recent_activity", 10)
    if record.git and not record.git.has_uncommitted_changes:
        _add(positive, "clean_git_state", 8)
    if record.git and record.git.has_uncommitted_changes:
        _add(positive, "local_change_attention", 6)
    if "src" in record.key_directories:
        _add(positive, "source_structure", 8)
    if "tests" in record.key_directories or "test" in record.key_directories:
        _add(positive, "test_coverage_signal", 8)
    if "docs" in record.key_directories or "readme" in record.maturity_signals:
        _add(positive, "docs_or_readme", 6)
    if record.project_type in {"python", "node", "rust", "mixed"}:
        _add(positive, "project_type_relevance", 6)
    if Path(record.path).exists() or record.source_type == "ssh":
        _add(positive, "packability", 5)
    if record.git and record.git.divergence_status in {"ahead", "diverged"}:
        _add(positive, "needs_git_attention", 6)

    if record.duplicate_cluster_id:
        _add(negative, "duplicate_penalty", -25)
    if record.github and record.github.orphan_candidate:
        _add(negative, "orphan_remote_penalty", -8)
    if record.github and record.github.remote_matches is False:
        _add(negative, "github_drift_penalty", -8)
    if _is_stale(record):
        _add(negative, "stale_penalty", -12)
    if record.maturity_score < 20:
        _add(negative, "incomplete_penalty", -8)
    if record.suppressed:
        _add(negative, "noise_suppression_penalty", -250)
    if "CONTAINER_DIRECTORY" in record.relationship_labels:
        _add(negative, "container_directory_penalty", -150)
    if "MONOREPO_SUBPROJECT" in record.relationship_labels:
        _add(positive, "monorepo_subproject", 4)

    score = sum(positive.values()) + sum(negative.values())
    reasons = _top_reasons(positive, negative, record)

    estimated_tokens = estimate_tokens(record)
    if not reasons:
        reasons.append("repo-like")

    return PriorityQueueItem(
        rank=0,
        name=record.name or Path(record.path).name,
        path=record.path,
        project_type=record.project_type,
        score=score,
        estimated_tokens=estimated_tokens,
        reasons=reasons,
        score_breakdown={"positive": positive, "negative": negative},
        classification_confidence=record.classification_confidence,
        relationship_labels=record.relationship_labels,
        noise_class=record.noise_class,
        suppressed=record.suppressed,
    )


def estimate_tokens(record: RepoRecord) -> int:
    if record.estimated_size_bytes <= 0 and record.file_count <= 0:
        return 1_000
    size_based = max(1_000, record.estimated_size_bytes // 4)
    file_based = record.file_count * 200
    return min(max(size_based, file_based), 1_000_000)


def _add(bucket: dict[str, int], key: str, value: int) -> None:
    if value:
        bucket[key] = value


def _top_reasons(
    positive: dict[str, int],
    negative: dict[str, int],
    record: RepoRecord,
) -> list[str]:
    reasons = [
        f"+{points} {label.replace('_', ' ')}"
        for label, points in sorted(positive.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]
    reasons.extend(
        f"{points} {label.replace('_', ' ')}"
        for label, points in sorted(negative.items(), key=lambda item: (item[1], item[0]))[:3]
    )
    if record.duplicate_cluster_id:
        reasons.append(f"duplicate cluster {record.duplicate_cluster_id}")
    return reasons


def _has_recent_activity(record: RepoRecord) -> bool:
    if not record.git or not record.git.last_commit_date:
        return False
    commit_date = _parse_date(record.git.last_commit_date)
    return commit_date is not None and (datetime.now(UTC) - commit_date).days <= 180


def _is_stale(record: RepoRecord) -> bool:
    if not record.git or not record.git.last_commit_date:
        return record.maturity_score < 20 and record.file_count <= 3
    commit_date = _parse_date(record.git.last_commit_date)
    return commit_date is not None and (datetime.now(UTC) - commit_date).days > 730


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
