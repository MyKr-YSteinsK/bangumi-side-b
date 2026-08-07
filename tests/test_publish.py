"""Local-bare-remote coverage for manual, transactional Pages publication."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import date
from io import StringIO
from pathlib import Path

import pytest

from bgm_side_b import __version__
from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.progress import ConsoleProgressReporter
from bgm_side_b.release import publish as publish_module
from bgm_side_b.release.candidate import (
    advance_data_generation,
    mark_data_generation_dirty,
)
from bgm_side_b.release.manifest import build_snapshot_manifest, manifest_json
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
    (root / "CHANGELOG.md").write_text(
        "## 尚未发布\n\n### 调整\n\n- PWA 发布流程\n", "utf-8"
    )
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
    report = json.loads(dry_run.report_path.read_text("utf-8"))
    assert report["app_version"] == __version__
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


def test_release_staging_keeps_nojekyll_out_of_the_snapshot(
    publish_root: tuple[Path, Path],
) -> None:
    root, _ = publish_root
    staging = root / "staging"
    shutil.copytree(root / "dist" / "pages", staging)
    (staging / ".nojekyll").write_text("", encoding="utf-8")
    entries = publish_module.index_candidate(staging, "/bangumi-side-b/")
    manifest = build_snapshot_manifest(
        entries,
        release_version="2026.07.30.2",
        app_version="0.1.0",
        deployment_path="/bangumi-side-b/",
    )
    manifest_text = manifest_json(manifest)
    (staging / "snapshot-manifest.json").write_text(manifest_text, encoding="utf-8")
    release = {
        "schema": 1,
        "release_version": "2026.07.30.2",
        "app_version": "0.1.0",
        "generated_at": "2026-07-31T00:00:00Z",
        "published_at": "2026-07-31T00:00:00Z",
        "quarter_count": 1,
        "subject_count": 1,
        "total_bytes": manifest.payload()["total_bytes"],
        "content_hash": manifest.content_hash,
        "manifest_url": "snapshot-manifest.json",
        "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        "change_kind": "system",
        "summary": {"system": ["PWA hotfix"], "data": []},
    }
    Publisher(root)._validate_staging(staging, manifest, release)
    assert all(".nojekyll" not in item.url for item in entries)
    (staging / ".nojekyll").unlink()
    with pytest.raises(PublishError, match="lacks .nojekyll"):
        Publisher(root)._validate_staging(staging, manifest, release)


def test_publish_refuses_a_partial_or_interrupted_sync(
    publish_root: tuple[Path, Path],
) -> None:
    root, _ = publish_root
    marker = json.loads(
        (root / "workspace" / "state" / "pages-build.json").read_text("utf-8")
    )
    mark_data_generation_dirty(root / "workspace")

    with pytest.raises(PublishError, match="sync failed or was interrupted"):
        Publisher(root)._validate_data_generation(marker)


def test_publish_dry_run_refuses_an_empty_marker_before_version_allocation(
    publish_root: tuple[Path, Path],
) -> None:
    root, _ = publish_root
    marker_path = root / "workspace" / "state" / "pages-build.json"
    marker = json.loads(marker_path.read_text("utf-8"))
    marker["subject_count"] = 0
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(PublishError, match="marker has no subjects"):
        Publisher(root).publish(dry_run=True, remote="test")


def test_publish_rejects_empty_snapshot_and_candidate_pages(
    publish_root: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = publish_root
    marker = json.loads(
        (root / "workspace" / "state" / "pages-build.json").read_text("utf-8")
    )
    empty_snapshot = {
        "candidate_id": marker["candidate_id"],
        "facts_snapshot_hash": marker["facts_snapshot_hash"],
        "source_commit": marker["source_commit"],
        "facts": {"subjects": {}, "quarters": []},
    }
    monkeypatch.setattr(
        publish_module, "read_pages_build_snapshot", lambda _: empty_snapshot
    )
    with pytest.raises(PublishError, match="facts snapshot has no subjects"):
        Publisher(root)._validate_build_snapshot(marker)

    monkeypatch.undo()
    monkeypatch.setattr(
        publish_module,
        "candidate_content_hash",
        lambda _: str(marker["business_content_hash"]),
    )
    (root / "dist" / "pages" / "subjects" / "101" / "index.html").unlink()
    with pytest.raises(PublishError, match="no subject detail page"):
        Publisher(root)._validate_candidate_tree(marker)


def test_publish_rejects_a_candidate_without_quarter_cards(
    publish_root: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = publish_root
    marker = json.loads(
        (root / "workspace" / "state" / "pages-build.json").read_text("utf-8")
    )
    monkeypatch.setattr(
        publish_module,
        "candidate_content_hash",
        lambda _: str(marker["business_content_hash"]),
    )
    page = root / "dist" / "pages" / "quarters" / "2026-04" / "index.html"
    page.write_text(
        page.read_text("utf-8").replace("data-subject-id=", "data-card-id="),
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="no quarter card"):
        Publisher(root)._validate_candidate_tree(marker)


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
