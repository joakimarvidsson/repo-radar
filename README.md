# repo-radar

`repo-radar` is a local-first CLI for discovering repositories and repo-like project
folders, classifying them, reconciling local Git state with GitHub when possible, and
producing token-efficient AI-ready outputs for later use by models and agents.

It is designed to be generic and open-source-friendly. No personal paths, hostnames,
or usernames are required in the repository. Environment-specific inputs live in
command-line options, saved local runtime state, or an optional `repo_radar.yaml`.

## Why repo-radar exists

AI agents often need a compact view of many repositories before deciding where to spend
context. Packing every repository in full is expensive and noisy. `repo-radar` stages
the workflow so agents can inspect progressively:

```text
inventory -> digest -> shortlist -> full pack -> agent brief
```

- Inventory records what exists and what each folder probably is.
- Digests use compressed Repomix output first, reducing tokens while preserving structure.
- Shortlists choose the most useful repositories under a token budget.
- Full packs are generated only for shortlisted repositories.
- Agent briefs summarize what to inspect first, where duplicates or drift might exist,
  and which repositories need follow-up.

## Features

- Discover Git repositories and repo-like folders across configurable local roots.
- Optionally discover remote folders over SSH through a generic source adapter.
- Detect common project markers such as `pyproject.toml`, `requirements.txt`,
  `package.json`, `Cargo.toml`, `go.mod`, `src/`, `notebooks/`, and `.github/`.
- Extract Git metadata, remotes, current branch, default branch, last commit date,
  dirty status, ahead/behind status, languages, file counts, size, and key directories.
- Reconcile GitHub remotes with the `gh` CLI when available and authenticated.
- Detect likely orphan local repositories, remote mismatches, renamed repositories,
  and public/private visibility when GitHub allows it.
- Cluster probable duplicates with explainable signals from remotes, manifests,
  folder signatures, README hashes, and structural similarity.
- Rank repositories with inspectable positive and negative scoring factors.
- Generate JSON, Markdown, priority queues, compressed digests, full packs, and agent briefs.
- Generate compact AI handoff notes for a coding agent or model.
- Support dry runs for discovery and packing commands.
- Use Repomix as the default AI-friendly packer backend.
- Keep a backend seam for optional Code2Prompt support later.

## Installation

Use Python 3.12 or newer.

```bash
uv sync
uv run repo-radar --help
```

For editable development without `uv`:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
repo-radar --help
```

## Configuration

No config file is required for normal use:

```bash
repo-radar scan
repo-radar handoff
repo-radar inventory
```

When no `--config` is supplied, `repo-radar` uses CLI inputs first, then saved local
runtime state, then automatic local root discovery. If the current working directory
already looks like a project, it scans that directory and warns that only the current
project is being scanned. Otherwise it checks common developer directories such as
`~/projects`, `~/Projects`, `~/code`, `~/Code`, `~/github`, `~/GitHub`, and `~/Documents`.

Create a config only when you want a reusable checked-in or shared setup:

```bash
cp examples/repo_radar.sample.yaml repo_radar.yaml
```

Edit roots and optional SSH sources:

```yaml
local_roots:
  - /path/to/workspace

ssh_sources:
  - name: staging-box
    host: example.invalid
    user: deploy
    roots:
      - /srv/projects
    enabled: false
