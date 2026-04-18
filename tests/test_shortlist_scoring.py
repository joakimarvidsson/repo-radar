from __future__ import annotations

import json

from repo_radar.models import GitHubReconciliation, GitMetadata, RepoRecord
from repo_radar.shortlist import build_priority_queue, render_priority_queue


def test_shortlist_persists_explainable_score_breakdown(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/primary",
            name="primary",
            project_type="python",
            maturity_score=65,
            maturity_signals=["readme", "tests", "manifest", "recent-commit"],
            classification_confidence=90,
            file_count=20,
            estimated_size_bytes=20_000,
            key_directories=["src", "tests", "docs"],
            git=GitMetadata(
                remotes={"origin": "git@github.com:owner/primary.git"},
                last_commit_date="2026-01-01T00:00:00+00:00",
                has_uncommitted_changes=False,
            ),
        ),
        RepoRecord(
            path="/workspace/duplicate",
            name="duplicate",
            project_type="python",
            maturity_score=80,
            maturity_signals=["readme", "tests", "manifest"],
            classification_confidence=80,
            file_count=20,
            estimated_size_bytes=20_000,
            key_directories=["src", "tests"],
            duplicate_cluster_id="dup-001",
            duplicate_signals=["normalized-remote"],
            github=GitHubReconciliation(orphan_candidate=True, exists=False),
        ),
    ]

    queue = build_priority_queue(records, token_budget=100_000, max_repos=5)
    render_priority_queue(queue, tmp_path)
    payload = json.loads((tmp_path / "repo_priority_queue.json").read_text(encoding="utf-8"))

    first = payload["items"][0]
    duplicate = next(item for item in payload["items"] if item["name"] == "duplicate")
    assert first["score_breakdown"]["positive"]
    assert duplicate["score_breakdown"]["negative"]["duplicate_penalty"] < 0
    assert duplicate["score_breakdown"]["negative"]["orphan_remote_penalty"] < 0
    assert "duplicate cluster dup-001" in " ".join(duplicate["reasons"])
    assert duplicate["classification_confidence"] == 80


def test_shortlist_does_not_select_suppressed_noise():
    records = [
        RepoRecord(
            path="/workspace/app",
            name="app",
            project_type="python",
            maturity_score=60,
            file_count=10,
            estimated_size_bytes=10_000,
        ),
        RepoRecord(
            path="/home/user/.bun/install/cache/pkg",
            name="pkg",
            project_type="node",
            maturity_score=100,
            file_count=100,
            estimated_size_bytes=100_000,
            noise_class="CACHE_OR_PACKAGE_STORE",
            suppressed=True,
            noise_reasons=["package cache path"],
        ),
    ]

    queue = build_priority_queue(records, token_budget=1_000_000, max_repos=5)
    by_name = {item.name: item for item in queue}

    assert by_name["app"].selected is True
    assert by_name["pkg"].selected is False
    assert by_name["pkg"].suppressed is True
    assert by_name["pkg"].score_breakdown["negative"]["noise_suppression_penalty"] < 0
