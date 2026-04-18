from __future__ import annotations

from typer.testing import CliRunner

from repo_radar.cli import app


def test_cli_inventory_smoke(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")
    config = tmp_path / "repo_radar.yaml"
    outputs = tmp_path / "outputs"
    config.write_text(
        f"""
local_roots:
  - {project.as_posix()}
ssh_sources: []
ignore_patterns: []
max_depth: 2
github:
  enabled: false
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["inventory", "--config", str(config), "--outputs-dir", str(outputs)]
    )

    assert result.exit_code == 0, result.output
    assert (outputs / "repo_inventory.json").exists()
    assert "1 repositories" in result.output


def test_cli_scan_dry_run_does_not_write_outputs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    config = tmp_path / "repo_radar.yaml"
    outputs = tmp_path / "outputs"
    config.write_text(
        f"local_roots:\n  - {project.as_posix()}\ngithub:\n  enabled: false\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["scan", "--config", str(config), "--outputs-dir", str(outputs), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert not (outputs / "repo_inventory.json").exists()


def test_cli_config_check_reports_valid_config(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = tmp_path / "repo_radar.yaml"
    config.write_text(
        f"local_roots:\n  - {project.as_posix()}\ngithub:\n  enabled: false\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["config-check", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "Config OK" in result.output
    assert str(project) in result.output


def test_cli_config_check_fails_for_missing_local_root(tmp_path):
    missing = tmp_path / "missing"
    config = tmp_path / "repo_radar.yaml"
    config.write_text(
        f"local_roots:\n  - {missing.as_posix()}\ngithub:\n  enabled: false\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["config-check", "--config", str(config)])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_cli_doctor_reports_packer_status(monkeypatch, tmp_path):
    config = tmp_path / "repo_radar.yaml"
    config.write_text("local_roots:\n  - .\ngithub:\n  enabled: false\n", encoding="utf-8")

    def fake_which(name: str):
        if name == "repomix":
            return "/usr/local/bin/repomix"
        return None

    monkeypatch.setattr("repo_radar.packers.shutil.which", fake_which)

    result = CliRunner().invoke(app, ["doctor", "--config", str(config)])

    assert result.exit_code == 0, result.output
    assert "repomix" in result.output
    assert "available" in result.output
