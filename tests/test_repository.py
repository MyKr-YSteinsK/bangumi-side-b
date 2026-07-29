"""Tests for subject snapshots, transactional deletion, and sync state."""

from datetime import date
from pathlib import Path

import pytest

from bgm_side_b.database import Database
from bgm_side_b.repository import (
    CharacterRecord,
    CharacterVoiceRecord,
    PersonRecord,
    RawTag,
    SubjectCharacterRecord,
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectSource,
    SubjectTitle,
    SyncState,
)


@pytest.fixture
def repository(tmp_path: Path) -> SubjectRepository:
    database = Database(tmp_path / "workspace" / "data" / "facts.sqlite3")
    database.migrate()
    return SubjectRepository(database)


def _subject(subject_id: int = 1) -> SubjectRecord:
    return SubjectRecord(
        subject_id=subject_id,
        media_format="tv",
        summary="stable summary",
        air_date=date(2022, 1, 2),
        episode_count=12,
        rating_score=7.1,
        rating_count=100,
    )


def test_subject_upsert_refresh_and_snapshot_replacement(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject())
        repository.upsert_subject(connection, _subject())
        repository.replace_titles(
            connection,
            1,
            [SubjectTitle("preferred", "中文名"), SubjectTitle("alias", "Alias")],
        )
        repository.replace_infobox(
            connection,
            1,
            [SubjectInfoboxItem("原作", {"items": ["漫画"]})],
        )
        repository.replace_raw_tags(
            connection,
            1,
            [RawTag("喜剧", 10), RawTag("搞笑", 2)],
        )
        repository.replace_sources(
            connection,
            1,
            [SubjectSource("manga", "infobox", "漫画")],
        )
        repository.replace_quarters(
            connection,
            1,
            [SubjectQuarter(2022, 1, "new")],
        )
        repository.write_sync_state(
            connection,
            SyncState(
                "subject",
                1,
                "details",
                "success",
                "2022-01-01T00:00:00Z",
                "2022-01-01T00:00:01Z",
            ),
        )

    with repository.transaction() as connection:
        repository.refresh_rating(connection, 1, 8.2, 200)
        repository.replace_titles(
            connection,
            1,
            [SubjectTitle("preferred", "新中文名")],
        )

    connection = repository.database.connect()
    try:
        subject = connection.execute("SELECT * FROM subjects WHERE id = 1").fetchone()
        assert subject["summary"] == "stable summary"
        assert subject["air_date"] == "2022-01-02"
        assert subject["rating_score"] == 8.2
        assert subject["rating_count"] == 200
        assert connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 1
        title_count = connection.execute(
            "SELECT COUNT(*) FROM subject_titles"
        ).fetchone()[0]
        assert title_count == 1
        infobox_value = connection.execute(
            "SELECT value_json FROM subject_infobox_items"
        ).fetchone()[0]
        assert infobox_value == '{"items":["漫画"]}'
        tags = connection.execute(
            "SELECT tag_name, tag_count FROM subject_raw_tags ORDER BY position"
        ).fetchall()
        assert [tuple(tag) for tag in tags] == [("喜剧", 10), ("搞笑", 2)]
    finally:
        connection.close()
    assert repository.subject_exists(1)
    assert repository.get_sync_state("subject", 1, "details").status == "success"


def test_blacklist_delete_cascades_subject_data_but_preserves_shared_entities(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject(1))
        repository.upsert_subject(connection, _subject(2))
        repository.replace_titles(
            connection,
            1,
            [SubjectTitle("preferred", "Subject One")],
        )
        connection.executemany(
            """
            INSERT INTO characters (id, original_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            [(10, "Shared", "2022-01-01T00:00:00Z", "2022-01-01T00:00:00Z")],
        )
        connection.executemany(
            """
            INSERT INTO persons (id, original_name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            [(20, "Shared Voice", "2022-01-01T00:00:00Z", "2022-01-01T00:00:00Z")],
        )
        for subject_id in (1, 2):
            connection.execute(
                """
                INSERT INTO subject_characters (
                    subject_id, character_id, role, position
                )
                VALUES (?, 10, 'main', 0)
                """,
                (subject_id,),
            )
            connection.execute(
                """
                INSERT INTO character_voices (
                    subject_id, character_id, person_id, position
                )
                VALUES (?, 10, 20, 0)
                """,
                (subject_id,),
            )

    with repository.transaction() as connection:
        assert repository.delete_subject(connection, 1)

    connection = repository.database.connect()
    try:
        title_count = connection.execute(
            "SELECT COUNT(*) FROM subject_titles"
        ).fetchone()[0]
        assert title_count == 0
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
    finally:
        connection.close()

    with repository.transaction() as connection:
        assert repository.delete_subject(connection, 2)

    connection = repository.database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    finally:
        connection.close()


def test_failed_delete_transaction_rolls_back(repository: SubjectRepository) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject())

    with pytest.raises(RuntimeError):
        with repository.transaction() as connection:
            repository.delete_subject(connection, 1)
            raise RuntimeError("force rollback")

    assert repository.subject_exists(1)


