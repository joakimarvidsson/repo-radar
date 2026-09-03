"""Unit tests for cleanup classification heuristics (no network)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "full_repo_cleanup.py"


@pytest.fixture(scope="module")
def cleanup():
    spec = importlib.util.spec_from_file_location("full_repo_cleanup", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cluster_numerai(cleanup):
    assert cleanup.cluster_numerai("numerai-classic-2026-master") == "classic"
    assert cleanup.cluster_numerai("Numerai-Tournament") == "classic"
    assert cleanup.cluster_numerai("numerai_signals_pipeline") == "signals"
    assert cleanup.cluster_numerai("numerai-crypto") == "crypto"
    assert cleanup.cluster_numerai("numerapi") == "misc"
    assert cleanup.cluster_numerai("repo-radar") is None


def test_provisional_classic_wins(cleanup):
    repos = [
        {
            "name": "Numerai-Tournament",
            "isPrivate": False,
            "isArchived": False,
            "isFork": False,
            "pushedAt": "2025-02-04T00:00:00Z",
            "diskUsage": 100,
            "url": "https://github.com/joakimarvidsson/Numerai-Tournament",
            "description": "",
        },
        {
            "name": "numerai-classic-2026-master",
            "isPrivate": True,
            "isArchived": False,
            "isFork": False,
            "pushedAt": "2026-08-01T00:00:00Z",
            "diskUsage": 5000,
            "url": "https://github.com/joakimarvidsson/numerai-classic-2026-master",
            "description": "AI Harness",
        },
        {
            "name": "numerai_xgb_eb",
            "isPrivate": False,
            "isArchived": False,
            "isFork": False,
            "pushedAt": "2021-10-29T00:00:00Z",
            "diskUsage": 50,
            "url": "https://github.com/joakimarvidsson/numerai_xgb_eb",
            "description": "",
        },
    ]
    decisions = cleanup.classify_repos(
        repos,
        local_by_remote={},
        provisional_classic="numerai-classic-2026-master",
    )
    by_name = {d.repo: d for d in decisions}
    assert by_name["numerai-classic-2026-master"].bucket == "KEEP_ACTIVE"
    assert by_name["Numerai-Tournament"].bucket == "MERGE_THEN_ARCHIVE"
    assert by_name["Numerai-Tournament"].keeper == "numerai-classic-2026-master"
    assert by_name["numerai_xgb_eb"].bucket == "MERGE_THEN_ARCHIVE"


def test_already_archived_is_done(cleanup):
    repos = [
        {
            "name": "numerapi",
            "isPrivate": False,
            "isArchived": True,
            "isFork": True,
            "pushedAt": "2023-08-17T00:00:00Z",
            "diskUsage": 10,
            "url": "https://github.com/joakimarvidsson/numerapi",
            "description": "",
        }
    ]
    decisions = cleanup.classify_repos(
        repos, local_by_remote={}, provisional_classic="numerai-classic-2026-master"
    )
    assert decisions[0].bucket == "ARCHIVE_AS_IS"
    assert decisions[0].status == "done"


def test_keep_name_beats_fork(cleanup):
    repos = [
        {
            "name": "typed-python-zero-to-hero",
            "isPrivate": False,
            "isArchived": False,
            "isFork": True,
            "pushedAt": "2026-08-25T00:00:00Z",
            "diskUsage": 100,
            "url": "https://github.com/joakimarvidsson/typed-python-zero-to-hero",
            "description": "workshop",
        }
    ]
    decisions = cleanup.classify_repos(
        repos, local_by_remote={}, provisional_classic="numerai-classic-2026-master"
    )
    assert decisions[0].bucket == "KEEP_ACTIVE"
