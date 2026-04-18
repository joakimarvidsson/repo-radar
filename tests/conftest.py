from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def git_commit(path: Path, filename: str = "README.md", content: str = "# Test\n") -> None:
    (path / filename).parent.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2025-01-02T03:04:05+00:00",
        "GIT_COMMITTER_DATE": "2025-01-02T03:04:05+00:00",
    }
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=path, env=env, check=True, stdout=subprocess.PIPE
    )
