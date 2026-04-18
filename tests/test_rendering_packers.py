from __future__ import annotations

import json

from repo_radar.config import PackerSettings
from repo_radar.models import PriorityQueueItem, RepoRecord
from repo_radar.packers import RepomixPacker, pack_repositories, pack_shortlisted_repos
from repo_radar.rendering import render_agent_brief, render_groups, render_inventory
from repo_radar.shortlist import build_priority_queue


def test_render_inventory_groups_and_brief(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/a",
            name="a",
            project_type="python",
            maturity_score=80,
            file_count=10,
            recommendation_labels=["KEEP"],
            likely_canonical=True,
        ),
        RepoRecord(
            path="/workspace/b",
            name="b",
            project_type="node",
            maturity_score=50,
            file_count=5,
            duplicate_cluster_id="dup-001",
            recommendation_labels=["DUPLICATE_OF", "MERGE_CANDIDATE"],
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
    assert "## Action Groups" in (outputs / "repo_inventory.md").read_text(encoding="utf-8")
    assert "Likely primary / canonical repos" in (outputs / "repo_inventory.md").read_text(
        encoding="utf-8"
    )
    assert groups["by_project_type"]["python"]["count"] == 1
    assert groups["action_groups"]["MERGE_CANDIDATE"]["count"] == 1
    assert groups["canonical_repos"]["count"] == 1
    assert "Inspect first" in brief_path.read_text(encoding="utf-8")


def test_render_groups_includes_monorepo_families_and_containers(tmp_path):
    records = [
        RepoRecord(
            path="/workspace/mono",
            name="mono",
            relationship_labels=["MONOREPO_ROOT"],
        ),
        RepoRecord(
            path="/workspace/mono/apps/api",
            name="api",
            relationship_labels=["MONOREPO_SUBPROJECT"],
            monorepo_root_path="/workspace/mono",
        ),
        RepoRecord(
            path="/workspace/Projects",
            name="Projects",
            relationship_labels=["CONTAINER_DIRECTORY"],
        ),
    ]

    groups = render_groups(records, tmp_path)
    inventory_md = tmp_path / "repo_inventory.md"
    render_inventory(records, tmp_path)
    content = inventory_md.read_text(encoding="utf-8")

    assert groups["monorepo_families"][0]["root_path"] == "/workspace/mono"
    assert groups["container_directories"]["count"] == 1
    assert "Monorepo families" in content
    assert "Container directories" in content


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


def test_pack_repositories_writes_metadata_manifest(tmp_path):
    class FakePacker:
        def pack(self, repo_path, output_path, compressed, dry_run):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("packed", encoding="utf-8")
            return {
                "repo_path": str(repo_path),
                "output_path": str(output_path),
                "backend": "fake",
                "compressed": compressed,
                "dry_run": dry_run,
                "success": True,
                "command": ["fake-pack"],
                "error": None,
            }

    repo = tmp_path / "repo"
    repo.mkdir()
    output_dir = tmp_path / "digests"

    results = pack_repositories(
        [RepoRecord(path=str(repo), name="repo")],
        output_dir,
        FakePacker(),
        compressed=True,
    )

    manifest = json.loads((output_dir / "pack_metadata.json").read_text(encoding="utf-8"))
    assert len(results) == 1
    assert manifest["schema_version"] == "1.0"
    assert manifest["compressed"] is True
    assert manifest["result_count"] == 1
    assert manifest["success_count"] == 1
    assert manifest["results"][0]["command"] == ["fake-pack"]


def test_pack_repositories_dry_run_does_not_write_metadata(tmp_path):
    class FakePacker:
        def pack(self, repo_path, output_path, compressed, dry_run):
            return {
                "repo_path": str(repo_path),
                "output_path": str(output_path),
                "backend": "fake",
                "compressed": compressed,
                "dry_run": dry_run,
                "success": True,
                "command": ["fake-pack"],
                "error": None,
            }

    repo = tmp_path / "repo"
    repo.mkdir()
    output_dir = tmp_path / "digests"

    pack_repositories(
        [RepoRecord(path=str(repo), name="repo")],
        output_dir,
        FakePacker(),
        compressed=True,
        dry_run=True,
    )

    assert not (output_dir / "pack_metadata.json").exists()
