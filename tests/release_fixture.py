"""Reusable current-schema project fixture for isolated release workflow tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bgm_side_b.build.site_builder import UnifiedSiteBuilder
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database
from tests.test_site_builder import _build_fixture

ROOT = Path(__file__).parents[1]


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def create_release_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a clean current-schema project with a local origin bare remote."""
    seed = tmp_path / "seed"
    _build_fixture(seed)
    root = tmp_path / "project"
    root.mkdir()
    shutil.copytree(ROOT / "config", root / "config")
    shutil.copytree(ROOT / "static", root / "static")
    shutil.copytree(seed / "workspace", root / "workspace")
    database_path = root / "workspace" / "data" / "archive.sqlite3"
    database_path.rename(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    (root / "dist").mkdir()
    (root / ".gitignore").write_text("dist/\nworkspace/\n", encoding="utf-8")
    (root / "README.md").write_text("release fixture\n", encoding="utf-8")

    git(root, "init", "-q")
    git(root, "checkout", "-q", "-b", "main")
    git(root, "add", "-A")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "-u", "origin", "main")
    return root, remote


def make_builder(root: Path) -> UnifiedSiteBuilder:
    database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    tags = load_tag_rules(
        root / "config" / "allowed-tags.toml",
        root / "config" / "tag-aliases.toml",
    )
    return UnifiedSiteBuilder(
        root,
        database,
        tags,
        workspace_directory=root / "workspace",
        site_directory=root / "dist" / "site",
        reports_directory=root / "workspace" / "reports",
    )
