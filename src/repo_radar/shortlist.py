from __future__ import annotations

import json
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
    score = record.maturity_score
    reasons = list(record.maturity_signals[:4])

    if record.git and record.git.has_uncommitted_changes:
        score += 12
        reasons.append("local-changes")
    if record.git and record.git.divergence_status in {"ahead", "diverged"}:
        score += 10
        reasons.append(record.git.divergence_status)
    if record.github and record.github.orphan_candidate:
        score += 8
        reasons.append("orphan-candidate")
    if record.github and record.github.remote_matches is False:
        score += 8
        reasons.append("github-drift")
    if record.project_type in {"python", "node", "rust", "mixed"}:
        score += 5
        reasons.append(record.project_type)

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
    )


def estimate_tokens(record: RepoRecord) -> int:
    if record.estimated_size_bytes <= 0 and record.file_count <= 0:
        return 1_000
    size_based = max(1_000, record.estimated_size_bytes // 4)
    file_based = record.file_count * 200
    return min(max(size_based, file_based), 1_000_000)
