from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from repo_radar import __version__
from repo_radar.config import validate_config
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
from repo_radar.recommendations import annotate_queue_with_recommendations, apply_recommendations
from repo_radar.rendering import (
    render_agent_brief,
    render_agent_handoff,
    render_groups,
    render_inventory,
)
from repo_radar.runtime import RuntimeContext, persist_runtime_context, resolve_runtime_context
from repo_radar.shortlist import build_priority_queue, render_priority_queue
from repo_radar.summary import build_scan_summary, format_scan_summary

app = typer.Typer(help="Discover repositories and produce AI-ready inventory packs.")
console = Console(soft_wrap=True)


ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Optional path to repo_radar.yaml. Omit for saved state or auto-discovery.",
    ),
]
ConfigCheckOption = Annotated[Path, typer.Option("--config", "-c", help="Path to repo_radar.yaml.")]
OutputsOption = Annotated[Path, typer.Option("--outputs-dir", help="Output directory.")]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print actions without writing outputs.")
]
RootOption = Annotated[
    list[Path] | None,
    typer.Option("--root", help="Local root to scan. Repeat for multiple roots."),
]
AutoOption = Annotated[
    bool | None,
    typer.Option("--auto/--no-auto", help="Force or disable automatic local root discovery."),
]
SSHOption = Annotated[
    list[str] | None,
    typer.Option("--ssh", help="SSH target such as user@host. Repeat for multiple targets."),
]
SSHRootOption = Annotated[
    list[str] | None,
    typer.Option("--ssh-root", help="Remote root for --ssh targets. Repeat for multiple roots."),
]
IncludeNoiseOption = Annotated[
    bool,
    typer.Option("--include-noise", help="Include cache/vendor/generated scan results."),
]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"repo-radar {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Discover repositories and produce AI-ready inventory packs."""


def _resolve(
    config_path: Path | None,
    outputs_dir: Path,
    roots: list[Path] | None = None,
    auto: bool | None = None,
    ssh_targets: list[str] | None = None,
    ssh_roots: list[str] | None = None,
    include_noise: bool = False,
) -> RuntimeContext:
    return resolve_runtime_context(
        config_path=config_path,
        outputs_dir=outputs_dir,
        cli_roots=roots,
        auto=auto,
        ssh_targets=ssh_targets,
        ssh_roots=ssh_roots,
        include_noise=include_noise,
    )


def _print_effective_sources(context: RuntimeContext) -> None:
    console.print(f"source: {context.source_mode}")
    console.print(f"state: {context.state_path}")
    if context.config_path:
        console.print(f"config: {context.config_path}")
    if context.local_roots:
        console.print("local roots:")
        for root in context.local_roots:
            console.print(f"- {root}")
    if context.config.ssh_sources:
        console.print("ssh sources:")
        for source in context.config.ssh_sources:
            if source.enabled:
                console.print(f"- {source.target}: {', '.join(source.roots)}")
    if context.warnings:
        console.print("warnings:")
        for warning in context.warnings:
            console.print(f"- {warning}")
    excludes = context.source_summary().get("effective_excludes") or []
    if isinstance(excludes, list) and excludes:
        console.print("effective excludes:")
        console.print("- " + ", ".join(str(item) for item in excludes))
    rules = context.source_summary().get("noise_suppression_rules") or []
    if isinstance(rules, list) and rules:
        state = "off" if context.config.include_noise else "on"
        console.print(f"noise suppression: {state}")


def _remember(context: RuntimeContext, dry_run: bool) -> None:
    if dry_run or context.config_path is not None:
        return
    persist_runtime_context(context)


def _has_source_override(
    roots: list[Path] | None = None,
    auto: bool | None = None,
    ssh_targets: list[str] | None = None,
    include_noise: bool = False,
) -> bool:
    return bool(roots or auto is not None or ssh_targets or include_noise)


