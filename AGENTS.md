# AGENTS.md

## Build and test
- Use Python 3.12
- Install dependencies with: `uv sync`
- Run tests with: `uv run pytest`
- Run lint with: `uv run ruff check .`
- Validate imports/bytecode with: `uv run python -m compileall src tests`

## Review expectations
- Prefer narrow PRs
- Do not commit generated outputs from `outputs/` except `outputs/.gitkeep`
- Do not commit local runtime artifacts, caches, `.omx/`, or secrets
- Preserve zero-config behavior unless the task explicitly changes it
- Prefer fixes with tests when behavior changes

## Repo conventions
- Keep CLI UX simple and local-first
- Avoid adding databases, hosted backends, or unnecessary services
- Treat monorepo/container labeling separately from duplicate detection