```

Keep secrets out of config. SSH authentication should use your normal SSH agent or
read-only deploy keys.

`repo-radar` remembers last-used roots and SSH inputs in a local runtime state file.
By default this is under `~/.local/state/repo-radar/state.json`, or under
`$XDG_STATE_HOME/repo-radar/state.json` when `XDG_STATE_HOME` is set. Tests and
automation can override it with `REPO_RADAR_STATE_PATH`.

Precedence is:

```text
CLI flags > explicit --config > saved state > auto-discovery
```

## CLI workflow

Run the whole staged flow without writing outputs:

```bash
repo-radar scan --dry-run
```

Scan a specific local root without editing config:

```bash
repo-radar scan --root /path/to/workspace
repo-radar inventory --root /path/to/workspace --root /path/to/another/workspace
```

Force or disable auto-discovery:

```bash
repo-radar scan --auto
repo-radar doctor --no-auto
```

Add an SSH source from the CLI:

```bash
repo-radar scan --ssh user@example.invalid --ssh-root /srv/projects
```

Write inventory files:

```bash
repo-radar inventory --config repo_radar.yaml
```

Reconcile local Git remotes against GitHub:

```bash
repo-radar reconcile --config repo_radar.yaml
```

Create compressed Repomix digests:

```bash
repo-radar digest --config repo_radar.yaml
```

Build a token-budget-aware queue:

```bash
repo-radar shortlist --config repo_radar.yaml --token-budget 200000 --limit 10
```

Create full packs only for shortlisted repositories:

```bash
repo-radar pack --config repo_radar.yaml
```

Write an agent brief:

```bash
repo-radar brief --config repo_radar.yaml
```

Write a compact AI handoff:

```bash
repo-radar handoff --config repo_radar.yaml
```

Or run the staged flow in one command:

```bash
repo-radar scan --config repo_radar.yaml
repo-radar scan --config repo_radar.yaml --pack
```

Validate configuration and local tool availability:

```bash
repo-radar doctor
repo-radar config-check --config repo_radar.yaml
repo-radar doctor --config repo_radar.yaml
```

## Outputs

`repo-radar` writes:

- `outputs/repo_inventory.json`
- `outputs/repo_inventory.md`
- `outputs/repo_groups.json`
- `outputs/repo_priority_queue.json`
- `outputs/repo_digests/`
- `outputs/repo_fullpacks/`
- `outputs/agent_brief.md`
- `outputs/agent_handoff.md`

Digest and full-pack directories also receive `pack_metadata.json` manifests with packer
commands, success/failure counts, and per-repository result records.

`agent_brief.md` and `agent_handoff.md` include the effective scan source mode and roots
used for that run, so downstream agents can see whether results came from CLI flags,
explicit config, saved state, or auto-discovery.

Generated output files are ignored by git except for placeholders and the sample brief.

## Repomix backend

Repomix is the default packer because it can produce AI-oriented XML, Markdown, JSON,
or plain-text repository packs, respects ignore rules, includes security checks, and
supports compression with `--compress`.

`repo-radar digest` uses compressed Repomix packs by default. `repo-radar pack` creates
full packs for shortlisted repositories only.

If `repomix` is installed, `repo-radar` uses it. Otherwise it falls back to:

```bash
npx --yes repomix@latest
```

Relevant Repomix options are configured under `packer:` in `repo_radar.yaml`.

## GitHub reconciliation

When `github.enabled` is true, `repo-radar` parses GitHub SSH/HTTPS remotes and uses
`gh repo view` when the GitHub CLI is available and authenticated.

It records whether the repository exists, its visibility, the default branch, whether
the local remote identity matches GitHub, and whether a local repository looks orphaned.

If `gh` is missing or unauthenticated, inventory still works. Reconciliation fields record
the reason GitHub metadata could not be checked.

GitHub lookups are cached by default under `outputs/.cache/github_reconciliation.json`.
Set `github.cache_ttl_seconds` to control refresh cadence, or disable caching with:

```yaml
github:
  cache_enabled: false
```

## Duplicate detection and scoring

Duplicate detection is deterministic and explainable. Repo records can receive a
`duplicate_cluster_id` and `duplicate_signals` when practical heuristics agree:

- normalized Git remote URL match
- same resolved local path discovered through different roots
- same manifest/package name
- same README content hash
- same top-level folder signature
- same basename with strong structural similarity

Shortlist scoring writes a `score_breakdown` for every ranked repo in
`outputs/repo_priority_queue.json`. Positive factors include maturity, recent activity,
clean Git state, source/test/docs structure, classification confidence, and packability.
Negative factors include duplicate penalties, GitHub drift, orphan remotes, stale repos,
and incomplete project signals.

## SSH scanning

SSH sources are optional and disabled by default. When enabled, the adapter runs read-only
remote commands using `ssh`, `find`, marker checks, and `du`. It does not clone, modify,
or write to remote hosts.

Use conservative roots and max depth values:

```yaml
ssh_sources:
  - name: remote-example
    host: example.invalid
    roots:
      - /srv/projects
    max_depth: 4
    enabled: true
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -m compileall src tests
uv run repo-radar doctor
uv run repo-radar scan --dry-run
uv run repo-radar handoff
```

## Open-source roadmap

See `docs/ROADMAP.md` for planned improvements around richer source adapters,
Code2Prompt support, better duplicate detection, richer token accounting, and CI packaging.
