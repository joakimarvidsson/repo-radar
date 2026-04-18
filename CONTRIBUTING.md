# Contributing to repo-radar

Thanks for considering a contribution. `repo-radar` is in public alpha, so small,
well-tested changes are preferred.

## Development setup

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format . --check
uv run python -m compileall src tests
```

## Contribution guidelines

- Keep the tool local-first and generic.
- Do not commit personal paths, private hostnames, generated broad-scan outputs, or secrets.
- Add tests for behavior changes.
- Keep broad-scan rules practical and explainable.
- Prefer small, reviewable patches over large rewrites.

## Reporting issues

When reporting a scan-quality issue, include:

- command run
- operating system
- whether a config file was used
- sanitized example paths
- expected vs actual classification or recommendation

Avoid attaching full inventories if they contain private paths or project names.
