from __future__ import annotations

from repo_radar.config import PackerSettings
from repo_radar.packers import get_packer_status


def test_get_packer_status_prefers_repomix(monkeypatch):
    def fake_which(name: str):
        return "/usr/local/bin/repomix" if name == "repomix" else None

    monkeypatch.setattr("repo_radar.packers.shutil.which", fake_which)

    status = get_packer_status(PackerSettings())

    assert status.available is True
    assert status.executable == "repomix"
    assert status.command == ["repomix"]


def test_get_packer_status_falls_back_to_npx(monkeypatch):
    def fake_which(name: str):
        return "/usr/local/bin/npx" if name == "npx" else None

    monkeypatch.setattr("repo_radar.packers.shutil.which", fake_which)

    status = get_packer_status(PackerSettings())

    assert status.available is True
    assert status.executable == "npx"
    assert status.command == ["npx", "--yes", "repomix@latest"]


def test_get_packer_status_reports_missing_backend(monkeypatch):
    monkeypatch.setattr("repo_radar.packers.shutil.which", lambda _name: None)

    status = get_packer_status(PackerSettings())

    assert status.available is False
    assert "Install repomix" in status.message
