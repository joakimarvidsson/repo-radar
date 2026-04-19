from __future__ import annotations

from pathlib import Path


def test_ci_workflow_runs_standard_quality_gate():
    workflow = Path(".github/workflows/ci.yml")

    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")

    assert "uv sync --frozen" in content
    assert "uv run ruff check ." in content
    assert "uv run ruff format . --check" in content
    assert "uv run pytest" in content
    assert "uv run python -m compileall src tests" in content
    assert "uv run repo-radar --help" in content
    assert "uv run repo-radar config-check --config repo_radar.yaml" in content
    assert "uv run repo-radar scan --dry-run" in content
    assert "uv run repo-radar handoff" in content


def test_package_version_is_pep440_alpha():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    init_file = Path("src/repo_radar/__init__.py").read_text(encoding="utf-8")

    assert 'version = "0.1.0a0"' in pyproject
    assert '__version__ = "0.1.0a0"' in init_file
