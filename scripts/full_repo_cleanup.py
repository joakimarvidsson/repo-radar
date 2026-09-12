#!/usr/bin/env python3
"""Full local + GitHub repo cleanup runner (Desktop / Monty).

Implements the archive-first consolidation plan:
  Phase 0  safety rails + private-repo gate
  Phase 1  dual inventory (repo-radar + gh)
  Phase 2  classify into decisions.md
  Phase 3  Numerai Classic / Signals / Crypto consolidation helpers
  Phase 4  batch archive remaining stale remotes
  Phase 5  verification checklist

Hard rules:
  - Never ``gh repo delete`` in pass 1 (archive only)
  - Never permanently delete local clones until quarantined after remote archive
  - Abort if private GitHub repos are not visible (wrong machine / token)

Usage (from a repo-radar checkout on Monty)::

    uv run python scripts/full_repo_cleanup.py --root ~
    uv run python scripts/full_repo_cleanup.py --root ~ --apply-archive
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Bucket = Literal[
    "KEEP_ACTIVE",
    "MERGE_THEN_ARCHIVE",
    "ARCHIVE_AS_IS",
    "LOCAL_ONLY_REVIEW",
    "IGNORE_NOISE",
]

NUMERAI_CLASSIC_HINTS = (
    "classic",
    "tournament",
    "ai-harness",
    "ai_harness",
    "numerai-classic",
    "numerai_classic",
    "xgb_eb",
    "nn_example",
)
NUMERAI_SIGNALS_HINTS = ("signals", "dsignals", "ysignals")
NUMERAI_CRYPTO_HINTS = ("crypto",)
NUMERAI_MISC_HINTS = ("numerai", "numerapi", "nmr")

KEEP_NAME_HINTS = (
    "repo-radar",
    "joakimarvidsson",  # profile README
    "typed-python-zero-to-hero",
    "ai-agents-workshop",
    "loop-library",
    "pipelinebench",
    "cursorfy",
    "ralph-to-ralph",
    "pi-apes",
)


@dataclass
class Decision:
    repo: str
    bucket: Bucket
    keeper: str = ""
    evidence: str = ""
    status: str = "pending"
    is_private: bool = False
    is_archived: bool = False
    is_fork: bool = False
    pushed_at: str = ""
    url: str = ""
    local_paths: list[str] = field(default_factory=list)


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def cleanup_dir(explicit: Path | None) -> Path:
    path = explicit or Path.home() / f"repo-cleanup-{utc_stamp()}"
    (path / "quarantine").mkdir(parents=True, exist_ok=True)
    (path / "radar").mkdir(parents=True, exist_ok=True)
    return path


def phase0_gate(out: Path) -> dict[str, Any]:
    """Confirm gh sees private repos; write BLOCKER.md and exit if not."""
    auth = run(["gh", "auth", "status"], check=False)
    listing = run(
        [
            "gh",
            "repo",
            "list",
            "--limit",
            "20",
            "--json",
            "name,isPrivate,isArchived,pushedAt",
        ],
        check=False,
    )
    info: dict[str, Any] = {
        "auth_ok": auth.returncode == 0,
        "auth_stderr": (auth.stderr or "")[:2000],
        "list_ok": listing.returncode == 0,
        "list_stderr": (listing.stderr or "")[:2000],
        "private_visible": False,
        "sample": [],
    }
    if listing.returncode == 0 and listing.stdout.strip():
        sample = json.loads(listing.stdout)
        info["sample"] = sample
        info["private_visible"] = any(r.get("isPrivate") for r in sample)

    (out / "phase0_gate.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    if not info["private_visible"]:
        blocker = out / "BLOCKER.md"
        blocker.write_text(
            "\n".join(
                [
                    "# BLOCKER: private GitHub repos not visible",
                    "",
                    "This runner must execute on a machine where `gh` is authenticated",
                    "as an account that can list **private** repos (typically Monty / Desktop",
                    "with `joakimarvidsson` login).",
                    "",
                    "Observed:",
                    f"- `gh auth status` exit={auth.returncode}",
                    "- private repos in first 20 listings: **no**",
                    "",
                    "No archive / merge / quarantine-delete actions were taken.",
                    "",
                    "Unblock: re-run on Monty after `gh auth login` as joakimarvidsson,",
                    "or re-dispatch the Cloud Agent with Self-hosted → Monty.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(blocker.read_text(encoding="utf-8"), file=sys.stderr)
        raise SystemExit(2)
    print("Phase 0 OK: private repos visible.")
    return info


def export_github(out: Path, owner: str) -> list[dict[str, Any]]:
    result = run(
        [
            "gh",
            "repo",
            "list",
            owner,
            "--limit",
            "1000",
            "--json",
            "name,description,isPrivate,isArchived,isFork,pushedAt,updatedAt,url,diskUsage",
        ]
    )
    repos = json.loads(result.stdout)
    path = out / "github_repos.json"
    path.write_text(json.dumps(repos, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(repos)} repos)")
    return repos


def run_repo_radar(repo_radar_root: Path, roots: list[Path], out: Path) -> None:
    radar_out = out / "radar"
    for root in roots:
        run(
            ["uv", "run", "repo-radar", "doctor", "--root", str(root)],
            cwd=repo_radar_root,
            check=False,
            capture=False,
        )
    # Single handoff covering all roots
    cmd = ["uv", "run", "repo-radar", "handoff", "--outputs-dir", str(radar_out)]
    for root in roots:
        cmd.extend(["--root", str(root)])
    run(cmd, cwd=repo_radar_root, check=False, capture=False)


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def cluster_numerai(name: str) -> str | None:
    n = _norm(name)
    if any(h in n for h in NUMERAI_CRYPTO_HINTS) and "numerai" in n:
        return "crypto"
    if any(h in n for h in NUMERAI_SIGNALS_HINTS):
        return "signals"
    if any(h in n for h in NUMERAI_CLASSIC_HINTS) or n in {
        "numerai-tournament",
        "numerai-xgb-eb",
        "numerai-nn-example",
    }:
        return "classic"
    if any(h in n for h in NUMERAI_MISC_HINTS):
        return "misc"
    return None


def score_keeper(repo: dict[str, Any]) -> tuple[int, str]:
    """Higher is better. Prefer non-fork, non-archived, recent, harness-ish names."""
    name = _norm(repo["name"])
    score = 0
    reasons: list[str] = []
    if not repo.get("isFork"):
        score += 50
        reasons.append("original")
    if not repo.get("isArchived"):
        score += 40
        reasons.append("active")
    if repo.get("isPrivate"):
        score += 20
        reasons.append("private")
    if "harness" in name or "2026" in name or "master" in name:
        score += 30
        reasons.append("harness/2026/master signal")
    pushed = repo.get("pushedAt") or ""
    if pushed:
        # crude recency boost by year
        try:
            year = int(pushed[:4])
            score += max(0, year - 2020) * 5
            reasons.append(f"pushed:{pushed[:10]}")
        except ValueError:
            pass
    disk = int(repo.get("diskUsage") or 0)
    score += min(disk // 500, 20)
    return score, ", ".join(reasons)


def classify_repos(
    repos: list[dict[str, Any]],
    *,
    local_by_remote: dict[str, list[str]],
    provisional_classic: str,
) -> list[Decision]:
    decisions: list[Decision] = []
    by_cluster: dict[str, list[dict[str, Any]]] = {
        "classic": [],
        "signals": [],
        "crypto": [],
        "misc": [],
    }
    others: list[dict[str, Any]] = []

    for repo in repos:
        cluster = cluster_numerai(repo["name"])
        if cluster:
            by_cluster[cluster].append(repo)
        else:
            others.append(repo)

    keepers: dict[str, str] = {}
    for cluster, members in by_cluster.items():
        if not members:
            continue
        active = [m for m in members if not m.get("isArchived")]
        if active:
            pool = active
            if cluster == "classic":
                preferred = [m for m in pool if _norm(m["name"]) == _norm(provisional_classic)]
                if preferred:
                    keepers[cluster] = preferred[0]["name"]
                else:
                    ranked = sorted(pool, key=score_keeper, reverse=True)
                    keepers[cluster] = ranked[0]["name"]
            else:
                ranked = sorted(pool, key=score_keeper, reverse=True)
                keepers[cluster] = ranked[0]["name"]
        else:
            # Entire cluster already archived — no active keeper
            pass

        for member in members:
            name = member["name"]
            paths = local_by_remote.get(name, []) + local_by_remote.get(
                f"joakimarvidsson/{name}", []
            )
            if member.get("isArchived"):
                bucket: Bucket = "ARCHIVE_AS_IS"
                evidence = "already archived"
                status = "done"
                keeper = keepers.get(cluster, "")
            elif cluster in keepers and name == keepers[cluster]:
                bucket = "KEEP_ACTIVE"
                _, why = score_keeper(member)
                evidence = f"Numerai {cluster} keeper ({why})"
                status = "pending"
                keeper = keepers[cluster]
            elif cluster in keepers:
                bucket = "MERGE_THEN_ARCHIVE"
                evidence = f"Numerai {cluster} sibling of keeper `{keepers[cluster]}`"
                status = "pending"
                keeper = keepers[cluster]
            else:
                bucket = "ARCHIVE_AS_IS"
                evidence = f"Numerai {cluster} cluster has no active keeper candidate"
                status = "pending"
                keeper = ""
            decisions.append(
                Decision(
                    repo=name,
                    bucket=bucket,
                    keeper=keeper,
                    evidence=evidence,
                    status=status,
                    is_private=bool(member.get("isPrivate")),
                    is_archived=bool(member.get("isArchived")),
                    is_fork=bool(member.get("isFork")),
                    pushed_at=str(member.get("pushedAt") or ""),
                    url=str(member.get("url") or ""),
                    local_paths=paths,
                )
            )

    for member in others:
        name = member["name"]
        n = _norm(name)
        paths = local_by_remote.get(name, []) + local_by_remote.get(f"joakimarvidsson/{name}", [])
        if member.get("isArchived"):
            bucket = "ARCHIVE_AS_IS"
            evidence = "already archived"
            status = "done"
        elif any(h in n for h in KEEP_NAME_HINTS):
            bucket = "KEEP_ACTIVE"
            evidence = "matched keep-name heuristic (active tooling / workshop / profile)"
            status = "pending"
        elif member.get("isFork") and not member.get("isPrivate"):
            bucket = "ARCHIVE_AS_IS"
            evidence = "public fork — archive unless local commits ahead of upstream"
            status = "pending"
        else:
            # Default: keep recent originals, archive older quiet repos
            pushed = str(member.get("pushedAt") or "")
            recent = (
                pushed.startswith("2026")
                or pushed.startswith("2025-1")
                or pushed.startswith("2025-0")
            )
            if recent and not member.get("isFork"):
                bucket = "KEEP_ACTIVE"
                evidence = f"recent original push ({pushed[:10]})"
                status = "pending"
            else:
                bucket = "ARCHIVE_AS_IS"
                evidence = f"stale or low-priority ({pushed[:10] or 'unknown push'})"
                status = "pending"
        decisions.append(
            Decision(
                repo=name,
                bucket=bucket,
                keeper="",
                evidence=evidence,
                status=status,
                is_private=bool(member.get("isPrivate")),
                is_archived=bool(member.get("isArchived")),
                is_fork=bool(member.get("isFork")),
                pushed_at=str(member.get("pushedAt") or ""),
                url=str(member.get("url") or ""),
                local_paths=paths,
            )
        )

    # Stable sort: Numerai keepers first, then by bucket, then name
    order = {
        "KEEP_ACTIVE": 0,
        "MERGE_THEN_ARCHIVE": 1,
        "ARCHIVE_AS_IS": 2,
        "LOCAL_ONLY_REVIEW": 3,
        "IGNORE_NOISE": 4,
    }
    decisions.sort(key=lambda d: (order[d.bucket], d.repo.lower()))
    return decisions


def load_local_remotes_from_radar(radar_dir: Path) -> dict[str, list[str]]:
    """Map github repo name / full_name -> local paths using radar inventory if present."""
    mapping: dict[str, list[str]] = {}
    inv = radar_dir / "repo_inventory.json"
    if not inv.exists():
        return mapping
    try:
        data = json.loads(inv.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return mapping
    records = data if isinstance(data, list) else data.get("repos") or data.get("records") or []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        path = str(rec.get("path") or rec.get("root") or rec.get("local_path") or "")
        remotes = rec.get("remotes") or rec.get("remote_urls") or []
        if isinstance(remotes, dict):
            remotes = list(remotes.values())
        names: list[str] = []
        for remote in remotes:
            remote_s = str(remote)
            m = re.search(r"github\.com[:/](?P<full>[^/]+/[^/.]+)(?:\.git)?", remote_s)
            if m:
                full = m.group("full")
                names.append(full)
                names.append(full.split("/")[-1])
        for name in names:
            mapping.setdefault(name, [])
            if path and path not in mapping[name]:
                mapping[name].append(path)
    return mapping


def write_decisions_md(out: Path, decisions: list[Decision], keepers: dict[str, str]) -> Path:
    lines = [
        "# Repo cleanup decisions",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Numerai keepers",
        "",
        f"- Classic: `{keepers.get('classic', '(none found)')}`",
        f"- Signals: `{keepers.get('signals', '(none found)')}`",
        f"- Crypto: `{keepers.get('crypto', '(none found)')}`",
        "",
        "## Decisions",
        "",
        (
            "| repo | bucket | keeper | private | archived | fork | pushed | "
            "evidence | status | local_paths |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in decisions:
        paths = "<br>".join(d.local_paths) if d.local_paths else ""
        lines.append(
            (
                "| {repo} | {bucket} | {keeper} | {priv} | {arch} | {fork} | {pushed} | "
                "{evidence} | {status} | {paths} |"
            ).format(
                repo=d.repo,
                bucket=d.bucket,
                keeper=d.keeper or "",
                priv=d.is_private,
                arch=d.is_archived,
                fork=d.is_fork,
                pushed=(d.pushed_at or "")[:10],
                evidence=d.evidence.replace("|", "/"),
                status=d.status,
                paths=paths.replace("|", "/"),
            )
        )
    lines.append("")
    path = out / "decisions.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "decisions.json").write_text(
        json.dumps([asdict(d) for d in decisions], indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {path}")
    return path


def extract_keepers(decisions: list[Decision]) -> dict[str, str]:
    keepers: dict[str, str] = {}
    for d in decisions:
        if d.bucket != "KEEP_ACTIVE":
            continue
        cluster = cluster_numerai(d.repo)
        if cluster in {"classic", "signals", "crypto"} and cluster not in keepers:
            keepers[cluster] = d.repo
        # If evidence names keeper cluster
        if "Numerai classic keeper" in d.evidence:
            keepers["classic"] = d.repo
        if "Numerai signals keeper" in d.evidence:
            keepers["signals"] = d.repo
        if "Numerai crypto keeper" in d.evidence:
            keepers["crypto"] = d.repo
    return keepers


def archive_repo(owner: str, name: str, *, apply: bool) -> tuple[bool, str]:
    full = f"{owner}/{name}"
    if not apply:
        return True, f"dry-run: would archive {full}"
    proc = run(["gh", "repo", "archive", full, "--yes"], check=False)
    if proc.returncode == 0:
        return True, f"archived {full}"
    err = (proc.stderr or proc.stdout or "").strip()
    return False, f"FAILED archive {full}: {err[:500]}"


def apply_archives(
    out: Path,
    owner: str,
    decisions: list[Decision],
    *,
    apply: bool,
    only_buckets: set[str],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for d in decisions:
        if d.is_archived:
            continue
        if d.bucket not in only_buckets:
            continue
        if d.bucket == "MERGE_THEN_ARCHIVE" and d.status != "merged":
            # Do not archive until unique content marked merged
            results.append(
                {
                    "repo": d.repo,
                    "ok": "false",
                    "detail": "skipped: MERGE_THEN_ARCHIVE not marked status=merged",
                }
            )
            continue
        ok, detail = archive_repo(owner, d.repo, apply=apply)
        results.append({"repo": d.repo, "ok": str(ok).lower(), "detail": detail})
        if ok and apply:
            d.status = "archived"
            d.is_archived = True
    (out / "archive_results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    return results


def quarantine_locals(out: Path, decisions: list[Decision], *, apply: bool) -> list[str]:
    notes: list[str] = []
    q = out / "quarantine"
    for d in decisions:
        if d.bucket not in {"MERGE_THEN_ARCHIVE", "ARCHIVE_AS_IS"}:
            continue
        if d.bucket == "MERGE_THEN_ARCHIVE" and d.status not in {"merged", "archived"}:
            continue
        if not d.is_archived and d.status != "archived":
            continue
        for path_s in d.local_paths:
            src = Path(path_s)
            if not src.exists():
                notes.append(f"missing local path {src}")
                continue
            dest = q / f"{d.repo}__{src.name}"
            if not apply:
                notes.append(f"dry-run: quarantine {src} -> {dest}")
                continue
            if dest.exists():
                notes.append(f"quarantine dest exists, skip: {dest}")
                continue
            shutil.move(str(src), str(dest))
            notes.append(f"quarantined {src} -> {dest}")
    (out / "quarantine_log.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return notes


def write_verification(out: Path, decisions: list[Decision], keepers: dict[str, str]) -> Path:
    active_numerai = [
        d
        for d in decisions
        if cluster_numerai(d.repo) in {"classic", "signals", "crypto"} and not d.is_archived
    ]
    lines = [
        "# Verification",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Keepers",
        f"- Classic: `{keepers.get('classic', '')}`",
        f"- Signals: `{keepers.get('signals', '')}`",
        f"- Crypto: `{keepers.get('crypto', '')}`",
        "",
        "## Checklist",
        f"- [ ] One non-archived Classic: {'YES' if keepers.get('classic') else 'NO — missing'}",
        f"- [ ] One non-archived Signals: "
        f"{'YES' if keepers.get('signals') else 'NO — none found (may be private-only pending)'}",
        f"- [ ] One non-archived Crypto: "
        f"{'YES' if keepers.get('crypto') else 'NO — none found (may be private-only pending)'}",
        f"- [ ] Active Numerai tournament remotes remaining: {len(active_numerai)}",
        "- [ ] Quarantine reviewed after 7 days",
        "- [ ] decisions.md statuses updated to done",
        "",
        "## Active Numerai-related remotes",
        "",
    ]
    for d in active_numerai:
        lines.append(f"- `{d.repo}` bucket={d.bucket} status={d.status}")
    lines.append("")
    path = out / "VERIFICATION.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--owner",
        default="joakimarvidsson",
        help="GitHub user/org to clean",
    )
    p.add_argument(
        "--root",
        action="append",
        type=Path,
        default=None,
        help="Local root for repo-radar (repeatable). Default: ~",
    )
    p.add_argument(
        "--repo-radar-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Checkout of repo-radar (for uv run)",
    )
    p.add_argument(
        "--cleanup-dir",
        type=Path,
        default=None,
        help="Working folder (default: ~/repo-cleanup-YYYY-MM-DD)",
    )
    p.add_argument(
        "--provisional-classic",
        default="numerai-classic-2026-master",
        help="Preferred Classic keeper name when present",
    )
    p.add_argument(
        "--skip-radar",
        action="store_true",
        help="Skip repo-radar handoff (GitHub-only classify)",
    )
    p.add_argument(
        "--skip-gate",
        action="store_true",
        help="Skip private-repo visibility gate (NOT recommended)",
    )
    p.add_argument(
        "--apply-archive",
        action="store_true",
        help="Actually run gh repo archive for ARCHIVE_AS_IS pending rows",
    )
    p.add_argument(
        "--apply-merge-archives",
        action="store_true",
        help="Also archive MERGE_THEN_ARCHIVE rows already marked status=merged",
    )
    p.add_argument(
        "--apply-quarantine",
        action="store_true",
        help="Move archived siblings' local paths into quarantine/",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = cleanup_dir(args.cleanup_dir)
    print(f"Cleanup dir: {out}")

    if not args.skip_gate:
        phase0_gate(out)
    else:
        print("WARNING: skipping private-repo gate", file=sys.stderr)

    roots = args.root or [Path.home()]
    if not args.skip_radar:
        run_repo_radar(args.repo_radar_root, roots, out)
    local_map = load_local_remotes_from_radar(out / "radar")

    repos = export_github(out, args.owner)
    decisions = classify_repos(
        repos,
        local_by_remote=local_map,
        provisional_classic=args.provisional_classic,
    )
    keepers = extract_keepers(decisions)
    # Ensure evidence-based keeper map for Numerai clusters
    for d in decisions:
        if d.bucket == "KEEP_ACTIVE" and "Numerai classic keeper" in d.evidence:
            keepers["classic"] = d.repo
        if d.bucket == "KEEP_ACTIVE" and "Numerai signals keeper" in d.evidence:
            keepers["signals"] = d.repo
        if d.bucket == "KEEP_ACTIVE" and "Numerai crypto keeper" in d.evidence:
            keepers["crypto"] = d.repo
    write_decisions_md(out, decisions, keepers)

    only: set[str] = set()
    if args.apply_archive:
        only.add("ARCHIVE_AS_IS")
    if args.apply_merge_archives:
        only.add("MERGE_THEN_ARCHIVE")
    if only:
        apply_archives(out, args.owner, decisions, apply=True, only_buckets=only)
        write_decisions_md(out, decisions, keepers)
    else:
        apply_archives(
            out,
            args.owner,
            decisions,
            apply=False,
            only_buckets={"ARCHIVE_AS_IS", "MERGE_THEN_ARCHIVE"},
        )

    quarantine_locals(out, decisions, apply=args.apply_quarantine)
    write_verification(out, decisions, keepers)

    print("\nDone. Next:")
    print(f"  1. Review {out / 'decisions.md'}")
    print("  2. Merge unique Numerai sibling content into keepers; set status=merged")
    print("  3. Re-run with --apply-archive and/or --apply-merge-archives")
    print("  4. Re-run with --apply-quarantine after remotes are archived")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
