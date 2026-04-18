from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from repo_radar.models import DuplicateCluster, RepoRecord

REASON_WEIGHTS = {
    "normalized-remote": 95,
    "same-git-root": 95,
    "manifest-name": 80,
    "readme-hash": 75,
    "readme-title": 65,
    "top-level-signature": 60,
    "basename-variant": 55,
    "basename-structural-similarity": 50,
    "normalized-basename": 50,
    "manifest-similarity": 40,
}


def normalize_remote_url(url: str) -> str | None:
    clean = url.strip()
    if not clean:
        return None

    ssh_match = re.match(r"^git@(?P<host>[^:]+):(?P<path>.+)$", clean)
    if ssh_match:
        host = ssh_match.group("host").lower()
        path = ssh_match.group("path")
        return _remote_key(host, path)

    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https", "ssh"} and parsed.netloc:
        host = parsed.netloc.lower()
        if "@" in host:
            host = host.split("@", 1)[1]
        return _remote_key(host, parsed.path.lstrip("/"))
    return None


def assign_duplicate_clusters(
    records: list[RepoRecord],
) -> tuple[list[RepoRecord], list[DuplicateCluster]]:
    updated = [record.model_copy(deep=True) for record in records]
    parent = list(range(len(updated)))
    reasons_by_pair: dict[tuple[int, int], set[str]] = defaultdict(set)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int, reason: str) -> None:
        if left == right:
            return
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
        reasons_by_pair[tuple(sorted((left, right)))].add(reason)

    _union_by_key(updated, union, _remote_keys, "normalized-remote")
    _union_by_key(updated, union, _resolved_path_keys, "same-git-root")
    _union_by_key(updated, union, _manifest_keys, "manifest-name")
    _union_by_key(updated, union, _readme_keys, "readme-hash")
    _union_by_key(updated, union, _readme_title_keys, "readme-title")
    _union_by_key(updated, union, _signature_keys, "top-level-signature")

    for left in range(len(updated)):
        for right in range(left + 1, len(updated)):
            left_name = updated[left].name or Path(updated[left].path).name
            right_name = updated[right].name or Path(updated[right].path).name
            left_normalized = normalize_basename(left_name)
            right_normalized = normalize_basename(right_name)
            if left_normalized == right_normalized and left_normalized:
                reason = (
                    "basename-variant"
                    if (updated[left].name or "") != (updated[right].name or "")
                    else "normalized-basename"
                )
                if _structural_similarity(updated[left], updated[right]) >= 0.45:
                    union(left, right, reason)
            if _same_basename_structural_similarity(updated[left], updated[right]):
                union(left, right, "basename-structural-similarity")
            if _manifest_similarity(updated[left], updated[right]) >= 0.6 and (
                left_normalized == right_normalized
                or _readme_title_normalized(updated[left])
                == _readme_title_normalized(updated[right])
            ):
                union(left, right, "manifest-similarity")

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(updated)):
        components[find(index)].append(index)

    cluster_components = [
        sorted(indexes, key=lambda index: updated[index].path)
        for indexes in components.values()
        if len(indexes) > 1
    ]
    cluster_components.sort(key=lambda indexes: updated[indexes[0]].path)

    clusters: list[DuplicateCluster] = []
    for number, indexes in enumerate(cluster_components, start=1):
        cluster_id = f"dup-{number:03d}"
        reasons = _component_reasons(indexes, reasons_by_pair)
        confidence = _cluster_confidence(reasons)
        canonical_index = _canonical_index(updated, indexes)
        canonical_path = updated[canonical_index].path
        paths = sorted(updated[index].path for index in indexes)
        names = sorted({updated[index].name or Path(updated[index].path).name for index in indexes})
        for index in indexes:
            updated[index].duplicate_cluster_id = cluster_id
            updated[index].duplicate_signals = reasons
            updated[index].duplicate_confidence = confidence
            updated[index].duplicate_canonical_path = canonical_path
            updated[index].likely_canonical = index == canonical_index
        clusters.append(
            DuplicateCluster(
                id=cluster_id,
                paths=paths,
                names=names,
                reasons=reasons,
                confidence=confidence,
                canonical_path=canonical_path,
            )
        )

    return updated, clusters


def _union_by_key(records, union, key_func, reason: str) -> None:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        for key in key_func(record):
            groups[key].append(index)
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        for offset, left in enumerate(indexes):
            for right in indexes[offset + 1 :]:
                union(left, right, reason)


