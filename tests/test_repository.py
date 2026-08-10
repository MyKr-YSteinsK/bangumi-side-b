"""Direct repository coverage for clean archive subject facts."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path, PurePosixPath

import pytest

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
    InfoboxItem,
    QuarterAppearance,
    QuarterSyncState,
    ReviewIssue,
    ReviewQueueItem,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
    cover_relative_path,
)


@pytest.fixture
def repository(tmp_path: Path) -> SubjectRepository:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    return SubjectRepository(database)


def _subject(subject_id: int, name: str = "Original") -> SubjectRecord:
    return SubjectRecord(
        subject_id,
        name,
        "中文名",
        "原始简介",
        MediaFormat.TV,
        date(2026, 4, 1),
        None,
        12,
        7.5,
        100,
        JapaneseDecision(
            JapaneseClassification.ACCEPTED_JAPANESE,
            "infobox_country",
            '["日本"]',
        ),
    )


def _snapshot(subject_id: int = 101) -> SubjectSnapshot:
    return SubjectSnapshot(
        _subject(subject_id),
        aliases=("Alias", "别名"),
        infobox=(InfoboxItem("国家/地区", ["日本", "美国"]),),
        tags=("奇幻", "冒险"),
        source=SourceDecision(SourceType.MANGA, "infobox", "漫画"),
        premiere=QuarterAppearance(
            Quarter(2026, 4),
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            "2026-04-01",
        ),
        cover=CoverRecord(
            "https://example.invalid/cover", "large", "a" * 64, 1200, 1800, 10
        ),
        review_issues=(
            ReviewIssue(
                "quarter_boundary",
                Quarter(2026, 4),
                "2026-03-31",
                {"reason": "boundary"},
                "2026-08-09T00:00:00Z",
            ),
        ),
    )


def _continuing(quarter: Quarter, evidence_value: str) -> QuarterAppearance:
    return QuarterAppearance(
        quarter,
        QuarterAppearanceKind.CONTINUING,
        QuarterAssignmentSource.AUTOMATIC,
        "main_episode_airdate",
        evidence_value,
    )


def test_subject_snapshot_round_trip_and_child_replacement(
    repository: SubjectRepository,
) -> None:
    snapshot = _snapshot()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)

    assert repository.get_subject_facts(101) == snapshot
    assert repository.list_subjects_appearing_in_quarter(Quarter(2026, 4)) == (
        snapshot,
    )

    updated = SubjectSnapshot(
        replace(snapshot.subject, name_cn=None, summary_raw=None),
        aliases=("Only Alias",),
        infobox=(),
        tags=("日常",),
        source=SourceDecision(SourceType.UNKNOWN),
        premiere=QuarterAppearance(
            Quarter(2026, 4),
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.MANUAL,
            "manual_override",
            "review:42",
        ),
        cover=None,
        review_issues=(),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, updated)

    assert repository.get_subject_facts(101) == updated


def test_failed_snapshot_replacement_rolls_back_every_child(
    repository: SubjectRepository,
) -> None:
    original = _snapshot()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, original)
    duplicate = ReviewIssue("same", None, None, {}, "now")
    invalid = replace(
        original,
        subject=replace(original.subject, name_original="Changed"),
        review_issues=(duplicate, duplicate),
    )

    with pytest.raises(sqlite3.IntegrityError):
        with repository.transaction() as connection:
            repository.replace_subject_snapshot(connection, invalid)

    assert repository.get_subject_facts(101) == original


def test_affected_quarters_and_blacklist_purge_cascade_subject_facts(
    repository: SubjectRepository,
) -> None:
    first = replace(
        _snapshot(101), continuing=(_continuing(Quarter(2026, 7), "2026-07-04"),)
    )
    second = replace(
        _snapshot(202),
        premiere=QuarterAppearance(
            Quarter(2026, 7),
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.MANUAL,
            "manual_override",
            "review:7",
        ),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, first)
        repository.replace_subject_snapshot(connection, second)

    assert repository.affected_quarters(frozenset({101, 202, 999})) == (
        Quarter(2026, 4),
        Quarter(2026, 7),
    )
    with repository.transaction() as connection:
        assert repository.delete_subjects(connection, frozenset({101, 999})) == 1

    assert repository.get_subject_facts(101) is None
    assert repository.get_subject_facts(202) == second
    connection = repository.database.connect()
    try:
        for table in (
            "subject_titles",
            "subject_infobox",
            "subject_tags",
            "subject_sources",
            "subject_quarters",
            "subject_covers",
            "subject_review_issues",
        ):
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE subject_id = 101"
            ).fetchone()[0]
            assert count == 0
    finally:
        connection.close()


def test_tv_premiere_and_continuing_appearances_are_independent(
    repository: SubjectRepository,
) -> None:
    snapshot = replace(
        _snapshot(),
        continuing=(
            _continuing(Quarter(2026, 7), "2026-07-04"),
            _continuing(Quarter(2026, 10), "2026-10-03"),
            _continuing(Quarter(2027, 1), "2027-01-02"),
        ),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)

    assert repository.get_premiere_appearance(101) == snapshot.premiere
    assert repository.list_subjects_appearing_in_quarter(Quarter(2026, 7)) == (
        snapshot,
    )
    assert repository.list_tv_subjects_appearing_in_previous_quarter(
        Quarter(2026, 10)
    ) == (snapshot,)

    replacement = QuarterAppearance(
        Quarter(2026, 1),
        QuarterAppearanceKind.PREMIERE,
        QuarterAssignmentSource.MANUAL,
        "manual_override",
        "review:101",
    )
    with repository.transaction() as connection:
        repository.replace_premiere(connection, 101, replacement)

    stored = repository.get_subject_facts(101)
    assert stored is not None
    assert stored.premiere == replacement
    assert stored.continuing == snapshot.continuing


def test_appearance_constraints_reject_duplicate_premieres_and_movie_continuing(
    repository: SubjectRepository,
) -> None:
    snapshot = _snapshot()
    movie = replace(
        snapshot,
        subject=replace(snapshot.subject, media_format=MediaFormat.MOVIE),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, appearance_kind,
                    assignment_source, evidence_type, evidence_value
                ) VALUES (
                    101, 2026, 7, 'premiere', 'automatic', 'air_date', '2026-07-01'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, appearance_kind,
                    assignment_source, evidence_type, evidence_value
                ) VALUES (101, 2026, 4, 'continuing', 'automatic',
                          'main_episode_airdate', '2026-04-01')
                """
            )
        repository.replace_subject_snapshot(connection, movie)
        with pytest.raises(sqlite3.IntegrityError, match="movies cannot"):
            repository.upsert_continuing_appearance(
                connection, 101, _continuing(Quarter(2026, 7), "2026-07-01")
            )


