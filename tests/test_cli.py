from __future__ import annotations

from pathlib import Path

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


def test_cli_scan_runs_without_config_file(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(home))

    with CliRunner().isolated_filesystem(temp_dir=project):
        result = CliRunner().invoke(app, ["scan", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "repositories" in result.output


def test_cli_inventory_root_override_saves_runtime_state(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    project = tmp_path / "project"
    outputs = tmp_path / "outputs"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    result = CliRunner().invoke(
        app,
        ["inventory", "--root", str(project), "--outputs-dir", str(outputs)],
    )

    assert result.exit_code == 0, result.output
    assert "source: cli" in result.output
    assert state_path.exists()


def test_cli_doctor_reports_effective_roots_and_current_repo_warning(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(home))

    with CliRunner().isolated_filesystem(temp_dir=project):
        result = CliRunner().invoke(app, ["doctor", "--no-auto"])

    assert result.exit_code != 0
    assert "source: none" in result.output
    assert "No viable local roots" in result.output


def test_cli_doctor_warns_when_only_current_project_is_scanned(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(home))

    with CliRunner().isolated_filesystem(temp_dir=project):
        result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "source: auto" in result.output
    assert "Only the current working directory" in result.output


def test_cli_handoff_runs_without_config(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(home))

    with CliRunner().isolated_filesystem(temp_dir=project):
        result = CliRunner().invoke(app, ["handoff"])
        handoff_exists = Path("outputs/agent_handoff.md").exists()

    assert result.exit_code == 0, result.output
    assert handoff_exists


def test_cli_scan_dry_run_prints_summary_and_effective_excludes(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    home = tmp_path / "home"
    project = home / "project"
    project.mkdir(parents=True)
    (project / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPO_RADAR_STATE_PATH", str(state_path))
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(app, ["scan", "--root", str(home), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "summary:" in result.output
    assert "total discovered:" in result.output
    assert "duplicate clusters:" in result.output
    assert "effective excludes:" in result.output
    assert "Downloads" in result.output
