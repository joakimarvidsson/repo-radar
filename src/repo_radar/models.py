from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RadarBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class DiscoveredProject(RadarBaseModel):
    path: str
    name: str | None = None
    source_type: Literal["local", "ssh"] = "local"
    source_name: str = "local"
    is_git: bool = False
    is_repo_like: bool = True
    markers: list[str] = Field(default_factory=list)
    estimated_size_bytes: int = 0

    @model_validator(mode="after")
    def fill_name(self) -> DiscoveredProject:
        if not self.name:
            self.name = self.path.rstrip("/").split("/")[-1] or self.path
        return self


class GitMetadata(RadarBaseModel):
    remotes: dict[str, str] = Field(default_factory=dict)
    current_branch: str | None = None
    default_branch: str | None = None
    last_commit_date: str | None = None
    ahead: int | None = None
    behind: int | None = None
    divergence_status: str | None = None
    has_uncommitted_changes: bool = False
    changed_files: int = 0
    untracked_files: int = 0


class GitHubReconciliation(RadarBaseModel):
    github_repo: str | None = None
    exists: bool = False
    visibility: str | None = None
    url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    remote_matches: bool | None = None
    mismatch_reason: str | None = None
    orphan_candidate: bool = False
    checked_with: str = "none"
    error: str | None = None


class RepoRecord(RadarBaseModel):
    path: str
    name: str | None = None
    source_type: Literal["local", "ssh"] = "local"
    source_name: str = "local"
    is_git: bool = False
    is_repo_like: bool = False
    markers: list[str] = Field(default_factory=list)
    git: GitMetadata | None = None
    github: GitHubReconciliation | None = None
    primary_languages: list[str] = Field(default_factory=list)
    language_file_counts: dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    estimated_size_bytes: int = 0
    key_directories: list[str] = Field(default_factory=list)
    project_type: str = "unknown"
    maturity_score: int = 0
    maturity_signals: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_name(self) -> RepoRecord:
        if not self.name:
            self.name = self.path.rstrip("/").split("/")[-1] or self.path
        return self


class PriorityQueueItem(RadarBaseModel):
    rank: int
    name: str
    path: str
    project_type: str = "unknown"
    score: int
    estimated_tokens: int = 0
    reasons: list[str] = Field(default_factory=list)
    selected: bool = False


class PackerResult(RadarBaseModel):
    repo_path: str
    output_path: str
    backend: str
    compressed: bool
    dry_run: bool = False
    success: bool
    command: list[str] = Field(default_factory=list)
    error: str | None = None


class InventoryDocument(RadarBaseModel):
    schema_version: str = "1.0"
    repository_count: int
    repositories: list[dict[str, Any]]