def _remote_keys(record: RepoRecord) -> list[str]:
    if not record.git:
        return []
    return sorted(
        key for url in record.git.remotes.values() if (key := normalize_remote_url(url)) is not None
    )


def _resolved_path_keys(record: RepoRecord) -> list[str]:
    path = Path(record.path).expanduser()
    try:
        return [str(path.resolve())]
    except OSError:
        return [str(path)]


def _manifest_keys(record: RepoRecord) -> list[str]:
    return [f"manifest:{name.lower()}" for name in record.manifest_names if len(name) > 1]


def _readme_keys(record: RepoRecord) -> list[str]:
    return [f"readme:{record.readme_hash}"] if record.readme_hash else []


def _readme_title_keys(record: RepoRecord) -> list[str]:
    title = _readme_title_normalized(record)
    return [f"readme-title:{title}"] if title else []


def _signature_keys(record: RepoRecord) -> list[str]:
    if not record.top_level_signature or record.top_level_signature.count("|") < 2:
        return []
    return [f"signature:{record.top_level_signature}"]


def _same_basename_structural_similarity(left: RepoRecord, right: RepoRecord) -> bool:
    if (left.name or "").lower() != (right.name or "").lower():
        return False
    if left.project_type != right.project_type:
        return False
    return _structural_similarity(left, right) >= 0.6


def _structural_similarity(left: RepoRecord, right: RepoRecord) -> float:
    left_signals = set(left.key_directories) | set(left.markers) | set(left.primary_languages)
    right_signals = set(right.key_directories) | set(right.markers) | set(right.primary_languages)
    if not left_signals or not right_signals:
        return 0.0
    overlap = len(left_signals & right_signals)
    denominator = max(len(left_signals), len(right_signals))
    return overlap / denominator if denominator else 0.0


def _manifest_similarity(left: RepoRecord, right: RepoRecord) -> float:
    left_manifests = {marker for marker in left.markers if _is_manifest_marker(marker)}
    right_manifests = {marker for marker in right.markers if _is_manifest_marker(marker)}
    if not left_manifests or not right_manifests:
        return 0.0
    return len(left_manifests & right_manifests) / max(len(left_manifests), len(right_manifests))


def _component_reasons(
    indexes: list[int],
    reasons_by_pair: dict[tuple[int, int], set[str]],
) -> list[str]:
    reasons: set[str] = set()
    index_set = set(indexes)
    for (left, right), pair_reasons in reasons_by_pair.items():
        if left in index_set and right in index_set:
            reasons.update(pair_reasons)
    return sorted(reasons)


def _remote_key(host: str, path: str) -> str:
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    return f"{host}/{normalized_path}".lower()


def normalize_basename(name: str) -> str:
    normalized = name.lower().strip()
    normalized = re.sub(r"[\s_]+", "-", normalized)
    normalized = re.sub(r"(\.git)$", "", normalized)
    normalized = re.sub(
        r"[-_](old|backup|bak|copy|archive|archived|wip|tmp|temp)(-\d+)?$",
        "",
        normalized,
    )
    normalized = re.sub(r"[-_](19|20)\d{2}([-_]?\d{2})?([-_]?\d{2})?$", "", normalized)
    normalized = re.sub(r"[-_]\d{8}$", "", normalized)
    normalized = re.sub(r"[-_]+$", "", normalized)
    return normalized


def _readme_title_normalized(record: RepoRecord) -> str | None:
    if not record.readme_title:
        return None
    return re.sub(r"\s+", " ", record.readme_title.lower()).strip()


def _is_manifest_marker(marker: str) -> bool:
    return marker in {
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
    }


def _cluster_confidence(reasons: list[str]) -> int:
    return min(100, sum(REASON_WEIGHTS.get(reason, 20) for reason in set(reasons)))


def _canonical_index(records: list[RepoRecord], indexes: list[int]) -> int:
    return max(
        indexes,
        key=lambda index: (_canonical_score(records[index]), _path_sort_key(records[index])),
    )


def _canonical_score(record: RepoRecord) -> int:
    name = record.name or Path(record.path).name
    normalized = normalize_basename(name)
    suffix_penalty = 0 if normalized == name.lower().replace("_", "-") else -25
    remote_bonus = 20 if record.git and record.git.remotes else 0
    structure_bonus = min(20, len(record.key_directories) * 5)
    return (
        record.maturity_score
        + record.classification_confidence
        + remote_bonus
        + structure_bonus
        + suffix_penalty
    )


def _path_sort_key(record: RepoRecord) -> str:
    return "~" + record.path
