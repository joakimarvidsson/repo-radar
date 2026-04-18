from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repo_radar.config import RadarConfig, SSHSourceConfig, load_config
from repo_radar.discovery.base import (
    BROAD_NOISE_SKIP_DIRS,
    BROAD_NOISE_SKIP_PATTERNS,
    DEFAULT_SKIP_DIRS,
)
from repo_radar.noise import NOISE_SUPPRESSION_RULES

COMMON_DEVELOPER_DIRS = [
    "projects",
    "Projects",
    "code",
    "Code",
    "github",
    "GitHub",
    "Documents",
]
DEFAULT_MAX_AUTO_ROOTS = 8


class RuntimeState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_roots: list[Path] = Field(default_factory=list)
    ssh_targets: list[str] = Field(default_factory=list)
    ssh_roots: list[str] = Field(default_factory=list)
    updated_at: str | None = None

    @field_validator("local_roots", mode="before")
    @classmethod
    def normalize_roots(cls, value: object) -> list[Path]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [Path(value)]
        return [Path(item) for item in value]  # type: ignore[arg-type]


class RuntimeContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: RadarConfig
    source_mode: Literal["cli", "config", "state", "auto", "none"]
    local_roots: list[Path] = Field(default_factory=list)
    ssh_targets: list[str] = Field(default_factory=list)
    ssh_roots: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    state_path: Path
    config_path: Path | None = None

    def source_summary(self) -> dict[str, object]:
        return {
            "mode": self.source_mode,
            "local_roots": [str(root) for root in self.local_roots],
            "ssh_sources": [
                f"{source.target}:{root}"
                for source in self.config.ssh_sources
                if source.enabled
                for root in source.roots
            ],
            "warnings": self.warnings,
            "effective_excludes": effective_excludes(self.config),
            "noise_suppression_rules": NOISE_SUPPRESSION_RULES,
            "include_noise": self.config.include_noise,
        }

    def to_state(self) -> RuntimeState:
        return RuntimeState(
            local_roots=self.local_roots,
            ssh_targets=self.ssh_targets,
            ssh_roots=self.ssh_roots,
            updated_at=datetime.now(UTC).isoformat(),
        )


def state_path_from_env() -> Path:
    override = os.environ.get("REPO_RADAR_STATE_PATH")
    if override:
        return Path(override).expanduser()
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "repo-radar" / "state.json"
    return Path.home() / ".local" / "state" / "repo-radar" / "state.json"


def load_runtime_state(path: Path | None = None) -> RuntimeState | None:
    state_path = path or state_path_from_env()
    if not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return RuntimeState.model_validate(raw)


def save_runtime_state(state: RuntimeState, path: Path | None = None) -> Path:
    state_path = path or state_path_from_env()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_copy(
        update={"updated_at": state.updated_at or datetime.now(UTC).isoformat()}
    )
    state_path.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return state_path


def discover_auto_roots(
    cwd: Path | None = None,
    home: Path | None = None,
    max_roots: int = DEFAULT_MAX_AUTO_ROOTS,
) -> list[Path]:
    current = (cwd or Path.cwd()).expanduser()
    user_home = (home or Path.home()).expanduser()
    if _looks_like_project_root(current):
        candidates = [current]
    else:
        candidates = [current, *(user_home / name for name in COMMON_DEVELOPER_DIRS)]

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_dir():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
        if len(roots) >= max_roots:
            break
    return roots


def parse_ssh_target(target: str, roots: list[str]) -> SSHSourceConfig:
    clean = target.strip()
    if "@" in clean:
        user, host = clean.split("@", 1)
    else:
        user, host = None, clean
    return SSHSourceConfig(
        name=host,
        host=host,
        user=user,
        roots=roots,
        enabled=True,
    )


