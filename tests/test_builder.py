"""Integration tests for the offline static-site build command layer."""

from __future__ import annotations

import json
from datetime import date
from io import StringIO
from pathlib import Path

import pytest

from bgm_side_b import __version__
from bgm_side_b.build.builder import ArchiveBuilder, BuildError
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.progress import ConsoleProgressReporter
from bgm_side_b.repository import (
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)
from bgm_side_b.sync import SyncScope

ROOT = Path(__file__).parents[1]


def test_offline_builder_generates_both_profiles_without_mutating_sqlite(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "facts.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.upsert_subject(
            connection,
            SubjectRecord(101, "tv", None, date(2026, 4, 1), 12, 7.2, 100),
        )
        repository.replace_titles(
            connection, 101, [SubjectTitle("preferred", "可构建作品")]
        )
        repository.replace_infobox(
            connection, 101, [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", "Japan")]
        )
        repository.replace_quarters(
            connection, 101, [SubjectQuarter(2026, 4, "new")]
        )
        repository.upsert_subject(
            connection,
            SubjectRecord(102, "tv", None, date(2025, 1, 1), 12, 7.2, 100),
        )
        repository.replace_titles(connection, 102, [SubjectTitle("preferred", "Old")])
        repository.replace_infobox(
            connection, 102, [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", "Japan")]
        )
        repository.replace_quarters(connection, 102, [SubjectQuarter(2025, 1, "new")])
    before = database.path.read_bytes()
    settings, tags, sources = load_rules(ROOT / "config")
    run = ArchiveBuilder(
        ROOT,
        database,
        settings,
        tags,
        sources,
        workspace_directory=workspace,
        distribution_directory=tmp_path / "dist",
        reports_directory=tmp_path / "reports",
    ).build(SyncScope((2026,), 4))

    assert database.path.read_bytes() == before
    assert (tmp_path / "dist" / "local" / "index.html").is_file()
    quarter_page = tmp_path / "dist" / "local" / "quarters" / "2026-04" / "index.html"
    assert quarter_page.is_file()
    assert (tmp_path / "dist" / "local" / "subjects" / "101" / "index.html").is_file()
    assert not (tmp_path / "dist" / "local" / "quarters" / "2025-01").exists()
    assert (tmp_path / "dist" / "pages" / "index.html").is_file()
    assert not (tmp_path / "dist" / "pages" / "media" / "characters").exists()
    manifest = json.loads(
        (tmp_path / "dist" / "pages" / "manifest.webmanifest").read_text("utf-8")
    )
    assert manifest["display"] == "standalone"
    assert manifest["scope"] == "./"
    assert manifest["description"] == "基于 Bangumi 数据生成的本地优先季度动画资料库。"
    settings = tmp_path / "dist" / "pages" / "settings" / "index.html"
    assert settings.is_file()
    settings_html = settings.read_text("utf-8")
    assert 'meta name="description"' in settings_html
    assert "基于 Bangumi 数据生成的本地优先季度动画资料库" in settings_html
    assert "GitHub Pages 与离线 PWA。" in settings_html
    assert __version__ in settings_html
    assert (tmp_path / "dist" / "pages" / "updates" / "index.html").is_file()
    assert (tmp_path / "dist" / "pages" / "sw.js").is_file()
    worker = (tmp_path / "dist" / "pages" / "sw.js").read_text(encoding="utf-8")
    assert "const SHELL_SCHEMA = 6;" in worker
    assert not (tmp_path / "dist" / "local" / "manifest.webmanifest").exists()
    assert not (tmp_path / "dist" / "local" / "sw.js").exists()
    report = run.report_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in report
    assert '"profile": "local"' in report
    assert '"profile": "pages"' in report
    assert '"configured_quarters": [' in report
    assert '"ignored_database_quarters": [' in report
    assert '"character_sections": 0' in report
    marker = json.loads((workspace / "state" / "pages-build.json").read_text("utf-8"))
    assert marker["profile"] == "pages"
    assert marker["schema"] == 2
    assert marker["app_version"] == __version__
    assert marker["source_commit"] != "unavailable"
    snapshot = json.loads(
        (workspace / "state" / "pages-snapshot.json").read_text("utf-8")
    )
    assert snapshot["candidate_id"] == marker["candidate_id"]
    assert snapshot["facts_snapshot_hash"] == marker["facts_snapshot_hash"]


def test_empty_database_refuses_build_and_preserves_existing_output(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "facts.sqlite3")
    database.migrate()
    settings, tags, sources = load_rules(ROOT / "config")
    previous = tmp_path / "dist" / "local" / "previous.txt"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous output", encoding="utf-8")
    marker = workspace / "state" / "pages-build.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("previous marker", encoding="utf-8")

    with pytest.raises(BuildError, match="configured release contains no subjects"):
        ArchiveBuilder(
            ROOT,
            database,
            settings,
            tags,
            sources,
            workspace_directory=workspace,
            distribution_directory=tmp_path / "dist",
            reports_directory=tmp_path / "reports",
        ).build(None, target="local")

    assert previous.read_text(encoding="utf-8") == "previous output"
    assert marker.read_text(encoding="utf-8") == "previous marker"


def test_build_reports_safe_staging_and_atomic_promotion_stages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "facts.sqlite3")
    database.migrate()
    settings, tags, sources = load_rules(ROOT / "config")
    stream = StringIO()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.upsert_subject(
            connection,
            SubjectRecord(101, "tv", None, date(2026, 4, 1), 12, 7.2, 100),
        )
        repository.replace_titles(
            connection, 101, [SubjectTitle("preferred", "可构建作品")]
        )
        repository.replace_infobox(
            connection, 101, [SubjectInfoboxItem("国家/地区", "Japan")]
        )
        repository.replace_quarters(
            connection, 101, [SubjectQuarter(2026, 4, "new")]
        )

    with ConsoleProgressReporter("build", mode="plain", stream=stream) as reporter:
        ArchiveBuilder(
            ROOT,
            database,
            settings,
            tags,
            sources,
            workspace_directory=workspace,
            distribution_directory=tmp_path / "dist",
            reports_directory=tmp_path / "reports",
            reporter=reporter,
        ).build(None, target="local")

    output = stream.getvalue()
    stages = (
        "SQLite schema",
        "SQLite facts",
        "local staging",
        "staging 验证",
        "原子替换",
    )
    for stage in stages:
        assert stage in output
    assert output.index("staging 验证") < output.index("原子替换")
    assert str(tmp_path) not in output
