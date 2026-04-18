# repo-radar Roadmap

## Near term

- Expand GitHub reconciliation to batch requests and cache successful lookups.
- Add richer duplicate detection using remote identity, normalized names, and manifest metadata.
- Add richer CLI smoke checks after the command surface stabilizes.

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

## Open-source readiness

- Add contribution guidelines and a security policy.
- Publish typed API documentation.
- Build release artifacts for PyPI.
- Add examples for common monorepo, homelab, and organization workspace layouts.
