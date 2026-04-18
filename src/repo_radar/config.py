from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "local"
    roots: list[Path] = Field(default_factory=lambda: [Path(".")])
    max_depth: int | None = None
    enabled: bool = True

    @field_validator("roots", mode="before")
    @classmethod
    def normalize_roots(cls, value: object) -> list[Path]:
        if value is None:
            return [Path(".")]
        if isinstance(value, (str, Path)):
            return [Path(value)]
        return [Path(item) for item in value]  # type: ignore[arg-type]


class SSHSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    host: str
    roots: list[str]
    user: str | None = None
    port: int | None = None
    max_depth: int | None = None
    enabled: bool = False
    timeout_seconds: int = 30

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    use_gh: bool = True
    timeout_seconds: int = 10


class PackerSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    backend: Literal["repomix", "code2prompt"] = "repomix"
    command: str | None = None
    style: Literal["xml", "markdown", "json", "plain"] = "xml"
    include_patterns: list[str] = Field(default_factory=list)
    ignore_patterns: list[str] = Field(default_factory=list)
    security_check: bool = True
    remove_comments: bool = False
    remove_empty_lines: bool = False
    extra_args: list[str] = Field(default_factory=list)
    token_count_encoding: str = "o200k_base"


class ShortlistSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token_budget: int = 200_000
    max_repos: int = 10


class RadarConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    local_roots: list[Path] = Field(default_factory=lambda: [Path(".")])
    local_sources: list[LocalSourceConfig] = Field(default_factory=list)
    ssh_sources: list[SSHSourceConfig] = Field(default_factory=list)
    ignore_patterns: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    max_depth: int = 5
    outputs_dir: Path = Path("outputs")
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    packer: PackerSettings = Field(default_factory=PackerSettings)
    shortlist: ShortlistSettings = Field(default_factory=ShortlistSettings)

    @field_validator("local_roots", mode="before")
    @classmethod
    def normalize_roots(cls, value: object) -> list[Path]:
        if value is None:
            return [Path(".")]
        if isinstance(value, (str, Path)):
            return [Path(value)]
        return [Path(item) for item in value]  # type: ignore[arg-type]

    def iter_local_sources(self) -> list[LocalSourceConfig]:
        if self.local_sources:
            return [
                LocalSourceConfig(
                    name=source.name,
                    roots=source.roots,
                    max_depth=source.max_depth if source.max_depth is not None else self.max_depth,
                    enabled=source.enabled,
                )
                for source in self.local_sources
            ]
        return [LocalSourceConfig(name="local", roots=self.local_roots, max_depth=self.max_depth)]


def load_config(path: Path | str = Path("repo_radar.yaml")) -> RadarConfig:
    config_path = Path(path)
    if not config_path.exists():
        return RadarConfig()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return RadarConfig.model_validate(raw)
