from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from repo_radar.config import load_config, validate_config
from repo_radar.packers import get_packer_status
from repo_radar.pipeline import (
    discover_inventory,
    load_inventory,
    load_queue,
    maybe_reconcile,
    run_digest,
    run_full_pack,
    run_scan,
    write_inventory_outputs,
)
from repo_radar.rendering import render_agent_brief, render_groups, render_inventory
from repo_radar.shortlist import build_priority_queue, render_priority_queue

app = typer.Typer(help="Discover repositories and produce AI-ready inventory packs.")
console = Console(soft_wrap=True)


ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Path to repo_radar.yaml.")]
OutputsOption = Annotated[Path, typer.Option("--outputs-dir", help="Output directory.")]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print actions without writing outputs.")
]


def _load(config_path: Path, outputs_dir: Path):
    config = load_config(config_path)
    config.outputs_dir = outputs_dir
    return config


@app.command()
def scan(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    pack: Annotated[
        bool, typer.Option("--pack", help="Generate full packs for shortlisted repos.")
    ] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Limit digest generation.")] = None,
) -> None:
    config = _load(config_path, outputs_dir)
    records, queue = run_scan(config, outputs_dir, dry_run=dry_run, pack=pack, digest_limit=limit)
    if dry_run:
        selected = sum(1 for item in queue if item.selected)
        console.print(
            f"Dry run: found {len(records)} repositories; "
            f"would select {selected} for first inspection."
        )
        return
    console.print(f"Wrote staged outputs for {len(records)} repositories to {outputs_dir}.")


@app.command()
def inventory(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
) -> None:
    config = _load(config_path, outputs_dir)
    records = discover_inventory(config, dry_run=dry_run)
    if dry_run:
        console.print(f"Dry run: would write inventory for {len(records)} repositories.")
        return
    write_inventory_outputs(records, outputs_dir)
    console.print(f"Wrote inventory for {len(records)} repositories to {outputs_dir}.")


@app.command()
def reconcile(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
) -> None:
    config = _load(config_path, outputs_dir)
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    records = maybe_reconcile(records, config)
    if dry_run:
        console.print(f"Dry run: would write reconciled inventory for {len(records)} repositories.")
        return
    write_inventory_outputs(records, outputs_dir)
    console.print(f"Wrote reconciled inventory for {len(records)} repositories.")


@app.command()
def digest(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Limit digest generation.")] = None,
) -> None:
    config = _load(config_path, outputs_dir)
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    results = run_digest(records, config, outputs_dir, dry_run=dry_run, limit=limit)
    console.print(f"{'Dry run: would create' if dry_run else 'Created'} {len(results)} digests.")


@app.command()
def shortlist(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    token_budget: Annotated[
        int | None, typer.Option("--token-budget", help="Override configured token budget.")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Override configured max repos.")
    ] = None,
) -> None:
    config = _load(config_path, outputs_dir)
    if token_budget is not None:
        config.shortlist.token_budget = token_budget
    if limit is not None:
        config.shortlist.max_repos = limit
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    queue = build_priority_queue(records, config.shortlist.token_budget, config.shortlist.max_repos)
    if dry_run:
        console.print(f"Dry run: would write shortlist with {len(queue)} ranked repositories.")
        return
    render_priority_queue(queue, outputs_dir)
    console.print(
        f"Wrote priority queue with {sum(item.selected for item in queue)} selected repositories."
    )


@app.command(name="pack")
def pack_command(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
) -> None:
    config = _load(config_path, outputs_dir)
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    queue = load_queue(outputs_dir) or build_priority_queue(
        records,
        config.shortlist.token_budget,
        config.shortlist.max_repos,
    )
    results = run_full_pack(records, queue, config, outputs_dir, dry_run=dry_run)
    console.print(f"{'Dry run: would create' if dry_run else 'Created'} {len(results)} full packs.")


@app.command()
def brief(
    config_path: ConfigOption = Path("repo_radar.yaml"),
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
) -> None:
    config = _load(config_path, outputs_dir)
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    queue = load_queue(outputs_dir) or build_priority_queue(
        records,
        config.shortlist.token_budget,
        config.shortlist.max_repos,
    )
    groups = render_groups(records, outputs_dir) if not dry_run else {"duplicates": []}
    if dry_run:
        console.print(f"Dry run: would write agent brief for {len(records)} repositories.")
        return
    render_inventory(records, outputs_dir)
    render_agent_brief(records, queue, groups, outputs_dir)
    console.print(f"Wrote agent brief to {outputs_dir / 'agent_brief.md'}.")


@app.command(name="config-check")
def config_check(
    config_path: ConfigOption = Path("repo_radar.yaml"),
) -> None:
    report = validate_config(config_path)
    if report.ok:
        console.print(f"Config OK: {report.config_path}")
        if report.local_roots:
            console.print("Local roots:")
            for root in report.local_roots:
                console.print(f"- {root}")
        if report.warnings:
            console.print("Warnings:")
            for warning in report.warnings:
                console.print(f"- {warning}")
        return

    console.print(f"Config invalid: {report.config_path}")
    for error in report.errors:
        console.print(f"- {error}")
    raise typer.Exit(code=1)


@app.command()
def doctor(
    config_path: ConfigOption = Path("repo_radar.yaml"),
) -> None:
    config_report = validate_config(config_path)
    config = load_config(config_path) if Path(config_path).exists() else None
    packer_status = get_packer_status(config.packer if config else load_config().packer)

    if config_report.ok:
        console.print("config: ok")
    else:
        console.print("config: invalid")
        for error in config_report.errors:
            console.print(f"- {error}")

    packer_word = "available" if packer_status.available else "unavailable"
    console.print(f"packer: {packer_status.backend} {packer_word}")
    console.print(f"- {packer_status.message}")
    if packer_status.command:
        console.print(f"- command: {' '.join(packer_status.command)}")

    if not config_report.ok or not packer_status.available:
        raise typer.Exit(code=1)
