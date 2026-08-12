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


def test_subject_snapshot_batch_uses_one_connection(
    repository: SubjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _snapshot(101)
    second = _snapshot(202)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, first)
        repository.replace_subject_snapshot(connection, second)

    connect_calls = 0
    native_connect = repository.database.connect

    def counted_connect() -> sqlite3.Connection:
        nonlocal connect_calls
        connect_calls += 1
        return native_connect()

    monkeypatch.setattr(repository.database, "connect", counted_connect)
    assert repository.get_subject_facts_many([202, 999, 101, 202]) == {
        101: first,
        202: second,
    }
    assert connect_calls == 1


def test_subject_snapshot_batch_selects_scale_with_chunks(
    repository: SubjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject_ids = tuple(range(1, 1201))
    with repository.transaction() as connection:
        for subject_id in subject_ids:
            repository.upsert_subject(connection, _subject(subject_id))

    connect_calls = 0
    selects: list[str] = []
    native_connect = repository.database.connect

    def traced_connect() -> sqlite3.Connection:
        nonlocal connect_calls
        connect_calls += 1
        connection = native_connect()
        connection.set_trace_callback(
            lambda statement: selects.append(statement)
            if statement.lstrip().upper().startswith("SELECT")
            else None
        )
        return connection

    monkeypatch.setattr(repository.database, "connect", traced_connect)
    observed: list[int] = []
    for count, maximum in ((1, 10), (100, 10), (1200, 30)):
        selects.clear()
        snapshots = repository.get_subject_facts_many(subject_ids[:count])
        assert tuple(snapshots) == subject_ids[:count]
        observed.append(len(selects))
        assert len(selects) <= maximum

    assert connect_calls == 3
    assert observed[2] < observed[1] * 4


def test_repository_fanout_readers_use_bounded_selects(
    repository: SubjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots = tuple(_snapshot(subject_id) for subject_id in range(1, 101))
    with repository.transaction() as connection:
        for snapshot in snapshots:
            repository.replace_subject_snapshot(connection, snapshot)

    selects: list[str] = []
    native_connect = repository.database.connect

    def traced_connect() -> sqlite3.Connection:
        connection = native_connect()
        connection.set_trace_callback(
            lambda statement: selects.append(statement)
            if statement.lstrip().upper().startswith("SELECT")
            else None
        )
        return connection

    monkeypatch.setattr(repository.database, "connect", traced_connect)
    appearing = repository.list_subjects_appearing_in_quarter(Quarter(2026, 4))
    assert tuple(item.subject.subject_id for item in appearing) == tuple(range(1, 101))
    assert len(selects) <= 10

    selects.clear()
    reviews = repository.list_review_issues(Quarter(2026, 4))
    assert len(reviews) == 100
    assert len(selects) <= 10

    selects.clear()
    assert repository.get_premiere_appearance(1) == snapshots[0].premiere
    assert len(selects) == 1


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


def test_large_id_set_queries_are_chunked_and_delete_rolls_back_atomically(
    repository: SubjectRepository,
) -> None:
    subject_ids = frozenset(range(1, 1502))
    april = QuarterAppearance(
        Quarter(2026, 4),
        QuarterAppearanceKind.PREMIERE,
        QuarterAssignmentSource.AUTOMATIC,
        "air_date",
        "2026-04-01",
    )
    july = replace(april, quarter=Quarter(2026, 7), evidence_value="2026-07-01")
    with repository.transaction() as connection:
        for subject_id in subject_ids:
            repository.upsert_subject(connection, _subject(subject_id))
            repository.replace_appearances(
                connection,
                subject_id,
                (april if subject_id % 2 else july,),
            )

    assert repository.affected_quarters(subject_ids | frozenset({999999})) == (
        Quarter(2026, 4),
        Quarter(2026, 7),
    )
    with pytest.raises(RuntimeError, match="rollback"):
        with repository.transaction() as connection:
            assert repository.delete_subjects(connection, subject_ids) == 1501
            raise RuntimeError("rollback")

    connection = repository.database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 1501
    finally:
        connection.close()
    with repository.transaction() as connection:
        assert repository.delete_subjects(connection, subject_ids) == 1501
    connection = repository.database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0
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


def test_bulk_snapshot_semantics_match_single_subject_reads(
    repository: SubjectRepository,
) -> None:
    tv = replace(
        _snapshot(101),
        aliases=("Alias A", "别名乙", "Alias C"),
        infobox=(
            InfoboxItem("放送开始", "2026-04-01"),
            InfoboxItem("国家/地区", ["日本"]),
            InfoboxItem("制作", {"studio": "A"}),
        ),
        tags=("奇幻", "冒险", "日常"),
        continuing=(
            _continuing(Quarter(2026, 7), "2026-07-04"),
            _continuing(Quarter(2026, 10), "2026-10-03"),
        ),
        review_issues=(
            ReviewIssue("issue_a", Quarter(2026, 4), "a", {"order": 1}, "now"),
            ReviewIssue("issue_b", Quarter(2026, 4), "b", {"order": 2}, "now"),
        ),
    )
    movie = replace(
        _snapshot(202),
        subject=replace(_snapshot(202).subject, media_format=MediaFormat.MOVIE),
        source=SourceDecision(SourceType.UNKNOWN),
        premiere=QuarterAppearance(
            Quarter(2026, 7),
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            "2026-07-01",
        ),
        continuing=(),
        cover=None,
        review_issues=(),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, tv)
        repository.replace_subject_snapshot(connection, movie)
        connection.execute("DELETE FROM subject_sources WHERE subject_id = 202")

    bulk = repository.get_subject_facts_many((202, 999999, 101, 202, 101))
    assert tuple(bulk) == (101, 202)
    assert bulk[101] == repository.get_subject_facts(101) == tv
    assert bulk[101].aliases == tv.aliases
    assert bulk[101].infobox == tv.infobox
    assert bulk[101].tags == tv.tags
    assert bulk[101].appearances == tv.appearances
    assert bulk[202] == repository.get_subject_facts(202)
    assert bulk[202].source == SourceDecision(SourceType.UNKNOWN)
    assert bulk[202].cover is None
    assert bulk[202].continuing == ()
    assert [item.issue.issue_code for item in repository.list_review_issues()] == [
        "issue_a",
        "issue_b",
    ]