def resolve_runtime_context(
    config_path: Path | None = None,
    outputs_dir: Path = Path("outputs"),
    cli_roots: list[Path] | None = None,
    auto: bool | None = None,
    ssh_targets: list[str] | None = None,
    ssh_roots: list[str] | None = None,
    include_noise: bool = False,
    state_path: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    max_auto_roots: int = DEFAULT_MAX_AUTO_ROOTS,
) -> RuntimeContext:
    state_file = state_path or state_path_from_env()
    state = load_runtime_state(state_file)
    warnings: list[str] = []
    cli_root_list = _existing_roots(cli_roots or [], warnings, source="CLI root")
    ssh_root_list = ssh_roots or []

    if config_path is not None:
        config = load_config(config_path)
        config.include_noise = include_noise or config.include_noise
        source_mode: Literal["cli", "config", "state", "auto", "none"] = "config"
        if cli_root_list:
            config.local_sources = []
            config.local_roots = cli_root_list
            source_mode = "cli"
        local_roots = _effective_config_roots(config)
        if ssh_targets:
            config.ssh_sources = [
                parse_ssh_target(target, ssh_root_list or ["."]) for target in ssh_targets
            ]
        elif ssh_root_list and config.ssh_sources:
            config.ssh_sources = [
                source.model_copy(update={"roots": ssh_root_list, "enabled": True})
                for source in config.ssh_sources
            ]
        config.outputs_dir = outputs_dir
        config.broad_scan = any(_is_broad_root(root, home or Path.home()) for root in local_roots)
        if config.broad_scan and not config.include_noise:
            config.max_depth = min(config.max_depth, 3)
        context = RuntimeContext(
            config=config,
            source_mode=source_mode,
            local_roots=local_roots,
            ssh_targets=_effective_ssh_targets(config),
            ssh_roots=_effective_ssh_roots(config),
            warnings=warnings,
            state_path=state_file,
            config_path=config_path,
        )
        return _with_root_warnings(context, cwd or Path.cwd(), home or Path.home())

    config = RadarConfig(outputs_dir=outputs_dir)
    source_mode: Literal["cli", "config", "state", "auto", "none"]
    if cli_root_list:
        local_roots = cli_root_list
        source_mode = "cli"
    elif state and state.local_roots and auto is not True:
        local_roots = _existing_roots(state.local_roots, warnings, source="saved root")
        source_mode = "state" if local_roots else "none"
    elif auto is False:
        local_roots = []
        source_mode = "none"
    else:
        local_roots = discover_auto_roots(cwd=cwd, home=home, max_roots=max_auto_roots)
        source_mode = "auto" if local_roots else "none"

    config.local_sources = []
    config.local_roots = local_roots
    config.include_noise = include_noise
    config.broad_scan = any(_is_broad_root(root, home or Path.home()) for root in local_roots)
    if config.broad_scan and not config.include_noise:
        config.max_depth = min(config.max_depth, 3)

    effective_ssh_targets = ssh_targets or (
        [] if auto is True else (state.ssh_targets if state else [])
    )
    effective_ssh_roots = ssh_root_list or (
        [] if auto is True else (state.ssh_roots if state else [])
    )
    if effective_ssh_targets:
        config.ssh_sources = [
            parse_ssh_target(target, effective_ssh_roots or ["."])
            for target in effective_ssh_targets
        ]

    context = RuntimeContext(
        config=config,
        source_mode=source_mode,
        local_roots=local_roots,
        ssh_targets=effective_ssh_targets,
        ssh_roots=effective_ssh_roots,
        warnings=warnings,
        state_path=state_file,
        config_path=None,
    )
    return _with_root_warnings(context, cwd or Path.cwd(), home or Path.home())


def persist_runtime_context(context: RuntimeContext) -> Path:
    return save_runtime_state(context.to_state(), context.state_path)


def _existing_roots(paths: list[Path], warnings: list[str], source: str) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        expanded = Path(path).expanduser()
        if not expanded.exists() or not expanded.is_dir():
            warnings.append(f"{source} does not exist or is not a directory: {expanded}")
            continue
        try:
            resolved = expanded.resolve()
        except OSError:
            resolved = expanded
        key = os.path.normcase(str(resolved))
        if key not in seen:
            roots.append(resolved)
            seen.add(key)
    return roots


def _effective_config_roots(config: RadarConfig) -> list[Path]:
    roots: list[Path] = []
    for source in config.iter_local_sources():
        if source.enabled:
            roots.extend(source.roots)
    return roots


def _effective_ssh_targets(config: RadarConfig) -> list[str]:
    return [source.target for source in config.ssh_sources if source.enabled]


def _effective_ssh_roots(config: RadarConfig) -> list[str]:
    roots: list[str] = []
    for source in config.ssh_sources:
        if source.enabled:
            roots.extend(source.roots)
    return roots


def _with_root_warnings(context: RuntimeContext, cwd: Path, home: Path) -> RuntimeContext:
    warnings = list(context.warnings)
    if not context.local_roots and not context.ssh_targets:
        warnings.append("No viable local roots or SSH sources were found.")
    elif _only_current_directory(context.local_roots, cwd):
        warnings.append("Only the current working directory will be scanned.")
    for root in context.local_roots:
        if _is_broad_root(root, home):
            warnings.append(
                f"Broad root requested: {root}. Default exclusions will skip heavy directories."
            )
    return context.model_copy(update={"warnings": warnings})


def _only_current_directory(roots: list[Path], cwd: Path) -> bool:
    if len(roots) != 1:
        return False
    try:
        return roots[0].resolve() == cwd.expanduser().resolve()
    except OSError:
        return roots[0] == cwd


def _looks_like_project_root(path: Path) -> bool:
    markers = [
        ".git",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "requirements.txt",
        "src",
    ]
    return any((path / marker).exists() for marker in markers)


def effective_excludes(config: RadarConfig) -> list[str]:
    return sorted(
        set(DEFAULT_SKIP_DIRS)
        | set(BROAD_NOISE_SKIP_DIRS)
        | set(BROAD_NOISE_SKIP_PATTERNS)
        | set(config.ignore_patterns)
    )


def _is_broad_root(path: Path, home: Path | None = None) -> bool:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser()
    home_path = (home or Path.home()).expanduser().resolve()
    return resolved == home_path or resolved == Path("/")
