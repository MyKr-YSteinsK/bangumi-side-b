"""Integration checks for the unique offline site builder."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bgm_side_b.build.site_builder import BuildError, UnifiedSiteBuilder
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
    SourceDecision,
    SourceType,
)
from bgm_side_b.repository import (
    CoverRecord,
    QuarterAppearance,
    QuarterSyncState,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)

ROOT = Path(__file__).parents[1]


def _subject(
    subject_id: int,
    media: MediaFormat,
    quarter: Quarter,
    *,
    cover: CoverRecord | None = None,
    continuing: Quarter | None = None,
) -> SubjectSnapshot:
    return SubjectSnapshot(
        SubjectRecord(
            subject_id,
            f"Original {subject_id}",
            f"中文 {subject_id}",
            "Summary",
            media,
            date(quarter.year, quarter.month, 2),
            None,
            12,
            8.0,
            10,
            JapaneseDecision(
                JapaneseClassification.ACCEPTED_JAPANESE,
                "bangumi_public_region_tag",
                "日本",
            ),
        ),
        tags=("奇幻",),
        source=SourceDecision(SourceType.ORIGINAL_ANIME, "infobox", "原创"),
        premiere=QuarterAppearance(
            quarter,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            date(quarter.year, quarter.month, 2).isoformat(),
        ),
        continuing=(
            ()
            if continuing is None
            else (
                QuarterAppearance(
                    continuing,
                    QuarterAppearanceKind.CONTINUING,
                    QuarterAssignmentSource.AUTOMATIC,
                    "main_episode_airdate",
                    f"{continuing.year:04d}-{continuing.month:02d}-03",
                ),
            )
        ),
        cover=cover,
    )


def _build_fixture(tmp_path: Path) -> tuple[UnifiedSiteBuilder, Database]:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "archive.sqlite3")
    database.initialize()
    covers = workspace / "covers"
    covers.mkdir(parents=True)
    cover_bytes = b"cover-101"
    (covers / "101.webp").write_bytes(cover_bytes)
    cover = CoverRecord(
        "https://example.invalid/101",
        "large",
        hashlib.sha256(cover_bytes).hexdigest(),
        9,
        1,
        len(cover_bytes),
    )
    repository = SubjectRepository(database)
    april = _subject(
        101,
        MediaFormat.TV,
        Quarter(2026, 4),
        cover=cover,
        continuing=Quarter(2026, 7),
    )
    movie = _subject(202, MediaFormat.MOVIE, Quarter(2026, 7))
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, april)
        repository.replace_subject_snapshot(connection, movie)
        repository.write_sync_state(
            connection,
            QuarterSyncState(Quarter(2026, 4), "complete", "complete", 1, 0, now, now),
        )
        repository.write_sync_state(
            connection,
            QuarterSyncState(Quarter(2026, 7), "complete", "complete", 2, 1, now, now),
        )
    tags = load_tag_rules(
        ROOT / "config" / "allowed-tags.toml",
        ROOT / "config" / "tag-aliases.toml",
    )
    builder = UnifiedSiteBuilder(
        ROOT,
        database,
        tags,
        workspace_directory=workspace,
        site_directory=tmp_path / "dist" / "site",
        reports_directory=workspace / "reports",
    )
    return builder, database


def test_build_all_writes_one_site_and_second_run_skips(tmp_path: Path) -> None:
    builder, _ = _build_fixture(tmp_path)
    first = builder.build()
    site = tmp_path / "dist" / "site"
    assert (site / "index.html").is_file()
    assert (site / "2026-04" / "index.html").is_file()
    assert (site / "2026-07" / "index.html").is_file()
    assert (site / "archive" / "index.html").is_file()
    assert (site / "settings" / "index.html").is_file()
    assert (site / "data" / "offline" / "2026-07.json").is_file()
    assert not (site / "subjects").exists()
    july = json.loads(
        (site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    assert [item["subject_id"] for item in july["tv"]["continuing"]] == [101]
    assert [item["subject_id"] for item in july["movie"]["premiere"]] == [202]
    second = builder.build()
    assert second.patch.written == ()
    assert second.dirty.skipped_quarters == ("2026-04", "2026-07")
    assert first.report_path.is_file()


def test_rating_change_only_dirties_own_quarter_and_shared_indexes(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_subject_facts(101)
    assert current is not None
    changed = current.subject.__class__(
        current.subject.subject_id,
        current.subject.name_original,
        current.subject.name_cn,
        current.subject.summary_raw,
        current.subject.media_format,
        current.subject.air_date,
        current.subject.end_date,
        current.subject.episode_count,
        9.0,
        current.subject.rating_count,
        current.subject.japanese,
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, replace(current, subject=changed)
        )
    run = builder.build()
    assert run.dirty.dirty_quarters == ("2026-04", "2026-07")
    assert "2026" in run.dirty.dirty_years
    assert run.dirty.archive_dirty


def test_incomplete_facts_keep_last_good_site_untouched(tmp_path: Path) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_sync_state(Quarter(2026, 7))
    assert current is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            replace(current, facts_status="incomplete"),
        )
    before = (tmp_path / "dist" / "site" / "2026-07" / "index.html").read_bytes()
    run = builder.build()
    assert run.patch.written == ()
    assert "2026-07" in run.dirty.skipped_quarters
    assert (
        tmp_path / "dist" / "site" / "2026-07" / "index.html"
    ).read_bytes() == before


def test_blacklist_residue_fails_closed_before_writing_site(tmp_path: Path) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.excluded_subject_ids = frozenset({101})
    with pytest.raises(BuildError, match="blacklist residue"):
        builder.build()
    assert not (tmp_path / "dist" / "site").exists()
