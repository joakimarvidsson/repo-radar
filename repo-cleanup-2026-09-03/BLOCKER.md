# BLOCKER: Full-local+GitHub repo cleanup aborted

**Status:** STOPPED before Phase 0. No quarantine, archive, delete, or merge performed.

## Machine identity check (required: Monty or Joakim's desktop)

| Check | Expected | Actual | Pass? |
|-------|----------|--------|-------|
| `hostname` | Monty (or Joakim desktop) | `cursor` | **NO** |
| `whoami` | Joakim (or local user) | `ubuntu` | **NO** |
| `$HOME` | Joakim home on Monty | `/home/ubuntu` | **NO** |
| `~/projects`, `~/Projects`, `~/code` | Present with local repos | None found (dirs missing/empty) | **NO** |
| Environment | Physical/local Monty | Cursor Cloud Agent VM (`/workspace`) | **NO** |

## GitHub private-repo access check

| Check | Expected | Actual | Pass? |
|-------|----------|--------|-------|
| `gh auth` account | `joakimarvidsson` (or token with private access) | `cursor` (ghs_ cloud-agent token) | **NO** |
| Private repos in `gh repo list joakimarvidsson` | At least some `isPrivate: true` | **All 10 returned repos are public** (`isPrivate: false`) | **NO** |

### Sample `gh repo list` (first 10)

```json
[
  {"isPrivate": false, "name": "repo-radar"},
  {"isPrivate": false, "name": "pi-apes-7"},
  {"isPrivate": false, "name": "typed-python-zero-to-hero"},
  {"isPrivate": false, "name": "beeware"},
  {"isPrivate": false, "name": "ai-agents-workshop-v4"},
  {"isPrivate": false, "name": "loop-library"},
  {"isPrivate": false, "name": "pipelinebench"},
  {"isPrivate": false, "name": "ai-agents-workshop-python"},
  {"isPrivate": false, "name": "jan"},
  {"isPrivate": false, "name": "cursorfy"}
]
```

## Why this blocks Phases 0–5

1. **Wrong machine:** Cleanup requires local quarantine under `~/repo-cleanup-2026-09-03/`, repo-radar scan of `~`, and duplicate-local quarantine. This host has no Joakim home tree (`~/projects` / `~/Projects` / `~/code` absent).
2. **No private visibility:** Numerai Classic / Signals / Crypto keepers and sibling archives depend on seeing private repos. Current token only surfaces public forks/repos.
3. **Safety rule:** Instructions require writing `BLOCKER.md` and stopping if not on Monty / no private access. Archiving from this VM would be incorrect and irreversible against the wrong scope.

## Actions NOT taken

- No `gh repo archive`
- No local quarantine moves
- No fork merges
- No `decisions.md` / `VERIFICATION.md` (plan not started)
- No Numerai keeper selection

## Unblock checklist (run on Monty / Joakim desktop)

1. SSH/login to Monty (or Joakim's desktop).
2. Confirm: `hostname` matches Monty (or known desktop hostname).
3. Authenticate as Joakim: `gh auth login` (or use Joakim PAT with `repo` scope).
4. Confirm private access: `gh repo list joakimarvidsson --limit 20 --json name,isPrivate` shows `isPrivate: true` entries.
5. Re-run Full-local+GitHub repo cleanup plan Phases 0–5 from that machine only.

## Artifact path

- This file: `repo-cleanup-2026-09-03/BLOCKER.md`
