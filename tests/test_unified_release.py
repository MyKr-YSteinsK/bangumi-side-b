"""Isolated release checks for exact unified-site publication."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bgm_side_b.release.site_publish import SitePublishError, UnifiedPublisher
from bgm_side_b.release.workflow import WorkflowError, _read_prepared


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


def _seed_pages(root: Path, remote: Path, message: str) -> None:
    pages = root.parent / "pages"
    _git(root.parent, "clone", "-q", str(remote), str(pages))
    (pages / "legacy.txt").write_text("legacy", "utf-8")
    _git(pages, "add", "legacy.txt")
    _git(
        pages,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        message,
    )
    _git(pages, "push", "-q", "origin", "HEAD:gh-pages")


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
    assert "release-report.json" not in tree.splitlines()
    message = _git(
        root, "--git-dir", str(remote), "log", "-1", "--format=%B", "gh-pages"
    ).splitlines()[0]
    assert message == (
        f"release: {run.release_version} [source {before_main[:12]}]"
    )


def test_release_version_uses_current_remote_commit_message(
    isolated_release: tuple[Path, Path],
) -> None:
    root, remote = isolated_release
    publisher = UnifiedPublisher(root)
    today = datetime.now(UTC).strftime("%Y.%m.%d")
    assert publisher._release_version("origin", "gh-pages") == f"{today}.1"

    _seed_pages(root, remote, f"release: {today}.1 [source {'0' * 12}]")
    assert publisher._release_version("origin", "gh-pages") == f"{today}.2"


@pytest.mark.parametrize("message", [
    "legacy release",
    "release: 2026.02.30.4 [source 000000000000]",
])
def test_release_version_resets_for_legacy_or_unparseable_remote_message(
    isolated_release: tuple[Path, Path], message: str
) -> None:
    root, remote = isolated_release
    publisher = UnifiedPublisher(root)
    today = datetime.now(UTC).strftime("%Y.%m.%d")
    _seed_pages(root, remote, message)
    assert publisher._release_version("origin", "gh-pages") == f"{today}.1"


def test_release_version_resets_for_a_different_day(
    isolated_release: tuple[Path, Path],
) -> None:
    root, remote = isolated_release
    publisher = UnifiedPublisher(root)
    previous = (datetime.now(UTC).date() - timedelta(days=1)).strftime("%Y.%m.%d")
    _seed_pages(root, remote, f"release: {previous}.9 [source {'0' * 12}]")
    today = datetime.now(UTC).strftime("%Y.%m.%d")
    assert publisher._release_version("origin", "gh-pages") == f"{today}.1"


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


def test_prepared_state_rejects_invalid_identity_and_scope_fields(
    isolated_release: tuple[Path, Path],
) -> None:
    root, _ = isolated_release
    state = root / "workspace" / "state" / "prepared-release.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    valid = {
        "schema": 2,
        "source_commit": "a" * 40,
        "app_version": "0.1.0",
        "candidate_content_hash": "b" * 64,
        "artifact_count": 1,
        "total_bytes": 1,
        "remote_gh_pages_commit": None,
        "prepared_at": "2026-08-12T00:00:00Z",
        "dry_run_report": "workspace/reports/release-publish.json",
        "public_quarters": ["2026-07"],
        "build_state_schema": 1,
    }
    invalid_values = (
        ("schema", 3),
        ("source_commit", "G" * 40),
        ("candidate_content_hash", "B" * 64),
        ("remote_gh_pages_commit", "G" * 40),
        ("dry_run_report", "C:/outside/report.json"),
        ("public_quarters", ["2026-02"]),
        ("public_quarters", ["2026-07", "2026-07"]),
        ("build_state_schema", 2),
    )
    for key, value in invalid_values:
        payload = {**valid, key: value}
        state.write_text(json.dumps(payload), "utf-8")
        with pytest.raises(WorkflowError, match="prepared release"):
            _read_prepared(root)
