"""Isolated release checks for exact unified-site publication."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bgm_side_b.release.site_publish import SitePublishError, UnifiedPublisher


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _site(root: Path) -> None:
    files = (
        "index.html",
        "archive/index.html",
        "settings/index.html",
        "assets/app.css",
        "assets/app.js",
        "assets/pwa.js",
        "manifest.webmanifest",
        "sw.js",
        "data/pwa-shell.json",
        "2026-07/index.html",
        "data/quarters/2026-07.json",
        "data/offline/2026-07.json",
    )
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "data/archive-index.json":
            continue
        path.write_text(relative, "utf-8")
    (root / "data" / "archive-index.json").write_text(
        json.dumps({"quarters": [{"quarter": "2026-07"}]}), "utf-8"
    )


@pytest.fixture
def isolated_release(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    _site(root / "dist" / "site")
    (root / "workspace" / "data").mkdir(parents=True)
    artifacts: dict[str, str] = {}
    sizes: dict[str, int] = {}
    from hashlib import sha256

    for path in (root / "dist" / "site").rglob("*"):
        if path.is_file():
            relative = path.relative_to(root / "dist" / "site").as_posix()
            content = path.read_bytes()
            artifacts[relative] = sha256(content).hexdigest()
            sizes[relative] = len(content)
    (root / "workspace" / "build-state.json").write_text(
        json.dumps({"schema": 1, "artifacts": artifacts, "artifact_sizes": sizes}),
        "utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    (root / "README.md").write_text("fixture", "utf-8")
    _git(root, "add", "README.md")
    _git(
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
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    return root, remote


def test_publish_dry_run_and_exact_tree_update(
    isolated_release: tuple[Path, Path],
) -> None:
    root, remote = isolated_release
    publisher = UnifiedPublisher(root)

    dry_run = publisher.publish(dry_run=True)
    assert not dry_run.published
    assert dry_run.report_path.is_file()
    missing = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "show-ref",
            "--verify",
            "refs/heads/gh-pages",
        ],
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0

    before_main = _git(root, "rev-parse", "HEAD")
    run = publisher.publish()
    assert run.published and run.remote_commit
    assert _git(root, "rev-parse", "HEAD") == before_main
    tree = _git(
        root, "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "gh-pages"
    )
    assert tree
    assert "index.html" in _git(
        root, "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "gh-pages"
    )


def test_publish_fails_closed_when_bound_remote_or_tree_changes(
    isolated_release: tuple[Path, Path],
) -> None:
    root, _ = isolated_release
    publisher = UnifiedPublisher(root)
    candidate = publisher.candidate()
    with pytest.raises(SitePublishError, match="dist/site changed"):
        publisher.publish(expected_content_hash="0" * 64)
    with pytest.raises(SitePublishError, match="gh-pages changed"):
        publisher.publish(expected_remote_commit="1" * 40)
    assert candidate.identity.content_hash
