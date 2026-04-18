from __future__ import annotations

from repo_radar.config import validate_config


def test_validate_config_reports_missing_roots(tmp_path):
    missing = tmp_path / "missing"
    config_path = tmp_path / "repo_radar.yaml"
    config_path.write_text(f"local_roots:\n  - {missing.as_posix()}\n", encoding="utf-8")

    report = validate_config(config_path)

    assert not report.ok
    assert any("does not exist" in issue for issue in report.errors)


def test_validate_config_accepts_existing_root(tmp_path):
    config_path = tmp_path / "repo_radar.yaml"
    config_path.write_text(f"local_roots:\n  - {tmp_path.as_posix()}\n", encoding="utf-8")

    report = validate_config(config_path)

    assert report.ok
    assert report.errors == []
    assert str(tmp_path) in report.local_roots
