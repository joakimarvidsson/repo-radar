# repo-radar

**Public alpha:** `repo-radar` is usable today, but the CLI, scoring heuristics, and
output schemas may still change before a stable release.

`repo-radar` is a local-first command-line tool for finding repositories and
repo-like folders on a messy development machine, grouping and prioritizing them, and
producing compact AI-ready handoff files.

It is built for people who have many local projects, old experiments, cloned repos,
monorepos, package caches, and editor/plugin directories mixed together. The default
workflow works without a config file.

## What it does

- Discovers Git repositories and repo-like folders from local roots.
- Works with zero config: `repo-radar scan`, `repo-radar handoff`, and
  `repo-radar inventory` use saved state or sensible local auto-discovery.
- Supports explicit roots such as `--root ~/projects` or broad scans such as `--root ~`.
- Suppresses obvious broad-scan noise like package caches, editor extensions,
  notebook checkpoints, generated folders, plugin marketplaces, and system folders.
- Distinguishes monorepo roots, monorepo subprojects, standalone projects, and
  container directories.
- Detects likely duplicates with explainable signals and confidence.
- Produces JSON and Markdown outputs for humans and AI agents.
- Uses Repomix as the default backend for compressed and full AI-ready repo packs.
- Optionally reconciles GitHub remotes through `gh` when it is installed and authenticated.

## Quickstart

The recommended alpha workflow is to run from source with Python 3.12+ and `uv`.
`repo-radar` is not yet published as a stable package.

```bash
git clone https://github.com/joakimarvidsson/repo-radar.git
cd repo-radar
uv sync
uv run repo-radar --version
uv run repo-radar doctor
uv run repo-radar scan --dry-run
uv run repo-radar handoff
```

Scan a specific folder:

```bash
uv run repo-radar scan --root ~/projects
uv run repo-radar handoff --root ~/projects
```

Scan your home directory with broad-scan suppression enabled:

```bash
uv run repo-radar doctor --root ~
uv run repo-radar scan --dry-run --root ~
uv run repo-radar handoff --root ~
```

Include normally suppressed cache/vendor/generated entries only when you explicitly
want to inspect them:

```bash
uv run repo-radar scan --dry-run --root ~ --include-noise
```

## Staged workflow

`repo-radar` is designed to avoid packing everything blindly:

```text
inventory -> digest -> shortlist -> full pack -> agent brief / handoff
```

Common commands:

```bash
uv run repo-radar inventory
uv run repo-radar digest
uv run repo-radar shortlist
uv run repo-radar pack
uv run repo-radar brief
uv run repo-radar handoff
```

`agent_handoff.md` is the compact AI-facing artifact. `agent_brief.md` is a fuller
summary.

## Configuration

Config is optional. Without `--config`, runtime source precedence is:

```text
CLI flags > saved local state > auto-discovery
```

Use `repo_radar.yaml` only when you want a reusable configuration:

```bash
cp examples/repo_radar.sample.yaml repo_radar.yaml
uv run repo-radar scan --config repo_radar.yaml
```

CLI overrides are usually enough:

```bash
uv run repo-radar scan --root /path/to/workspace
uv run repo-radar scan --root /path/one --root /path/two
uv run repo-radar scan --ssh user@example.invalid --ssh-root /srv/projects
```

Last-used local roots and SSH settings are saved in a local state file. The default is:

```text
~/.local/state/repo-radar/state.json
```

Set `REPO_RADAR_STATE_PATH` in tests or automation to override that location.

## Broad scans

Broad roots such as `--root ~` are allowed, but they are treated carefully.
`repo-radar` warns before broad scans, bounds traversal, and excludes noisy directories
such as:

- `.git`, `.venv`, `node_modules`, `.cache`
- `.bun`, `.npm`, `.pnpm-store`
- `.cursor`, `.vscode`, `.antigravity`
- `Library`, `Downloads`, `Movies`, `Music`, `Pictures`, `Applications`, `Trash`
- `.ipynb_checkpoints`, `.virtual_documents`
- generated `outputs`, `build`, `dist`, and `target`

The aim is to surface real user projects first. Use `--include-noise` for forensic
inspection of suppressed content.

## Monorepos and duplicates

Nested projects inside a shared Git root are treated as monorepo relationships, not
automatic duplicates. Records can be labeled as:

- `MONOREPO_ROOT`
- `MONOREPO_SUBPROJECT`
- `CONTAINER_DIRECTORY`
- `STANDALONE_PROJECT`

Duplicate clusters use explainable signals such as normalized remotes, manifest names,
README hashes/titles, top-level signatures, basename variants, and structural similarity.
Monorepo subprojects are protected from weak duplicate signals so normal app/package
layouts do not become misleading merge candidates.

## Outputs

Generated outputs live under `outputs/`:

- `repo_inventory.json`
- `repo_inventory.md`
- `repo_groups.json`
- `repo_priority_queue.json`
- `repo_digests/`
- `repo_fullpacks/`
- `agent_brief.md`
- `agent_handoff.md`

Generated output files are ignored by Git. The repository keeps only `outputs/.gitkeep`
so the output directory exists in fresh checkouts.

## GitHub reconciliation

When enabled, GitHub reconciliation parses GitHub remotes and uses `gh repo view`.
If `gh` is missing or unauthenticated, scanning still works and reconciliation fields
record why live GitHub data was unavailable.

GitHub lookup results are cached by default under:

```text
outputs/.cache/github_reconciliation.json
```

## Current limitations

- This is an alpha. Output schema details and scoring weights may change.
- Duplicate and recommendation logic is heuristic, not a proof of repository identity.
- SSH discovery is intentionally shallow and read-only.
- Code2Prompt is not integrated yet; Repomix is the default packer.
- No PyPI package is published yet; run from source for now.
- Broad scans are tuned for practical local machines, not exhaustive filesystem forensics.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format . --check
uv run python -m compileall src tests
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md).
