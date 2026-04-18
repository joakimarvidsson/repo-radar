from __future__ import annotations

from pathlib import Path


def test_ci_workflow_runs_standard_quality_gate():
    workflow = Path(".github/workflows/ci.yml")

    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")

    assert "uv sync" in content
    assert "uv run ruff check ." in content
    assert "uv run pytest" in content
    assert "uv run python -m compileall src tests" in content
