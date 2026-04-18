from __future__ import annotations

from repo_radar.models import RepoRecord
from repo_radar.noise import NoiseClass, classify_noise, filter_noise


def test_classify_noise_identifies_common_broad_scan_junk():
    cases = {
        "/home/user/.bun/install/cache/react@18.0.0@@@1": NoiseClass.CACHE_OR_PACKAGE_STORE,
        "/home/user/.npm/_npx/abc123": NoiseClass.CACHE_OR_PACKAGE_STORE,
        "/home/user/.pnpm-store/v3/files": NoiseClass.CACHE_OR_PACKAGE_STORE,
        "/home/user/.cursor/extensions/ms-python.python": NoiseClass.EDITOR_EXTENSION,
        "/home/user/.vscode/extensions/vendor.extension": NoiseClass.EDITOR_EXTENSION,
        "/home/user/project/.ipynb_checkpoints": NoiseClass.NOTEBOOK_CHECKPOINT,
        "/home/user/.virtual_documents/project": NoiseClass.NOTEBOOK_CHECKPOINT,
        "/home/user/.claude/plugins/marketplaces/plugin": NoiseClass.SYSTEM_OR_VENDOR,
        "/home/user/projects/real-app": NoiseClass.USER_PROJECT,
    }

    for path, expected in cases.items():
        result = classify_noise(path, broad_scan=True)
        assert result.noise_class == expected, path


def test_filter_noise_suppresses_noise_by_default_and_preserves_on_override():
    records = [
        RepoRecord(path="/home/user/projects/app", name="app"),
        RepoRecord(path="/home/user/.bun/install/cache/pkg@1.0.0", name="pkg"),
    ]

    default_records = filter_noise(records, broad_scan=True, include_noise=False)
    include_noise_records = filter_noise(records, broad_scan=True, include_noise=True)

    assert [record.name for record in default_records] == ["app"]
    assert len(include_noise_records) == 2
    assert include_noise_records[1].suppressed is True
    assert include_noise_records[1].noise_class == "CACHE_OR_PACKAGE_STORE"
