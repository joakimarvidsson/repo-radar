# repo-radar Roadmap

## Near term

- Add a `state clear` or `state show` command for runtime state inspection.
- Expand GitHub reconciliation to batch requests and smarter cache refresh policies.
- Add dependency/lockfile similarity to duplicate detection.
- Add configurable recommendation weights.
- Add richer CLI smoke checks for generated artifact shape.

## Packer backends

- Implement the Code2Prompt backend behind the existing packer interface.
- Add packer capability checks so commands can explain missing tools before work starts.
- Add optional split-output handling for very large repositories.

## Source adapters

- Add read-only adapters for mounted network shares and bare Git clone directories.
- Improve SSH scanning with remote marker summaries and optional remote Git metadata.
- Add adapter-level concurrency with bounded worker pools.

## AI workflow

- Add prompt templates for follow-up agent analysis.
- Add token accounting based on actual Repomix output metadata where available.
- Add priority strategies for maintenance, migration, documentation, and security review.
- Add handoff profiles for maintenance, archival cleanup, and migration review.
- Add machine-readable recommendation summaries for downstream agents.

## Open-source readiness

- Add contribution guidelines and a security policy.
- Publish typed API documentation.
- Build release artifacts for PyPI.
- Add examples for common monorepo, homelab, and organization workspace layouts.
