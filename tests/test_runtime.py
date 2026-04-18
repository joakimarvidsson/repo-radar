from __future__ import annotations

from repo_radar.runtime import (
    RuntimeState,
    discover_auto_roots,
    load_runtime_state,
    parse_ssh_target,
    resolve_runtime_context,
    save_runtime_state,
)


def test_discover_auto_roots_prefers_cwd_and_common_existing_dirs(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "workspace" / "repo"
    projects = home / "projects"
    code = home / "Code"
    documents = home / "Documents"
    for path in [cwd, projects, code, documents]:
        path.mkdir(parents=True)

    roots = discover_auto_roots(cwd=cwd, home=home, max_roots=10)

    assert roots[0] == cwd.resolve()
    assert projects.resolve() in roots
    assert code.resolve() in roots
    assert documents.resolve() in roots
    assert home.resolve() not in roots
    assert len(roots) == len(set(roots))


def test_discover_auto_roots_scans_current_project_without_parent_fanout(tmp_path):
    home = tmp_path / "home"
    cwd = home / "projects" / "repo"
    projects = home / "projects"
    cwd.mkdir(parents=True)
    (cwd / "pyproject.toml").write_text("[project]\nname='repo'\n", encoding="utf-8")

    roots = discover_auto_roots(cwd=cwd, home=home, max_roots=10)

    assert roots == [cwd.resolve()]
    assert projects.resolve() not in roots


def test_runtime_state_round_trips_last_used_settings(tmp_path):
    state_path = tmp_path / "state.json"
    root = tmp_path / "repo"
    root.mkdir()
    state = RuntimeState(
        local_roots=[root],
        ssh_targets=["user@example.invalid"],
        ssh_roots=["/srv/projects"],
    )

    save_runtime_state(state, state_path)
    loaded = load_runtime_state(state_path)

    assert loaded.local_roots == [root]
    assert loaded.ssh_targets == ["user@example.invalid"]
    assert loaded.ssh_roots == ["/srv/projects"]


def test_resolve_runtime_context_precedence_cli_config_state_auto(tmp_path):
    auto_root = tmp_path / "auto"
    saved_root = tmp_path / "saved"
    config_root = tmp_path / "config"
    cli_root = tmp_path / "cli"
    for path in [auto_root, saved_root, config_root, cli_root]:
        path.mkdir()

    state_path = tmp_path / "state.json"
    save_runtime_state(RuntimeState(local_roots=[saved_root]), state_path)
    config_path = tmp_path / "repo_radar.yaml"
    config_path.write_text(
        f"local_roots:\n  - {config_root.as_posix()}\ngithub:\n  enabled: false\n",
        encoding="utf-8",
    )

    cli_context = resolve_runtime_context(
        config_path=config_path,
        outputs_dir=tmp_path / "out",
        cli_roots=[cli_root],
        auto=None,
        state_path=state_path,
        cwd=auto_root,
        home=tmp_path,
    )
    config_context = resolve_runtime_context(
        config_path=config_path,
        outputs_dir=tmp_path / "out",
        state_path=state_path,
        cwd=auto_root,
        home=tmp_path,
    )
    state_context = resolve_runtime_context(
        config_path=None,
        outputs_dir=tmp_path / "out",
        state_path=state_path,
        cwd=auto_root,
        home=tmp_path,
    )
    auto_context = resolve_runtime_context(
        config_path=None,
        outputs_dir=tmp_path / "out",
        auto=True,
        state_path=state_path,
        cwd=auto_root,
        home=tmp_path,
    )

    assert cli_context.source_mode == "cli"
    assert cli_context.local_roots == [cli_root.resolve()]
    assert config_context.source_mode == "config"
    assert config_context.local_roots == [config_root]
    assert state_context.source_mode == "state"
    assert state_context.local_roots == [saved_root]
    assert auto_context.source_mode == "auto"
    assert auto_root.resolve() in auto_context.local_roots


def test_parse_ssh_target_accepts_user_host_and_host_only():
    user_source = parse_ssh_target("deploy@example.invalid", roots=["/srv/projects"])
    host_source = parse_ssh_target("example.invalid", roots=["/srv/repos"])

    assert user_source.name == "example.invalid"
    assert user_source.user == "deploy"
    assert user_source.host == "example.invalid"
    assert user_source.roots == ["/srv/projects"]
    assert host_source.user is None
    assert host_source.host == "example.invalid"


def test_broad_home_root_warns_and_surfaces_effective_excludes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    context = resolve_runtime_context(
        config_path=None,
        outputs_dir=tmp_path / "out",
        cli_roots=[home],
        state_path=tmp_path / "state.json",
        cwd=home / "repo",
        home=home,
    )

    summary = context.source_summary()
    assert context.local_roots == [home.resolve()]
    assert any("Broad root requested" in warning for warning in context.warnings)
    assert ".git" in summary["effective_excludes"]
    assert "Library" in summary["effective_excludes"]
    assert "Downloads" in summary["effective_excludes"]
    assert ".codex" in summary["effective_excludes"]
    assert ".cursor" in summary["effective_excludes"]
    assert ".bun/install/cache" in summary["noise_suppression_rules"]
    assert summary["include_noise"] is False
    assert context.config.max_depth == 3
