from __future__ import annotations

from repo_radar.config import LocalSourceConfig
from repo_radar.discovery.local import LocalFilesystemAdapter
from repo_radar.discovery.ssh import SSHSourceAdapter


def test_local_discovery_finds_git_and_repo_like_folders(tmp_path):
    git_repo = tmp_path / "git-repo"
    git_repo.mkdir()
    (git_repo / ".git").mkdir()

    python_app = tmp_path / "python-app"
    python_app.mkdir()
    (python_app / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    nested = tmp_path / "parent" / "child-rust"
    nested.mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")

    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "package.json").write_text("{}", encoding="utf-8")

    source = LocalSourceConfig(name="local", roots=[tmp_path], max_depth=4)
    adapter = LocalFilesystemAdapter(ignore_patterns=["ignored/**"])

    discovered = adapter.discover(source)
    paths = {item.path for item in discovered}

    assert str(git_repo) in paths
    assert str(python_app) in paths
    assert str(nested) in paths
    assert str(ignored) not in paths
    assert len(paths) == len(discovered)


def test_ssh_adapter_builds_safe_read_only_find_command():
    source = {
        "name": "remote",
        "host": "example.invalid",
        "user": "deploy",
        "port": 2222,
        "roots": ["/srv/projects"],
        "max_depth": 3,
        "enabled": True,
    }

    adapter = SSHSourceAdapter(ignore_patterns=["node_modules/**"])
    command = adapter.build_discovery_command(adapter.source_model(source), "/srv/projects")

    assert command[:4] == ["ssh", "-p", "2222", "deploy@example.invalid"]
    assert "find" in command[-1]
    assert "-maxdepth 3" in command[-1]
    assert "pyproject.toml" in command[-1]
    assert "package.json" in command[-1]


def test_ssh_adapter_parses_remote_discovery_rows():
    adapter = SSHSourceAdapter()
    rows = adapter.parse_rows("git|/srv/a|4096\nrepo_like|/srv/b|2048\nbad row\n")

    assert [row.path for row in rows] == ["/srv/a", "/srv/b"]
    assert rows[0].is_git is True
    assert rows[1].is_repo_like is True
    assert rows[1].estimated_size_bytes == 2048
