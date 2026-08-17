"""Risk-focused tests for the Plan 15 static projection layer."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b.build.site_projection import (
    ArchiveFactsReader,
    json_bytes,
    project_quarter,
    project_year,
)
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
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)

ROOT = Path(__file__).parents[1]


def _snapshot(
    subject_id: int,
    media_format: MediaFormat,
    *,
    premiere: Quarter,
    continuing: Quarter | None = None,
    cover: CoverRecord | None = None,
) -> SubjectSnapshot:
    return SubjectSnapshot(
        SubjectRecord(
            subject_id,
            f"Original {subject_id}",
            f"中文 {subject_id}",
            "第一行\r\n\r\n\r\n第二行",
            media_format,
            date(premiere.year, premiere.month, 2),
            None,
            12,
            8.5,
            100,
            JapaneseDecision(
                JapaneseClassification.ACCEPTED_JAPANESE,
                "bangumi_public_region_tag",
                "日本",
            ),
        ),
        aliases=(f"Alias {subject_id}",),
        tags=("喜剧", "搞笑", "奇幻", "未收录"),
        source=SourceDecision(SourceType.MANGA, "infobox", "漫画"),
        premiere=QuarterAppearance(
            premiere,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            f"{premiere.year:04d}-{premiere.month:02d}-02",
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


def _rules():
    return load_tag_rules(
        ROOT / "config" / "allowed-tags.toml",
    )


def test_projection_separates_tv_movie_and_continuing_and_is_deterministic(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "archive.sqlite3")
    database.initialize()
    cover_path = workspace / "covers" / "101.webp"
    cover_path.parent.mkdir(parents=True)
    cover_path.write_bytes(b"cover-101")
    cover_hash = hashlib.sha256(b"cover-101").hexdigest()
    repository = SubjectRepository(database)
    tv = _snapshot(
        101,
        MediaFormat.TV,
        premiere=Quarter(2026, 4),
        continuing=Quarter(2026, 7),
        cover=CoverRecord("https://example.invalid/101", "large", cover_hash, 9, 1, 9),
    )
    movie = _snapshot(202, MediaFormat.MOVIE, premiere=Quarter(2026, 7))
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, tv)
        repository.replace_subject_snapshot(connection, movie)

    facts = ArchiveFactsReader(database, workspace).read()
    april = project_quarter(facts, Quarter(2026, 4), _rules(), workspace)
    july = project_quarter(facts, Quarter(2026, 7), _rules(), workspace)

    assert [item.subject_id for item in april.tv_premiere] == [101]
    assert not april.tv_continuing
    assert [item.subject_id for item in july.tv_continuing] == [101]
    assert [item.subject_id for item in july.movie_premiere] == [202]
    card = april.tv_premiere[0]
    assert card.preferred_title == "中文 101"
    assert card.original_title == "Original 101"
    assert card.allowed_tags[:2] == ("喜剧", "奇幻")
    assert "搞笑" not in card.allowed_tags
    assert card.display_summary == "第一行\n\n第二行"
    assert card.cover_url == f"covers/101.webp?v={cover_hash}"
    assert card.premiere_quarter == "2026-04"
    assert json_bytes(april.to_dict()) == json_bytes(
        project_quarter(facts, Quarter(2026, 4), _rules(), workspace).to_dict()
    )


def test_tag_display_uses_nfkc_trim_exact_membership_and_whitelist_order(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "archive.sqlite3")
    database.initialize()
    repository = SubjectRepository(database)
    snapshot = _snapshot(505, MediaFormat.TV, premiere=Quarter(2026, 4))
    snapshot = SubjectSnapshot(
        snapshot.subject,
        aliases=snapshot.aliases,
        tags=("　奇幻　", "搞笑", "喜剧"),
        source=snapshot.source,
        premiere=snapshot.premiere,
        continuing=snapshot.continuing,
        cover=snapshot.cover,
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)

    facts = ArchiveFactsReader(database, workspace).read()
    projection = project_quarter(facts, Quarter(2026, 4), _rules(), workspace)

    assert projection.tv_premiere[0].allowed_tags == ("喜剧", "奇幻")
    assert "搞笑" not in projection.tv_premiere[0].allowed_tags


def test_year_catalog_keeps_only_list_and_lookup_fields(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "archive.sqlite3")
    database.initialize()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            _snapshot(404, MediaFormat.TV, premiere=Quarter(2026, 4)),
        )

    facts = ArchiveFactsReader(database, workspace).read()
    quarter = project_quarter(facts, Quarter(2026, 4), _rules(), workspace)
    record = project_year(2026, (quarter,)).to_dict()["records"][0]
    assert record["id"] == 404
    assert record["aliases"] == ["Alias 404"]
    assert record["episode_count"] == 12
    assert record["quarter"] == "2026-04"
    assert record["appearance"] == "premiere"
    assert "display_summary" not in record
    assert "end_date" not in record
    assert "premiere_quarter" not in record
    assert "bangumi_url" not in record


def test_projection_missing_cover_is_a_warning_and_null_url(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "archive.sqlite3")
    database.initialize()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            _snapshot(303, MediaFormat.TV, premiere=Quarter(2026, 4)),
        )

    facts = ArchiveFactsReader(database, workspace).read()
    projection = project_quarter(facts, Quarter(2026, 4), _rules(), workspace)
    assert projection.tv_premiere[0].cover_url is None
    assert projection.warnings == ("subject 303 has no cover",)


def test_legacy_zero_episode_count_projects_as_unknown(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "archive.sqlite3")
    database.initialize()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            _snapshot(306, MediaFormat.TV, premiere=Quarter(2026, 4)),
        )
        connection.execute("UPDATE subjects SET episode_count = 0 WHERE id = 306")

    facts = ArchiveFactsReader(database, workspace).read()
    projection = project_quarter(facts, Quarter(2026, 4), _rules(), workspace)

    assert projection.tv_premiere[0].episode_count is None
    assert projection.to_dict()["tv"]["premiere"][0]["episode_count"] is None


def test_large_archive_read_avoids_parameter_limits_and_caches_quarter_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "archive.sqlite3")
    database.initialize()
    quarters = tuple(
        Quarter(year, month)
        for year in range(2016, 2027)
        for month in (1, 4, 7, 10)
    )
    connection = database.connect()
    try:
        connection.executemany(
            """
            INSERT INTO subjects (
                id, name_original, name_cn, summary_raw, media_format, air_date,
                end_date, episode_count, rating_score, rating_count,
                japanese_evidence_type, japanese_evidence_value
            ) VALUES (?, ?, NULL, NULL, 'TV', ?, NULL, 12, NULL, NULL,
                      'infobox_country', 'Japan')
            """,
            (
                (
                    subject_id,
                    f"Subject {subject_id}",
                    date(quarter.year, quarter.month, 1).isoformat(),
                )
                for subject_id in range(1, 1201)
                for quarter in (quarters[(subject_id - 1) % len(quarters)],)
            ),
        )
        connection.executemany(
            """
            INSERT INTO subject_quarters (
                subject_id, year, quarter_month, appearance_kind,
                assignment_source, evidence_type, evidence_value
            ) VALUES (?, ?, ?, 'premiere', 'automatic', 'air_date', ?)
            """,
            (
                (
                    subject_id,
                    quarter.year,
                    quarter.month,
                    date(quarter.year, quarter.month, 1).isoformat(),
                )
                for subject_id in range(1, 1201)
                for quarter in (quarters[(subject_id - 1) % len(quarters)],)
            ),
        )
        connection.commit()
    finally:
        connection.close()

    statements: list[str] = []
    native_connect = database.connect

    def traced_connect() -> sqlite3.Connection:
        traced = native_connect()
        traced.set_trace_callback(lambda sql: statements.append(sql.lower()))
        return traced

    monkeypatch.setattr(database, "connect", traced_connect)
    facts = ArchiveFactsReader(database, workspace).read()

    assert len(facts.subjects) == 1200
    first_grouping = facts.by_quarter
    assert len(first_grouping) == 44
    assert facts.by_quarter is first_grouping
    with pytest.raises(TypeError):
        first_grouping[quarters[0]] = ()  # type: ignore[index]
    assert not any("where subject_id in" in sql for sql in statements)
