from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from repo_radar.models import DuplicateCluster, RepoRecord


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
    _union_by_key(updated, union, _signature_keys, "top-level-signature")

    for left in range(len(updated)):
        for right in range(left + 1, len(updated)):
            if _same_basename_structural_similarity(updated[left], updated[right]):
                union(left, right, "basename-structural-similarity")

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
        paths = sorted(updated[index].path for index in indexes)
        names = sorted({updated[index].name or Path(updated[index].path).name for index in indexes})
        for index in indexes:
            updated[index].duplicate_cluster_id = cluster_id
            updated[index].duplicate_signals = reasons
        clusters.append(DuplicateCluster(id=cluster_id, paths=paths, names=names, reasons=reasons))

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


def _signature_keys(record: RepoRecord) -> list[str]:
    if not record.top_level_signature or record.top_level_signature.count("|") < 2:
        return []
    return [f"signature:{record.top_level_signature}"]


def _same_basename_structural_similarity(left: RepoRecord, right: RepoRecord) -> bool:
    if (left.name or "").lower() != (right.name or "").lower():
        return False
    if left.project_type != right.project_type:
        return False
    left_signals = set(left.key_directories) | set(left.markers) | set(left.primary_languages)
    right_signals = set(right.key_directories) | set(right.markers) | set(right.primary_languages)
    if not left_signals or not right_signals:
        return False
    overlap = len(left_signals & right_signals)
    denominator = max(len(left_signals), len(right_signals))
    return denominator > 0 and overlap / denominator >= 0.6


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
