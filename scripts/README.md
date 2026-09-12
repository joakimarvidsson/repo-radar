# Full local + GitHub cleanup runner

Desktop / Monty helper that implements the archive-first consolidation playbook
(one Numerai Classic, one Signals, one Crypto; archive everything else safely).

## Prerequisites

- Run on a machine where `gh` is logged in as `joakimarvidsson` (or another
  account that can **list private repos** and **archive**).
- `uv` available in a checkout of this repository.
- Optional: existing local clones under `~` (or pass extra `--root` paths).

## Quick start

```bash
# Inventory + decisions only (safe; no archives)
uv run python scripts/full_repo_cleanup.py --root ~

# After reviewing ~/repo-cleanup-YYYY-MM-DD/decisions.md and merging
# unique Numerai sibling content into keepers:
uv run python scripts/full_repo_cleanup.py --root ~ --apply-archive

# After marking MERGE_THEN_ARCHIVE rows as status=merged in decisions.json:
uv run python scripts/full_repo_cleanup.py --root ~ --apply-merge-archives

# Move archived siblings' local clones into quarantine/
uv run python scripts/full_repo_cleanup.py --root ~ --apply-quarantine
```

## Outputs

Under `~/repo-cleanup-YYYY-MM-DD/` (override with `--cleanup-dir`):

| File | Purpose |
| --- | --- |
| `github_repos.json` | Full `gh repo list` including private |
| `radar/` | repo-radar handoff artifacts |
| `decisions.md` / `decisions.json` | Bucketed keep / merge / archive plan |
| `archive_results.json` | Dry-run or applied archive outcomes |
| `quarantine/` | Local clones moved after remote archive |
| `VERIFICATION.md` | End-state checklist |
| `BLOCKER.md` | Written only if private repos are invisible |

## Safety

- Pass 1 never runs `gh repo delete`.
- `MERGE_THEN_ARCHIVE` remotes are not archived until `status=merged`.
- Local deletes are quarantine moves only when `--apply-quarantine` is set.
- Default Classic keeper preference: `numerai-classic-2026-master`
  (`--provisional-classic`).