def test_quarter_sync_state_and_derived_cover_path(
    repository: SubjectRepository,
) -> None:
    quarter = Quarter(2026, 4)
    incomplete = QuarterSyncState(
        quarter, "incomplete", "incomplete", 10, 2, "attempt-1", None
    )
    complete = QuarterSyncState(
        quarter, "complete", "complete", 10, 0, "attempt-2", "success-2"
    )
    with repository.transaction() as connection:
        repository.write_sync_state(connection, incomplete)
        repository.write_sync_state(connection, complete)

    assert repository.get_sync_state(quarter) == complete
    assert cover_relative_path(101) == PurePosixPath("covers/101.webp")
    with pytest.raises(ValueError, match="positive"):
        cover_relative_path(0)


def test_review_queue_reconciles_and_preserves_unresolved_subject_facts(
    repository: SubjectRepository,
) -> None:
    unresolved = replace(
        _snapshot(),
        subject=replace(
            _subject(101),
            japanese=JapaneseDecision(
                JapaneseClassification.UNRESOLVED,
                "unresolved_missing_infobox_country",
                "[]",
            ),
        ),
        premiere=None,
        cover=None,
        review_issues=(
            ReviewIssue("japanese_unresolved", None, None, {"why": "missing"}, "now"),
        ),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, unresolved)

    assert repository.get_subject_facts(101) == unresolved
    assert repository.list_review_issues() == (
        ReviewQueueItem(unresolved.subject, unresolved.review_issues[0]),
    )
    with repository.transaction() as connection:
        repository.replace_review_issues(connection, 101, ())
    assert repository.list_review_issues() == ()
