"""Integration tests for the offline static-site build command layer."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.repository import (
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
            SubjectRecord(101, "tv", None, date(2022, 1, 1), 12, 7.2, 100),
        )
        repository.replace_titles(
            connection, 101, [SubjectTitle("preferred", "可构建作品")]
        )
        repository.replace_quarters(
            connection, 101, [SubjectQuarter(2022, 1, "new")]
        )
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
    ).build(SyncScope((2022,), 1))

    assert database.path.read_bytes() == before
    assert (tmp_path / "dist" / "local" / "index.html").is_file()
    quarter_page = tmp_path / "dist" / "local" / "quarters" / "2022-01" / "index.html"
    assert quarter_page.is_file()
    assert (tmp_path / "dist" / "local" / "subjects" / "101" / "index.html").is_file()
    assert (tmp_path / "dist" / "pages" / "index.html").is_file()
    assert not (tmp_path / "dist" / "pages" / "media" / "characters").exists()
    manifest = json.loads(
        (tmp_path / "dist" / "pages" / "manifest.webmanifest").read_text("utf-8")
    )
    assert manifest["display"] == "standalone"
    assert manifest["scope"] == "./"
    assert (tmp_path / "dist" / "pages" / "settings" / "index.html").is_file()
    assert (tmp_path / "dist" / "pages" / "updates" / "index.html").is_file()
    assert (tmp_path / "dist" / "pages" / "sw.js").is_file()
    assert not (tmp_path / "dist" / "local" / "manifest.webmanifest").exists()
    assert not (tmp_path / "dist" / "local" / "sw.js").exists()
    report = run.report_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in report
    assert '"profile": "local"' in report
    assert '"profile": "pages"' in report
    marker = json.loads((workspace / "state" / "pages-build.json").read_text("utf-8"))
    assert marker["profile"] == "pages"
    assert marker["schema"] == 2
    assert marker["source_commit"] != "unavailable"
    snapshot = json.loads(
        (workspace / "state" / "pages-snapshot.json").read_text("utf-8")
    )
    assert snapshot["candidate_id"] == marker["candidate_id"]
    assert snapshot["facts_snapshot_hash"] == marker["facts_snapshot_hash"]


def test_empty_database_builds_a_safe_empty_local_index(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "facts.sqlite3")
    database.migrate()
    settings, tags, sources = load_rules(ROOT / "config")
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
    index = (tmp_path / "dist" / "local" / "index.html").read_text(encoding="utf-8")
    assert "尚无可展示资料" in index
