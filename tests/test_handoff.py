from __future__ import annotations

from typer.testing import CliRunner

from repo_radar.cli import app
from repo_radar.models import GitHubReconciliation, PriorityQueueItem, RepoRecord
from repo_radar.rendering import render_agent_handoff


def test_render_agent_handoff_is_compact_and_action_oriented(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/a",
            name="a",
            project_type="python",
            duplicate_cluster_id="dup-001",
            duplicate_signals=["normalized-remote"],
            likely_canonical=True,
            recommendation_labels=["KEEP"],
        ),
        RepoRecord(
            path="/workspace/b",
            name="b",
            project_type="python",
            duplicate_cluster_id="dup-001",
            duplicate_signals=["normalized-remote"],
            recommendation_labels=["DUPLICATE_OF", "MERGE_CANDIDATE"],
        ),
        RepoRecord(
            path="/workspace/stale",
            name="stale",
            project_type="repo-like",
            maturity_score=5,
            github=GitHubReconciliation(orphan_candidate=True, exists=False),
            recommendation_labels=["ARCHIVE_CANDIDATE", "NEEDS_RECONCILIATION"],
        ),
    ]
    queue = [
        PriorityQueueItem(
            rank=1,
            name="a",
            path="/workspace/a",
            project_type="python",
            score=90,
            selected=True,
            reasons=["strong structure", "recent activity"],
        )
    ]
    groups = {
        "duplicates": [
            {
                "id": "dup-001",
                "reasons": ["normalized-remote"],
                "paths": ["/workspace/a", "/workspace/b"],
            }
        ]
    }

    path = render_agent_handoff(records, queue, groups, tmp_path)
    content = path.read_text(encoding="utf-8")

    assert path.name == "agent_handoff.md"
    assert "Inspect first" in content
    assert "dup-001" in content
    assert "Needs reconciliation" in content
    assert "Merge candidates" in content
    assert "Likely primary / canonical repos" in content
    assert "KEEP" in content
    assert "Recommended next commands" in content
    assert len(content.splitlines()) < 80


def test_render_agent_handoff_uses_zero_config_next_commands(tmp_path):
    path = render_agent_handoff(
        records=[RepoRecord(path="/workspace/app", name="app")],
        queue=[],
        groups={"duplicates": []},
        outputs_dir=tmp_path,
    )
    content = path.read_text(encoding="utf-8")

    assert "repo-radar digest --config" not in content
    assert "`repo-radar digest`" in content
    assert "`repo-radar shortlist`" in content
    assert "`repo-radar pack`" in content
    assert "`repo-radar brief`" in content


def test_render_agent_handoff_mentions_effective_sources(tmp_path):
    path = render_agent_handoff(
        records=[RepoRecord(path="/workspace/a", name="a")],
        queue=[],
        groups={"duplicates": []},
        outputs_dir=tmp_path,
        source_summary={
            "mode": "auto",
            "local_roots": ["/workspace"],
            "ssh_sources": ["deploy@example.invalid:/srv/projects"],
            "warnings": [],
        },
    )
    content = path.read_text(encoding="utf-8")

    assert "Source mode: auto" in content
    assert "`/workspace`" in content
    assert "deploy@example.invalid:/srv/projects" in content


def test_render_agent_handoff_summarizes_noise_without_flooding(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/app",
            name="app",
            project_type="python",
            recommendation_labels=["INSPECT"],
        ),
        RepoRecord(
            path="/home/user/.bun/install/cache/pkg",
            name="pkg",
            project_type="node",
            noise_class="CACHE_OR_PACKAGE_STORE",
            suppressed=True,
            recommendation_labels=["SUPPRESSED_NOISE"],
        ),
    ]
    queue = [
        PriorityQueueItem(
            rank=1,
            name="app",
            path="/workspace/app",
            project_type="python",
            score=90,
            selected=True,
            recommendation_labels=["INSPECT"],
        ),
        PriorityQueueItem(
            rank=2,
            name="pkg",
            path="/home/user/.bun/install/cache/pkg",
            project_type="node",
            score=-100,
            selected=False,
            suppressed=True,
            noise_class="CACHE_OR_PACKAGE_STORE",
            recommendation_labels=["SUPPRESSED_NOISE"],
        ),
    ]

    path = render_agent_handoff(records, queue, {"duplicates": []}, tmp_path)
    content = path.read_text(encoding="utf-8")

    assert "Suppressed noise summary" in content
    assert "CACHE_OR_PACKAGE_STORE: 1" in content
    assert "/home/user/.bun/install/cache/pkg" not in content


def test_cli_handoff_writes_agent_handoff_from_existing_outputs(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "repo_inventory.json").write_text(
        """
{
  "schema_version": "1.0",
  "repository_count": 1,
  "repositories": [
    {"path": "/workspace/a", "name": "a", "project_type": "python", "maturity_score": 80}
  ]
}
""",
        encoding="utf-8",
    )
    (outputs / "repo_priority_queue.json").write_text(
        """
{
  "schema_version": "1.0",
  "items": [
    {
      "rank": 1,
      "name": "a",
      "path": "/workspace/a",
      "project_type": "python",
      "score": 80,
      "selected": true,
      "reasons": ["readme"]
    }
  ]
}
""",
        encoding="utf-8",
    )
    config = tmp_path / "repo_radar.yaml"
    config.write_text("local_roots:\n  - .\ngithub:\n  enabled: false\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["handoff", "--config", str(config), "--outputs-dir", str(outputs)],
    )

    assert result.exit_code == 0, result.output
    assert (outputs / "agent_handoff.md").exists()
