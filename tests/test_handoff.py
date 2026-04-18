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
        ),
        RepoRecord(
            path="/workspace/b",
            name="b",
            project_type="python",
            duplicate_cluster_id="dup-001",
            duplicate_signals=["normalized-remote"],
        ),
        RepoRecord(
            path="/workspace/stale",
            name="stale",
            project_type="repo-like",
            maturity_score=5,
            github=GitHubReconciliation(orphan_candidate=True, exists=False),
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
    assert "Recommended next commands" in content
    assert len(content.splitlines()) < 80


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
