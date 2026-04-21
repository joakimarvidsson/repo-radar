from __future__ import annotations

import json
from pathlib import Path

from repo_radar.installed_audit import InstalledAuditRepo, InstalledAuditReport


def render_installed_audit_outputs(
    report: InstalledAuditReport,
    outputs_dir: Path,
    *,
    write_plan: bool = False,
) -> dict[str, Path]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = outputs_dir / "installed_audit.md"
    json_path = outputs_dir / "installed_audit.json"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_installed_audit_markdown(report), encoding="utf-8")
    outputs = {"markdown": markdown_path, "json": json_path}
    if write_plan:
        plan_path = outputs_dir / "update_plan.md"
        plan_path.write_text(_update_plan_markdown(report), encoding="utf-8")
        outputs["plan"] = plan_path
    return outputs


def _installed_audit_markdown(report: InstalledAuditReport) -> str:
    summary = report.summary
    lines = [
        "# Installed Tools Freshness Audit",
        "",
        "Advisory only. This report does not pull, merge, rebase, "
        "delete, or push repositories automatically.",
        "",
        "## Summary",
        "",
        f"- Git repositories audited: {summary.total_git_repositories}",
        f"- GitHub remotes: {summary.github_remote}",
        f"- Non-GitHub remotes: {summary.non_github_remote}",
        f"- No remote: {summary.no_remote}",
        f"- Likely safe to update: {summary.safe_to_update}",
        f"- Behind remote: {summary.behind_remote}",
        f"- Dirty working trees: {summary.dirty_worktrees}",
        f"- Ahead of remote: {summary.ahead_of_remote}",
        f"- Diverged: {summary.diverged}",
        f"- Detached or unusual: {summary.detached_or_unusual}",
        f"- Manual review: {summary.manual_review}",
    ]
    if report.live_mode:
        lines.extend(
            [
                f"- Live checks performed: {summary.live_checks_performed}",
                f"- Live checks skipped: {summary.live_checks_skipped}",
                f"- Live check limit: {summary.live_check_limit}",
            ]
        )
    if report.warnings:
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(
        _section(
            "Likely Safe To Update",
            report.safe_to_update,
            empty="No clean GitHub-backed fast-forward update candidates were found.",
        )
    )
    lines.extend(
        _section(
            "Behind Remote",
            report.behind_remote,
            empty="No repositories currently appear behind their configured upstream.",
        )
    )
    lines.extend(
        _section(
            "Dirty Working Trees",
            report.dirty_worktrees,
            empty="No dirty working trees were detected.",
        )
    )
    lines.extend(
        _section(
            "No Remote",
            report.no_remote,
            empty="All audited repositories have at least one remote configured.",
        )
    )
    lines.extend(
        _section(
            "Non-GitHub Remotes",
            report.non_github_remotes,
            empty="No non-GitHub primary remotes were detected.",
        )
    )
    lines.extend(
        _section(
            "Manual Review",
            report.manual_review,
            empty="No repositories currently require manual review before an update attempt.",
        )
    )
    return "\n".join(lines) + "\n"


def _update_plan_markdown(report: InstalledAuditReport) -> str:
    lines = [
        "# Installed Tool Update Plan",
        "",
        "Advisory only. Review each repository before running any command.",
        "",
        "## Safe Candidates",
        "",
    ]
    if report.safe_to_update:
        for repo in report.safe_to_update:
            lines.extend(_repo_block(repo))
    else:
        lines.append("- No clean fast-forward candidates were found.")

    lines.extend(["", "## Manual Review Queue", ""])
    if report.manual_review:
        for repo in report.manual_review:
            lines.extend(_repo_block(repo))
    else:
        lines.append("- No manual-review items were found.")
    return "\n".join(lines) + "\n"


def _section(title: str, repos: list[InstalledAuditRepo], *, empty: str) -> list[str]:
    lines = ["", f"## {title}", ""]
    if not repos:
        lines.append(f"- {empty}")
        return lines
    for repo in repos:
        lines.extend(_repo_block(repo))
    return lines


def _repo_block(repo: InstalledAuditRepo) -> list[str]:
    details: list[str] = []
    if repo.current_branch:
        details.append(f"branch `{repo.current_branch}`")
    if repo.primary_remote:
        details.append(f"remote `{repo.primary_remote.name}`")
    details.append(f"state `{repo.sync_status}`")
    if repo.behind:
        details.append(f"behind {repo.behind}")
    if repo.ahead:
        details.append(f"ahead {repo.ahead}")
    details.append("dirty" if repo.has_uncommitted_changes else "clean")
    reasons = "; ".join(repo.reasons[:3])
    commands = "; ".join(f"`{command}`" for command in repo.suggested_commands)
    lines = [
        f"- {repo.name or Path(repo.path).name} at `{repo.path}`: {', '.join(details)}.",
    ]
    if reasons:
        lines.append(f"  Notes: {reasons}.")
    if commands:
        lines.append(f"  Advisory commands: {commands}")
    return lines
