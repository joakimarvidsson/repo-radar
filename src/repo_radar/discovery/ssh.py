from __future__ import annotations

import shlex
import subprocess
from typing import Any

from repo_radar.config import SSHSourceConfig
from repo_radar.models import DiscoveredProject


class SSHSourceAdapter:
    def __init__(self, ignore_patterns: list[str] | None = None):
        self.ignore_patterns = ignore_patterns or []

    def source_model(self, raw: SSHSourceConfig | dict[str, Any]) -> SSHSourceConfig:
        if isinstance(raw, SSHSourceConfig):
            return raw
        return SSHSourceConfig.model_validate(raw)

    def build_discovery_command(self, source: SSHSourceConfig, root: str) -> list[str]:
        max_depth = source.max_depth if source.max_depth is not None else 5
        marker_checks = [
            '[ -d "$d/.git" ]',
            '[ -f "$d/pyproject.toml" ]',
            '[ -f "$d/requirements.txt" ]',
            '[ -f "$d/package.json" ]',
            '[ -f "$d/Cargo.toml" ]',
            '[ -f "$d/go.mod" ]',
            '[ -d "$d/src" ]',
            '[ -d "$d/notebooks" ]',
            'find "$d" -maxdepth 1 -name "*.ipynb" -type f | grep -q .',
        ]
        remote_script = (
            "set -eu; "
            f"find {shlex.quote(root)} -maxdepth {max_depth} -type d "
            "! -path '*/.git/*' ! -path '*/node_modules/*' ! -path '*/.venv/*' "
            "| while IFS= read -r d; do "
            f"if {' || '.join(marker_checks)}; then "
            'kind="repo_like"; [ -d "$d/.git" ] && kind="git"; '
            'kb=$(du -sk "$d" 2>/dev/null | awk \'{print $1}\' || printf "0"); '
            'printf "%s|%s|%s\\n" "$kind" "$d" "$((kb * 1024))"; '
            "fi; done"
        )
        command = ["ssh"]
        if source.port:
            command.extend(["-p", str(source.port)])
        command.extend([source.target, remote_script])
        return command

    def parse_rows(self, output: str, source_name: str = "ssh") -> list[DiscoveredProject]:
        rows: list[DiscoveredProject] = []
        for line in output.splitlines():
            parts = line.split("|")
            if len(parts) != 3:
                continue
            kind, path, size = parts
            try:
                size_bytes = int(size)
            except ValueError:
                size_bytes = 0
            rows.append(
                DiscoveredProject(
                    path=path,
                    source_type="ssh",
                    source_name=source_name,
                    is_git=kind == "git",
                    is_repo_like=True,
                    markers=[".git/"] if kind == "git" else [],
                    estimated_size_bytes=size_bytes,
                )
            )
        return rows

    def discover(self, source: SSHSourceConfig, dry_run: bool = False) -> list[DiscoveredProject]:
        if not source.enabled:
            return []
        discovered: list[DiscoveredProject] = []
        for root in source.roots:
            command = self.build_discovery_command(source, root)
            if dry_run:
                continue
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=source.timeout_seconds,
            )
            if result.returncode == 0:
                discovered.extend(self.parse_rows(result.stdout, source.name))
        return discovered
