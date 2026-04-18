from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from repo_radar.config import PackerSettings
from repo_radar.models import PackerResult, PriorityQueueItem, RepoRecord


class PackerStatus(BaseModel):
    backend: str
    available: bool
    executable: str | None = None
    command: list[str] = Field(default_factory=list)
    message: str


class PackerBackend(Protocol):
    def pack(
        self,
        repo_path: Path,
        output_path: Path,
        compressed: bool,
        dry_run: bool = False,
    ) -> PackerResult | dict[str, object]: ...


class RepomixPacker:
    def __init__(self, settings: PackerSettings | None = None):
        self.settings = settings or PackerSettings()

    def pack(
        self,
        repo_path: Path,
        output_path: Path,
        compressed: bool,
        dry_run: bool = False,
    ) -> PackerResult:
        command = self._command(output_path, compressed)
        if dry_run:
            return PackerResult(
                repo_path=str(repo_path),
                output_path=str(output_path),
                backend="repomix",
                compressed=compressed,
                dry_run=True,
                success=True,
                command=command,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not repo_path.exists():
            return PackerResult(
                repo_path=str(repo_path),
                output_path=str(output_path),
                backend="repomix",
                compressed=compressed,
                success=False,
                command=command,
                error="Repository path does not exist",
            )
        result = subprocess.run(
            command,
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
        return PackerResult(
            repo_path=str(repo_path),
            output_path=str(output_path),
            backend="repomix",
            compressed=compressed,
            success=result.returncode == 0,
            command=command,
            error=None if result.returncode == 0 else (result.stderr or result.stdout).strip(),
        )

    def _base_command(self) -> list[str]:
        return get_packer_status(self.settings).command or ["repomix"]

    def _command(self, output_path: Path, compressed: bool) -> list[str]:
        command = [
            *self._base_command(),
            "-o",
            str(output_path),
            "--style",
            self.settings.style,
            "--token-count-encoding",
            self.settings.token_count_encoding,
        ]
        if compressed:
            command.append("--compress")
        if not self.settings.security_check:
            command.append("--no-security-check")
        if self.settings.remove_comments:
            command.append("--remove-comments")
        if self.settings.remove_empty_lines:
            command.append("--remove-empty-lines")
        if self.settings.include_patterns:
            command.extend(["--include", ",".join(self.settings.include_patterns)])
        if self.settings.ignore_patterns:
            command.extend(["--ignore", ",".join(self.settings.ignore_patterns)])
        command.extend(self.settings.extra_args)
        return command


class Code2PromptPacker:
    def pack(
        self,
        repo_path: Path,
        output_path: Path,
        compressed: bool,
        dry_run: bool = False,
    ) -> PackerResult:
        return PackerResult(
            repo_path=str(repo_path),
            output_path=str(output_path),
            backend="code2prompt",
            compressed=compressed,
            dry_run=dry_run,
            success=False,
            command=[],
            error="Code2Prompt backend is reserved for future support",
        )


def pack_repositories(
    records: list[RepoRecord],
    output_dir: Path,
    packer: PackerBackend,
    compressed: bool,
    dry_run: bool = False,
    limit: int | None = None,
) -> list[PackerResult]:
    results: list[PackerResult] = []
    selected_records = records[:limit] if limit else records
    for record in selected_records:
        output_path = output_dir / f"{_safe_name(record.name or Path(record.path).name)}.xml"
        result = packer.pack(Path(record.path), output_path, compressed=compressed, dry_run=dry_run)
        results.append(_coerce_result(result))
    if not dry_run:
        write_pack_metadata(output_dir, results, compressed)
    return results


def pack_shortlisted_repos(
    records: list[RepoRecord],
    queue: list[PriorityQueueItem],
    output_dir: Path,
    packer: PackerBackend,
    compressed: bool,
    dry_run: bool = False,
) -> list[PackerResult]:
    by_path = {record.path: record for record in records}
    results: list[PackerResult] = []
    for item in queue:
        if not item.selected:
            continue
        record = by_path.get(item.path)
        if record is None:
            continue
        output_path = output_dir / f"{_safe_name(record.name or Path(record.path).name)}.xml"
        result = packer.pack(Path(record.path), output_path, compressed=compressed, dry_run=dry_run)
        results.append(_coerce_result(result))
    if not dry_run:
        write_pack_metadata(output_dir, results, compressed)
    return results


def create_packer(settings: PackerSettings) -> PackerBackend:
    if settings.backend == "code2prompt":
        return Code2PromptPacker()
    return RepomixPacker(settings)


def get_packer_status(settings: PackerSettings) -> PackerStatus:
    if settings.backend == "code2prompt":
        return PackerStatus(
            backend="code2prompt",
            available=False,
            message="Code2Prompt backend is reserved for future support",
        )
    if settings.command:
        command = shlex.split(settings.command)
        executable = command[0] if command else None
        available = bool(executable and shutil.which(executable))
        return PackerStatus(
            backend="repomix",
            available=available,
            executable=executable,
            command=command,
            message=(
                f"Configured packer command is available: {executable}"
                if available
                else f"Configured packer command was not found: {executable}"
            ),
        )
    if shutil.which("repomix"):
        return PackerStatus(
            backend="repomix",
            available=True,
            executable="repomix",
            command=["repomix"],
            message="repomix is available",
        )
    if shutil.which("npx"):
        return PackerStatus(
            backend="repomix",
            available=True,
            executable="npx",
            command=["npx", "--yes", "repomix@latest"],
            message="repomix will run through npx",
        )
    return PackerStatus(
        backend="repomix",
        available=False,
        executable=None,
        command=[],
        message="Install repomix or npx to enable digest and full pack generation",
    )


def write_pack_metadata(
    output_dir: Path,
    results: list[PackerResult],
    compressed: bool,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "pack_metadata.json"
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "compressed": compressed,
        "result_count": len(results),
        "success_count": sum(1 for result in results if result.success),
        "failure_count": sum(1 for result in results if not result.success),
        "results": [result.model_dump(mode="json") for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _safe_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in name)


def _coerce_result(result: PackerResult | dict[str, object]) -> PackerResult:
    if isinstance(result, PackerResult):
        return result
    return PackerResult.model_validate(result)
