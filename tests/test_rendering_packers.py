from __future__ import annotations

import json

from repo_radar.config import PackerSettings
from repo_radar.models import PriorityQueueItem, RepoRecord
from repo_radar.packers import RepomixPacker, pack_shortlisted_repos
from repo_radar.rendering import render_agent_brief, render_groups, render_inventory
from repo_radar.shortlist import build_priority_queue


def test_render_inventory_groups_and_brief(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/a", name="a", project_type="python", maturity_score=80, file_count=10
        ),
        RepoRecord(
            path="/workspace/b", name="b", project_type="node", maturity_score=50, file_count=5
        ),
    ]
    outputs = tmp_path / "outputs"

    render_inventory(records, outputs)
    groups = render_groups(records, outputs)
    queue = build_priority_queue(records, token_budget=50_000, max_repos=5)
    brief_path = render_agent_brief(records, queue, groups, outputs)

    inventory = json.loads((outputs / "repo_inventory.json").read_text(encoding="utf-8"))
    assert inventory["repositories"][0]["name"] == "a"
    assert "python" in (outputs / "repo_inventory.md").read_text(encoding="utf-8")
    assert groups["by_project_type"]["python"]["count"] == 1
    assert "Inspect first" in brief_path.read_text(encoding="utf-8")


def test_repomix_packer_dry_run_uses_npx_fallback_and_compress(monkeypatch, tmp_path):
    def fake_which(name: str):
        return None if name == "repomix" else f"/usr/bin/{name}"

    monkeypatch.setattr("repo_radar.packers.shutil.which", fake_which)
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "digest.xml"
    packer = RepomixPacker(PackerSettings(ignore_patterns=["dist/**"], include_patterns=["src/**"]))

    result = packer.pack(repo, output, compressed=True, dry_run=True)

    assert result.success is True
    assert result.dry_run is True
    assert result.command[:3] == ["npx", "--yes", "repomix@latest"]
    assert "--compress" in result.command
    assert "--ignore" in result.command
    assert "dist/**" in result.command
    assert "--include" in result.command


def test_pack_shortlisted_repos_only_packs_selected_items(monkeypatch, tmp_path):
    calls: list[str] = []

    class FakePacker:
        def pack(self, repo_path, output_path, compressed, dry_run):
            calls.append(str(repo_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            return {
                "repo_path": str(repo_path),
                "output_path": str(output_path),
                "backend": "fake",
                "compressed": compressed,
                "dry_run": dry_run,
                "success": True,
                "command": [],
                "error": None,
            }

    selected_repo = tmp_path / "selected"
    skipped_repo = tmp_path / "skipped"
    selected_repo.mkdir()
    skipped_repo.mkdir()
    records = [
        RepoRecord(path=str(selected_repo), name="selected"),
        RepoRecord(path=str(skipped_repo), name="skipped"),
    ]
    queue = [
        PriorityQueueItem(
            rank=1, name="selected", path=str(selected_repo), score=90, selected=True
        ),
        PriorityQueueItem(rank=2, name="skipped", path=str(skipped_repo), score=10, selected=False),
    ]

    results = pack_shortlisted_repos(
        records, queue, tmp_path / "fullpacks", FakePacker(), compressed=False
    )

    assert calls == [str(selected_repo)]
    assert len(results) == 1
