from __future__ import annotations

import json
from pathlib import Path

from repo_radar.installed_audit import (
    build_installed_audit_report,
    classify_installed_repo,
)
from repo_radar.installed_audit_rendering import render_installed_audit_outputs
from repo_radar.models import GitMetadata, RepoRecord


def _record(
    path: str,
    *,
    remote: str | None = "https://github.com/acme/tool.git",
    current_branch: str | None = "main",
    upstream_branch: str | None = "origin/main",
    ahead: int | None = 0,
    behind: int | None = 0,
    divergence_status: str | None = "in_sync",
    has_uncommitted_changes: bool = False,
    changed_files: int = 0,
    untracked_files: int = 0,
) -> RepoRecord:
    return RepoRecord(
        path=path,
        name=Path(path).name,
        source_type="local",
        source_name="local",
        is_git=True,
        git=GitMetadata(
            remotes={} if remote is None else {"origin": remote},
            git_root=path,
            current_branch=current_branch,
            upstream_branch=upstream_branch,
            ahead=ahead,
            behind=behind,
            divergence_status=divergence_status,
            has_uncommitted_changes=has_uncommitted_changes,
            changed_files=changed_files,
            untracked_files=untracked_files,
        ),
    )


def test_installed_repo_classification_covers_clean_behind_ahead_and_diverged_states():
    clean = classify_installed_repo(_record("/workspace/clean"))
    behind = classify_installed_repo(
        _record("/workspace/behind", behind=3, divergence_status="behind")
    )
    ahead = classify_installed_repo(_record("/workspace/ahead", ahead=2, divergence_status="ahead"))
    diverged = classify_installed_repo(
        _record("/workspace/diverged", ahead=1, behind=2, divergence_status="diverged")
    )

    assert clean.sync_status == "in_sync"
    assert clean.likely_safe_to_update is False
    assert clean.needs_manual_review is False

    assert behind.sync_status == "behind"
    assert behind.likely_safe_to_update is True
    assert behind.needs_manual_review is False
    assert any("pull --ff-only" in command for command in behind.suggested_commands)

    assert ahead.sync_status == "ahead"
    assert ahead.likely_safe_to_update is False
    assert ahead.needs_manual_review is True

    assert diverged.sync_status == "diverged"
    assert diverged.needs_manual_review is True


def test_installed_repo_classification_flags_non_github_remote_for_manual_review():
    record = classify_installed_repo(
        _record(
            "/workspace/internal-tool",
            remote="git@example.invalid:tools/internal-tool.git",
            behind=2,
            divergence_status="behind",
        )
    )

    assert record.remote_category == "non_github_remote"
    assert record.likely_safe_to_update is False
    assert record.needs_manual_review is True
    assert not any("pull --ff-only" in command for command in record.suggested_commands)


def test_installed_repo_classification_handles_missing_remote():
    record = classify_installed_repo(
        _record(
            "/workspace/local-only",
            remote=None,
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        )
    )

    assert record.remote_category == "no_remote"
    assert record.sync_status == "no_remote"
    assert record.needs_manual_review is True


def test_installed_audit_live_mode_respects_limit_and_tracks_skips():
    records = [
        _record("/workspace/one"),
        _record("/workspace/two"),
        _record("/workspace/three"),
    ]
    refreshed: list[str] = []

    def fake_refresher(record: RepoRecord) -> tuple[RepoRecord, str | None]:
        refreshed.append(record.path)
        updated_git = record.git.model_copy(update={"behind": 1, "divergence_status": "behind"})
        return record.model_copy(update={"git": updated_git}), None

    report = build_installed_audit_report(
        records,
        live=True,
        live_limit=2,
        refresher=fake_refresher,
    )

    assert refreshed == ["/workspace/one", "/workspace/two"]
    assert report.summary.live_checks_performed == 2
    assert report.summary.live_checks_skipped == 1
    assert report.summary.live_check_limit == 2
    assert any("capped" in warning for warning in report.warnings)


def test_installed_audit_rendering_writes_markdown_json_and_optional_plan(tmp_path):
    records = [
        _record("/workspace/behind", behind=2, divergence_status="behind"),
        _record(
            "/workspace/dirty",
            has_uncommitted_changes=True,
            changed_files=2,
            untracked_files=1,
        ),
        _record(
            "/workspace/no-remote",
            remote=None,
            upstream_branch=None,
            ahead=None,
            behind=None,
            divergence_status=None,
        ),
    ]
    report = build_installed_audit_report(records, live=False)

    outputs = render_installed_audit_outputs(report, tmp_path, write_plan=True)

    assert outputs["markdown"] == tmp_path / "installed_audit.md"
    assert outputs["json"] == tmp_path / "installed_audit.json"
    assert outputs["plan"] == tmp_path / "update_plan.md"

    markdown = (tmp_path / "installed_audit.md").read_text(encoding="utf-8")
    assert "Installed Tools Freshness Audit" in markdown
    assert "Advisory only" in markdown
    assert "Likely Safe To Update" in markdown
    assert "Behind Remote" in markdown
    assert "Dirty Working Trees" in markdown
    assert "No Remote" in markdown
    assert "Manual Review" in markdown

    payload = json.loads((tmp_path / "installed_audit.json").read_text(encoding="utf-8"))
    assert payload["summary"]["behind_remote"] == 1
    assert payload["summary"]["dirty_worktrees"] == 1
    assert payload["summary"]["no_remote"] == 1
    assert payload["safe_to_update"][0]["path"] == "/workspace/behind"
