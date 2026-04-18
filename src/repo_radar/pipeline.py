from __future__ import annotations

import json
from pathlib import Path

from repo_radar.classification import classify_repo
from repo_radar.config import RadarConfig
from repo_radar.discovery.local import LocalFilesystemAdapter
from repo_radar.discovery.ssh import SSHSourceAdapter
from repo_radar.duplicates import assign_duplicate_clusters
from repo_radar.metadata import extract_local_metadata
from repo_radar.models import PriorityQueueItem, RepoRecord
from repo_radar.noise import filter_noise
from repo_radar.packers import create_packer, pack_repositories, pack_shortlisted_repos
from repo_radar.recommendations import annotate_queue_with_recommendations, apply_recommendations
from repo_radar.reconciliation import GitHubCache, reconcile_records
from repo_radar.relationships import apply_project_relationships
from repo_radar.rendering import (
    render_agent_brief,
    render_agent_handoff,
    render_groups,
    render_inventory,
)
from repo_radar.shortlist import build_priority_queue, render_priority_queue


def discover_inventory(config: RadarConfig, dry_run: bool = False) -> list[RepoRecord]:
    records: dict[tuple[str, str], RepoRecord] = {}
    local_adapter = LocalFilesystemAdapter(
        config.ignore_patterns,
        config.include_patterns,
        broad_scan=config.broad_scan,
        include_noise=config.include_noise,
    )
    for source in config.iter_local_sources():
        for project in local_adapter.discover(source):
            record = classify_repo(
                extract_local_metadata(
                    project,
                    config.ignore_patterns,
                    config.include_patterns,
                    max_file_depth=2 if config.broad_scan else None,
                )
            )
            records[(record.source_type, record.path)] = record

    ssh_adapter = SSHSourceAdapter(config.ignore_patterns)
    for source in config.ssh_sources:
        for project in ssh_adapter.discover(source, dry_run=dry_run):
            record = RepoRecord(
                path=project.path,
                name=project.name,
                source_type="ssh",
                source_name=source.name,
                is_git=project.is_git,
                is_repo_like=project.is_repo_like,
                markers=project.markers,
                estimated_size_bytes=project.estimated_size_bytes,
                project_type="git-only" if project.is_git else "repo-like",
            )
            records[(record.source_type, record.path)] = record
    filtered = filter_noise(
        sorted(records.values(), key=lambda record: (record.name or "", record.path)),
        broad_scan=config.broad_scan,
        include_noise=config.include_noise,
    )
    related = apply_project_relationships(filtered)
    clustered, _clusters = assign_duplicate_clusters(related)
    return clustered


def write_inventory_outputs(records: list[RepoRecord], outputs_dir: Path) -> dict[str, object]:
    render_inventory(records, outputs_dir)
    return render_groups(records, outputs_dir)


def maybe_reconcile(
    records: list[RepoRecord],
    config: RadarConfig,
    outputs_dir: Path | None = None,
    dry_run: bool = False,
) -> list[RepoRecord]:
    if not config.github.enabled:
        return records
    cache = _github_cache(config, outputs_dir or config.outputs_dir, read_only=dry_run)
    reconcilable = [record for record in records if not record.suppressed]
    suppressed = [record for record in records if record.suppressed]
    reconciled = [*reconcile_records(reconcilable, config.github, cache=cache), *suppressed]
    related = apply_project_relationships(reconciled)
    clustered, _clusters = assign_duplicate_clusters(related)
    return clustered


def build_and_write_shortlist(
    records: list[RepoRecord],
    config: RadarConfig,
    outputs_dir: Path,
) -> list[PriorityQueueItem]:
    queue = build_priority_queue(
        records,
        token_budget=config.shortlist.token_budget,
        max_repos=config.shortlist.max_repos,
    )
    render_priority_queue(queue, outputs_dir)
    return queue


def run_digest(
    records: list[RepoRecord],
    config: RadarConfig,
    outputs_dir: Path,
    dry_run: bool = False,
    limit: int | None = None,
):
    packer = create_packer(config.packer)
    return pack_repositories(
        records,
        outputs_dir / "repo_digests",
        packer,
        compressed=True,
        dry_run=dry_run,
        limit=limit,
    )


def run_full_pack(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem],
    config: RadarConfig,
    outputs_dir: Path,
    dry_run: bool = False,
):
    packer = create_packer(config.packer)
    return pack_shortlisted_repos(
        records,
        queue,
        outputs_dir / "repo_fullpacks",
        packer,
        compressed=False,
        dry_run=dry_run,
    )


def load_inventory(outputs_dir: Path) -> list[RepoRecord]:
    path = outputs_dir / "repo_inventory.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RepoRecord.model_validate(item) for item in payload.get("repositories", [])]


def load_queue(outputs_dir: Path) -> list[PriorityQueueItem]:
    path = outputs_dir / "repo_priority_queue.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [PriorityQueueItem.model_validate(item) for item in payload.get("items", [])]


def run_scan(
    config: RadarConfig,
    outputs_dir: Path,
    dry_run: bool = False,
    pack: bool = False,
    digest_limit: int | None = None,
    source_summary: dict[str, object] | None = None,
) -> tuple[list[RepoRecord], list[PriorityQueueItem]]:
    records = discover_inventory(config, dry_run=dry_run)
    records = maybe_reconcile(records, config, outputs_dir=outputs_dir, dry_run=dry_run)
    queue = build_priority_queue(
        records,
        token_budget=config.shortlist.token_budget,
        max_repos=config.shortlist.max_repos,
    )
    records = apply_recommendations(records, {item.path: item.score for item in queue})
    queue = annotate_queue_with_recommendations(queue, records)
    if dry_run:
        return records, queue
    groups = write_inventory_outputs(records, outputs_dir)
    render_priority_queue(queue, outputs_dir)
    run_digest(records, config, outputs_dir, dry_run=False, limit=digest_limit)
    if pack:
        run_full_pack(records, queue, config, outputs_dir, dry_run=False)
    render_agent_brief(records, queue, groups, outputs_dir, source_summary=source_summary)
    return records, queue


def run_handoff(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem],
    outputs_dir: Path,
    source_summary: dict[str, object] | None = None,
) -> Path:
    groups = render_groups(records, outputs_dir)
    return render_agent_handoff(records, queue, groups, outputs_dir, source_summary=source_summary)


def _github_cache(
    config: RadarConfig,
    outputs_dir: Path,
    read_only: bool = False,
) -> GitHubCache | None:
    if not config.github.cache_enabled:
        return None
    cache_path = config.github.cache_path or (outputs_dir / ".cache" / "github_reconciliation.json")
    return GitHubCache(
        path=cache_path,
        ttl_seconds=config.github.cache_ttl_seconds,
        read_only=read_only,
    )
