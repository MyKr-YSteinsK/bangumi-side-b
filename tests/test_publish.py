"""Local-bare-remote coverage for manual, transactional Pages publication."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from io import StringIO
from pathlib import Path

import pytest

from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.progress import ConsoleProgressReporter
from bgm_side_b.release.candidate import advance_data_generation
from bgm_side_b.release.publish import Publisher, PublishError, _allowed_origin
from bgm_side_b.repository import (
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)

ROOT = Path(__file__).parents[1]


@pytest.fixture
def publish_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    for name in ("config", "static", "templates"):
        shutil.copytree(ROOT / name, root / name)
    (root / "CHANGELOG.md").write_text("## Unreleased\n\n- PWA release flow\n", "utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0'\n", "utf-8"
    )
    (root / ".gitignore").write_text("workspace/\ndist/\n", "utf-8")
    _git(root, "init")
    _git(root, "checkout", "-b", "main")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(root, "remote", "add", "test", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    workspace = root / "workspace"
    database = Database(workspace / "data" / "bangumi-side-b.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.upsert_subject(
            connection, SubjectRecord(101, "tv", None, date(2026, 4, 1), 12, 7.0, 1)
        )
        repository.replace_titles(
            connection, 101, [SubjectTitle("preferred", "发布测试")]
        )
        repository.replace_infobox(
            connection, 101, [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", "Japan")]
        )
        repository.replace_quarters(connection, 101, [SubjectQuarter(2026, 4, "new")])
    settings, tags, sources = load_rules(root / "config")
    ArchiveBuilder(root, database, settings, tags, sources).build(None)
    return root, remote


def test_publish_dry_run_and_local_bare_remote_transaction(
    publish_root: tuple[Path, Path],
) -> None:
    root, remote = publish_root
    (root / "workspace" / "data" / "bangumi-side-b.sqlite3").unlink()
    stream = StringIO()
    with ConsoleProgressReporter("publish", mode="plain", stream=stream) as reporter:
        dry_run = Publisher(root, reporter).publish(dry_run=True, remote="test")
    assert dry_run.dry_run and not dry_run.published
    assert dry_run.report_path.is_file()
    output = stream.getvalue()
    assert output.count("[发布") >= 14
    assert "正在读取远端 gh-pages" in output
    assert "snapshot manifest 已生成" in output
    assert str(root) not in output
    publish_stream = StringIO()
    with ConsoleProgressReporter(
        "publish", mode="plain", stream=publish_stream
    ) as reporter:
        first = Publisher(root, reporter).publish(remote="test")
    assert first.published and first.remote_commit
    assert "正在创建临时 worktree" in publish_stream.getvalue()
    assert "即将推送 release" in publish_stream.getvalue()
    publisher = Publisher(root)
    assert _git(root, "branch", "--show-current") == "main"
    assert _git(root, "--git-dir", str(remote), "show", "gh-pages:release.json")
    assert (root / "workspace" / "releases" / "history.json").is_file()
    snapshot = (root / "workspace" / "releases" / "current-snapshot.json").read_text(
        "utf-8"
    )
    assert '"101"' in snapshot
    with pytest.raises(PublishError, match="no publishable changes"):
        publisher.publish(remote="test")


def test_publish_refuses_changed_facts_and_unsafe_origin_branch(
    publish_root: tuple[Path, Path],
) -> None:
    root, _ = publish_root
    publisher = Publisher(root)
    with pytest.raises(PublishError, match="gh-pages only"):
        publisher.publish(dry_run=True, remote="origin", branch="main")
    advance_data_generation(root / "workspace")
    with pytest.raises(PublishError, match="facts changed"):
        publisher.publish(dry_run=True, remote="test")


def test_allowed_origin_requires_an_exact_repository_url() -> None:
    assert _allowed_origin("https://github.com/MyKr-YSteinsK/bangumi-side-b.git")
    assert _allowed_origin("git@github.com:MyKr-YSteinsK/bangumi-side-b.git")
    assert not _allowed_origin("https://github.com/other/MyKr-YSteinsK/bangumi-side-b.git")
    assert not _allowed_origin("https://github.com/MyKr-YSteinsK/bangumi-side-b.git?x=1")


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
