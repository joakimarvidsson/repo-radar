# Security Policy

`repo-radar` is a local-first tool. It scans local directories and can optionally run
read-only SSH discovery commands when explicitly configured.

## Reporting a vulnerability

Please report security issues privately through GitHub security advisories if available,
or open a minimal issue that does not disclose exploitable details.

## Scope

Security-sensitive areas include:

- accidental secret inclusion in generated outputs
- unsafe shell command construction
- SSH discovery behavior
- path traversal or unsafe file writes
- GitHub CLI invocation behavior

## Current alpha notes

- Generated outputs may include local paths and repository names. Review outputs before
  sharing them publicly.
- Do not commit generated `outputs/*` files from real scans.
- SSH discovery is intended to be read-only; treat any write behavior as a bug.
