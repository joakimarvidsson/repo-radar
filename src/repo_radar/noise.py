from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from repo_radar.models import RepoRecord


class NoiseClass(StrEnum):
    USER_PROJECT = "USER_PROJECT"
    SYSTEM_OR_VENDOR = "SYSTEM_OR_VENDOR"
    CACHE_OR_PACKAGE_STORE = "CACHE_OR_PACKAGE_STORE"
    GENERATED_OR_EPHEMERAL = "GENERATED_OR_EPHEMERAL"
    EDITOR_EXTENSION = "EDITOR_EXTENSION"
    NOTEBOOK_CHECKPOINT = "NOTEBOOK_CHECKPOINT"
    UNKNOWN = "UNKNOWN"


class NoiseClassification(BaseModel):
    noise_class: NoiseClass = NoiseClass.USER_PROJECT
    reasons: list[str] = Field(default_factory=list)
    suppressed: bool = False


CACHE_SEGMENTS = {
    ".bun",
    ".npm",
    ".pnpm-store",
    ".cache",
    "_npx",
    "node_modules",
}

EDITOR_EXTENSION_PATTERNS = {
    (".cursor", "extensions"),
    (".vscode", "extensions"),
    (".antigravity", "extensions"),
}

NOTEBOOK_SEGMENTS = {".ipynb_checkpoints", ".virtual_documents"}

SYSTEM_SEGMENTS = {
    "Library",
    "Applications",
    "Movies",
    "Music",
    "Pictures",
    "Trash",
    ".Trash",
}

GENERATED_SEGMENTS = {
    ".tmp",
    "tmp",
    "temp",
    "dist",
    "build",
    "target",
    "outputs",
    "generated",
    ".repo-radar",
}

VENDOR_PATTERNS = {
    ("plugins", "marketplaces"),
    ("plugin", "marketplaces"),
    ("go", "pkg", "mod"),
}

NOISE_SUPPRESSION_RULES = [
    ".bun/install/cache",
    ".npm",
    ".pnpm-store",
    ".cache",
    ".cursor/extensions",
    ".vscode/extensions",
    ".antigravity/extensions",
    "plugins/marketplaces",
    ".ipynb_checkpoints",
    ".virtual_documents",
    "tmp/temp/generated directories",
    "package version cache directories",
]


def classify_noise(path: str | Path, broad_scan: bool = False) -> NoiseClassification:
    path_obj = Path(path)
    parts = path_obj.parts
    reasons: list[str] = []

    if _contains_pair(parts, EDITOR_EXTENSION_PATTERNS):
        reasons.append("editor extension path")
        return NoiseClassification(
            noise_class=NoiseClass.EDITOR_EXTENSION,
            reasons=reasons,
            suppressed=True,
        )

    if any(part in NOTEBOOK_SEGMENTS for part in parts):
        reasons.append("notebook checkpoint or virtual document path")
        return NoiseClassification(
            noise_class=NoiseClass.NOTEBOOK_CHECKPOINT,
            reasons=reasons,
            suppressed=True,
        )

    if _is_package_cache(parts):
        reasons.append("package cache path")
        return NoiseClassification(
            noise_class=NoiseClass.CACHE_OR_PACKAGE_STORE,
            reasons=reasons,
            suppressed=True,
        )

    if any(part in SYSTEM_SEGMENTS for part in parts) or _contains_sequence(parts, VENDOR_PATTERNS):
        reasons.append("system, vendor, or plugin marketplace path")
        return NoiseClassification(
            noise_class=NoiseClass.SYSTEM_OR_VENDOR,
            reasons=reasons,
            suppressed=broad_scan,
        )

    if any(part in GENERATED_SEGMENTS for part in parts) or _looks_like_version_cache(
        path_obj.name
    ):
        reasons.append("generated, temporary, or package-version directory")
        return NoiseClassification(
            noise_class=NoiseClass.GENERATED_OR_EPHEMERAL,
            reasons=reasons,
            suppressed=broad_scan,
        )

    return NoiseClassification(noise_class=NoiseClass.USER_PROJECT)


def classify_record_noise(record: RepoRecord, broad_scan: bool) -> RepoRecord:
    classification = classify_noise(record.path, broad_scan=broad_scan)
    return record.model_copy(
        update={
            "noise_class": classification.noise_class.value,
            "noise_reasons": classification.reasons,
            "suppressed": classification.suppressed,
        },
        deep=True,
    )


def filter_noise(
    records: list[RepoRecord],
    broad_scan: bool,
    include_noise: bool,
) -> list[RepoRecord]:
    classified = [classify_record_noise(record, broad_scan=broad_scan) for record in records]
    if include_noise:
        return classified
    return [record for record in classified if not record.suppressed]


def is_noise_path(path: str | Path) -> bool:
    return classify_noise(path, broad_scan=True).suppressed


def _is_package_cache(parts: tuple[str, ...]) -> bool:
    if any(part in {".npm", ".pnpm-store"} for part in parts):
        return True
    if ".bun" in parts and "install" in parts and "cache" in parts:
        return True
    if "_npx" in parts and ".npm" in parts:
        return True
    return any(part == "node_modules" for part in parts)


def _contains_pair(parts: tuple[str, ...], patterns: set[tuple[str, str]]) -> bool:
    lowered = tuple(part for part in parts)
    return any(
        first in lowered and second in lowered[lowered.index(first) + 1 :]
        for first, second in patterns
    )


def _contains_sequence(parts: tuple[str, ...], patterns: set[tuple[str, ...]]) -> bool:
    for pattern in patterns:
        for index in range(0, len(parts) - len(pattern) + 1):
            if parts[index : index + len(pattern)] == pattern:
                return True
    return False


def _looks_like_version_cache(name: str) -> bool:
    return "@@@" in name or any(token in name for token in ["@1.", "@2.", "@3.", "@4."])