@app.command()
def scan(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
    pack: Annotated[
        bool, typer.Option("--pack", help="Generate full packs for shortlisted repos.")
    ] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Limit digest generation.")] = None,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    _print_effective_sources(context)
    records, queue = run_scan(
        context.config,
        outputs_dir,
        dry_run=dry_run,
        pack=pack,
        digest_limit=limit,
        source_summary=context.source_summary(),
    )
    _remember(context, dry_run)
    if dry_run:
        selected = sum(1 for item in queue if item.selected)
        console.print(format_scan_summary(build_scan_summary(records, queue)))
        console.print(
            f"Dry run: found {len(records)} repositories; "
            f"would select {selected} for first inspection."
        )
        return
    console.print(format_scan_summary(build_scan_summary(records, queue)))
    console.print(f"Wrote staged outputs for {len(records)} repositories to {outputs_dir}.")


@app.command()
def inventory(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    _print_effective_sources(context)
    records = discover_inventory(context.config, dry_run=dry_run)
    if dry_run:
        console.print(format_scan_summary(build_scan_summary(records)))
        console.print(f"Dry run: would write inventory for {len(records)} repositories.")
        return
    records = apply_recommendations(records)
    write_inventory_outputs(records, outputs_dir)
    _remember(context, dry_run)
    console.print(format_scan_summary(build_scan_summary(records)))
    console.print(f"Wrote inventory for {len(records)} repositories to {outputs_dir}.")


@app.command()
def reconcile(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    _print_effective_sources(context)
    records = (
        discover_inventory(context.config, dry_run=dry_run)
        if _has_source_override(roots, auto, ssh_targets, include_noise)
        else load_inventory(outputs_dir) or discover_inventory(context.config, dry_run=dry_run)
    )
    records = maybe_reconcile(records, context.config, outputs_dir=outputs_dir, dry_run=dry_run)
    if dry_run:
        console.print(format_scan_summary(build_scan_summary(records)))
        console.print(f"Dry run: would write reconciled inventory for {len(records)} repositories.")
        return
    records = apply_recommendations(records)
    write_inventory_outputs(records, outputs_dir)
    _remember(context, dry_run)
    console.print(f"Wrote reconciled inventory for {len(records)} repositories.")


@app.command()
def digest(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Limit digest generation.")] = None,
) -> None:
    context = _resolve(config_path, outputs_dir)
    records = load_inventory(outputs_dir) or discover_inventory(context.config, dry_run=dry_run)
    results = run_digest(records, context.config, outputs_dir, dry_run=dry_run, limit=limit)
    console.print(f"{'Dry run: would create' if dry_run else 'Created'} {len(results)} digests.")


@app.command()
def shortlist(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    token_budget: Annotated[
        int | None, typer.Option("--token-budget", help="Override configured token budget.")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Override configured max repos.")
    ] = None,
) -> None:
    context = _resolve(config_path, outputs_dir)
    config = context.config
    if token_budget is not None:
        config.shortlist.token_budget = token_budget
    if limit is not None:
        config.shortlist.max_repos = limit
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    queue = build_priority_queue(records, config.shortlist.token_budget, config.shortlist.max_repos)
    records = apply_recommendations(records, {item.path: item.score for item in queue})
    queue = annotate_queue_with_recommendations(queue, records)
    if dry_run:
        console.print(f"Dry run: would write shortlist with {len(queue)} ranked repositories.")
        return
    render_priority_queue(queue, outputs_dir)
    console.print(
        f"Wrote priority queue with {sum(item.selected for item in queue)} selected repositories."
    )


@app.command(name="pack")
def pack_command(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir)
    config = context.config
    records = load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    queue = load_queue(outputs_dir) or build_priority_queue(
        records,
        config.shortlist.token_budget,
        config.shortlist.max_repos,
    )
    records = apply_recommendations(records, {item.path: item.score for item in queue})
    queue = annotate_queue_with_recommendations(queue, records)
    results = run_full_pack(records, queue, config, outputs_dir, dry_run=dry_run)
    console.print(f"{'Dry run: would create' if dry_run else 'Created'} {len(results)} full packs.")


@app.command()
def brief(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    _print_effective_sources(context)
    config = context.config
    source_override = _has_source_override(roots, auto, ssh_targets, include_noise)
    records = (
        discover_inventory(config, dry_run=dry_run)
        if source_override
        else load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    )
    queue = (
        build_priority_queue(records, config.shortlist.token_budget, config.shortlist.max_repos)
        if source_override
        else load_queue(outputs_dir)
    ) or build_priority_queue(
        records,
        config.shortlist.token_budget,
        config.shortlist.max_repos,
    )
    groups = render_groups(records, outputs_dir) if not dry_run else {"duplicates": []}
    if dry_run:
        console.print(f"Dry run: would write agent brief for {len(records)} repositories.")
        return
    records = apply_recommendations(records, {item.path: item.score for item in queue})
    queue = annotate_queue_with_recommendations(queue, records)
    groups = render_groups(records, outputs_dir)
    render_inventory(records, outputs_dir)
    render_agent_brief(records, queue, groups, outputs_dir, source_summary=context.source_summary())
    _remember(context, dry_run)
    console.print(f"Wrote agent brief to {outputs_dir / 'agent_brief.md'}.")


@app.command()
def handoff(
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    dry_run: DryRunOption = False,
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    _print_effective_sources(context)
    config = context.config
    source_override = _has_source_override(roots, auto, ssh_targets, include_noise)
    records = (
        discover_inventory(config, dry_run=dry_run)
        if source_override
        else load_inventory(outputs_dir) or discover_inventory(config, dry_run=dry_run)
    )
    queue = (
        build_priority_queue(records, config.shortlist.token_budget, config.shortlist.max_repos)
        if source_override
        else load_queue(outputs_dir)
    ) or build_priority_queue(
        records,
        config.shortlist.token_budget,
        config.shortlist.max_repos,
    )
    groups = render_groups(records, outputs_dir) if not dry_run else {"duplicates": []}
    if dry_run:
        console.print(format_scan_summary(build_scan_summary(records, queue)))
        console.print(f"Dry run: would write agent handoff for {len(records)} repositories.")
        return
    records = apply_recommendations(records, {item.path: item.score for item in queue})
    queue = annotate_queue_with_recommendations(queue, records)
    groups = render_groups(records, outputs_dir)
    render_agent_handoff(
        records,
        queue,
        groups,
        outputs_dir,
        source_summary=context.source_summary(),
    )
    _remember(context, dry_run)
    console.print(format_scan_summary(build_scan_summary(records, queue)))
    console.print(f"Wrote agent handoff to {outputs_dir / 'agent_handoff.md'}.")


@app.command(name="config-check")
def config_check(
    config_path: ConfigCheckOption = Path("repo_radar.yaml"),
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
    config_path: ConfigOption = None,
    outputs_dir: OutputsOption = Path("outputs"),
    roots: RootOption = None,
    auto: AutoOption = None,
    ssh_targets: SSHOption = None,
    ssh_roots: SSHRootOption = None,
    include_noise: IncludeNoiseOption = False,
) -> None:
    context = _resolve(config_path, outputs_dir, roots, auto, ssh_targets, ssh_roots, include_noise)
    config_report = validate_config(config_path) if config_path is not None else None
    packer_status = get_packer_status(context.config.packer)

    _print_effective_sources(context)
    if config_report is None:
        console.print("config: not used")
    elif config_report.ok:
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

    console.print("next:")
    console.print("- repo-radar scan --dry-run")
    console.print("- repo-radar inventory")
    console.print("- repo-radar handoff")

    if (
        (config_report is not None and not config_report.ok)
        or not packer_status.available
        or (not context.local_roots and not context.ssh_targets)
    ):
        raise typer.Exit(code=1)