def test_blacklist_cleanup_is_limited_to_the_current_quarter(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject(1))
        repository.upsert_subject(connection, _subject(2))
        repository.replace_quarters(
            connection,
            1,
            [SubjectQuarter(2022, 1, "new")],
        )
        repository.replace_quarters(
            connection,
            2,
            [SubjectQuarter(2022, 4, "new")],
        )
        assert repository.delete_blacklisted_subjects_in_quarter(
            connection,
            frozenset({1, 2}),
            2022,
            1,
        ) == 1

    assert not repository.subject_exists(1)
    assert repository.subject_exists(2)


def test_permanent_and_continuing_quarter_updates_are_independent(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject())
        repository.replace_permanent_quarter(
            connection,
            1,
            SubjectQuarter(2022, 1, "new", "air_date", "2022-01-02"),
        )
        repository.replace_continuing_quarters(
            connection,
            1,
            [SubjectQuarter(2022, 4, "continuing", "episode_air_date", "2022-04-02")],
        )
        repository.replace_permanent_quarter(
            connection,
            1,
            SubjectQuarter(2022, 1, "new", "air_date", "2022-01-02"),
        )
        repository.replace_continuing_quarters(
            connection,
            1,
            [SubjectQuarter(2022, 7, "continuing", "episode_air_date", "2022-07-02")],
        )

    connection = repository.database.connect()
    try:
        rows = connection.execute(
            """
            SELECT year, month, appearance_kind FROM subject_quarters
            WHERE subject_id = 1 ORDER BY month
            """
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in rows] == [
        (2022, 1, "new"),
        (2022, 7, "continuing"),
    ]


def test_role_snapshots_share_entities_but_remove_stale_orphans(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject(1))
        repository.upsert_subject(connection, _subject(2))
        repository.upsert_character(
            connection, CharacterRecord(10, "Shared", None, None)
        )
        repository.upsert_person(connection, PersonRecord(20, "Cast One", None))
        repository.replace_roles_snapshot(
            connection,
            1,
            [SubjectCharacterRecord(10, "main", 0)],
            [CharacterVoiceRecord(10, 20, None, 0)],
        )
        repository.upsert_person(connection, PersonRecord(21, "Cast Two", None))
        repository.replace_roles_snapshot(
            connection,
            2,
            [SubjectCharacterRecord(10, "main", 0)],
            [CharacterVoiceRecord(10, 21, None, 0)],
        )
        repository.replace_roles_snapshot(connection, 1, (), ())

    connection = repository.database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 1
        people = connection.execute("SELECT id FROM persons ORDER BY id").fetchall()
        voices = connection.execute(
            "SELECT subject_id, person_id FROM character_voices"
        ).fetchall()
    finally:
        connection.close()
    assert [row["id"] for row in people] == [21]
    assert [tuple(row) for row in voices] == [(2, 21)]


def test_role_details_refresh_only_when_a_current_relation_needs_it(
    repository: SubjectRepository,
) -> None:
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject())
        repository.upsert_character(connection, CharacterRecord(10, "Lead", None, None))
        repository.upsert_person(connection, PersonRecord(20, "Cast", None))
        repository.replace_roles_snapshot(
            connection,
            1,
            [SubjectCharacterRecord(10, "main", 0)],
            [CharacterVoiceRecord(10, 20, None, 0)],
        )
    assert repository.role_details_need_refresh(1)

    with repository.transaction() as connection:
        for entity_type, entity_id, data_type in (
            ("character", 10, "character_detail"),
            ("person", 20, "person_detail"),
        ):
            repository.write_sync_state(
                connection,
                SyncState(
                    entity_type,
                    entity_id,
                    data_type,
                    "success",
                    "2022-01-01T00:00:00Z",
                    "2022-01-01T00:00:00Z",
                ),
            )
    assert not repository.role_details_need_refresh(1)
