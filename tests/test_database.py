"""Tests for transactional schema migration behavior."""

import sqlite3
from pathlib import Path

import pytest

from bgm_side_b.database import (
    MIGRATIONS,
    Database,
    Migration,
    MigrationError,
    UnknownMigrationVersionError,
)


def test_new_database_initializes_idempotently_with_foreign_keys(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "workspace" / "data" / "facts.sqlite3")

    assert database.migrate() == (1, 2)
    assert database.migrate() == ()

    connection = database.connect()
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "schema_migrations",
            "database_metadata",
            "subjects",
            "subject_titles",
            "subject_infobox_items",
            "subject_raw_tags",
            "subject_sources",
            "subject_quarters",
            "episodes",
            "characters",
            "persons",
            "subject_characters",
            "character_voices",
            "media_files",
            "sync_states",
        }.issubset(tables)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_titles (subject_id, title_kind, title, position)
                VALUES (999, 'preferred', 'missing', 0)
                """
            )
    finally:
        connection.close()


def test_existing_database_is_backed_up_and_failed_migration_rolls_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace" / "data" / "facts.sqlite3"
    base_database = Database(path)
    base_database.migrate()
    failing_migration = Migration(
        3,
        "broken migration",
        (
            "CREATE TABLE should_not_persist (id INTEGER PRIMARY KEY)",
            "THIS IS NOT SQL",
        ),
    )
    database = Database(path, migrations=MIGRATIONS + (failing_migration,))

    with pytest.raises(MigrationError):
        database.migrate()

    connection = database.connect()
    try:
        assert connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'should_not_persist'
            """
        ).fetchone() is None
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in versions] == [1, 2]
    finally:
        connection.close()
    assert list((tmp_path / "workspace" / "backups").glob("*.sqlite3"))


def test_unknown_migration_version_is_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "workspace" / "data" / "facts.sqlite3")
    database.migrate()
    connection = database.connect()
    try:
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (999, 'future', '2022-01-01T00:00:00Z')
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnknownMigrationVersionError):
        database.migrate()


def test_only_the_five_most_recent_migration_backups_are_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace" / "data" / "facts.sqlite3"
    migrations = list(MIGRATIONS)
    Database(path).migrate()

    for version in range(3, 9):
        migrations.append(
            Migration(
                version,
                f"test migration {version}",
                (f"CREATE TABLE test_migration_{version} (id INTEGER PRIMARY KEY)",),
            )
        )
        Database(path, migrations=tuple(migrations)).migrate()

    backups = list((tmp_path / "workspace" / "backups").glob("*.sqlite3"))
    assert len(backups) == 5


def test_plan_one_database_upgrades_to_schema_two_without_losing_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace" / "data" / "facts.sqlite3"
    legacy = Database(path, migrations=(MIGRATIONS[0],))
    assert legacy.migrate() == (1,)
    connection = legacy.connect()
    try:
        connection.execute(
            """
            INSERT INTO subjects (
                id, media_format, summary, air_date, episode_count, rating_score,
                rating_count, created_at, updated_at
            ) VALUES (1, 'tv', 'legacy', '2022-01-01', 12, 7.5, 10, 'a', 'b')
            """
        )
        connection.execute(
            """
            INSERT INTO subject_titles (subject_id, title_kind, title, position)
            VALUES (1, 'preferred', 'Legacy', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO subject_raw_tags (subject_id, position, tag_name, tag_count)
            VALUES (1, 0, 'tag', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO subject_sources (subject_id, source) VALUES (1, 'original')
            """
        )
        connection.execute(
            """
            INSERT INTO subject_quarters (subject_id, year, month, appearance_kind)
            VALUES (1, 2022, 1, 'new')
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(path)
    assert database.migrate() == (2,)

    connection = database.connect()
    try:
        subject = connection.execute("SELECT * FROM subjects WHERE id = 1").fetchone()
        assert subject["first_seen_at"] == "a"
        assert subject["last_seen_at"] == "b"
        assert subject["availability_status"] == "available"
        subject_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(subjects)")
        }
        assert {
            "end_date",
            "total_episode_count",
            "availability_status",
            "first_seen_at",
            "last_seen_at",
        }.issubset(subject_columns)
        episode_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(episodes)"
        ).fetchall()
        assert [row["table"] for row in episode_foreign_keys] == ["subjects"]
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_titles"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_raw_tags"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM subject_sources"
        ).fetchone()[0] == 1
        quarter = connection.execute("SELECT * FROM subject_quarters").fetchone()
        assert quarter["appearance_kind"] == "new"
        assert quarter["position"] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    assert list((tmp_path / "workspace" / "backups").glob("*.sqlite3"))


def test_schema_two_failure_rolls_back_to_a_plan_one_database(tmp_path: Path) -> None:
    path = tmp_path / "workspace" / "data" / "facts.sqlite3"
    Database(path, migrations=(MIGRATIONS[0],)).migrate()
    broken_schema_two = Migration(
        2,
        "broken enriched synchronization fact schema",
        MIGRATIONS[1].statements + ("THIS IS NOT SQL",),
        requires_foreign_keys_off=True,
    )

    with pytest.raises(MigrationError):
        Database(path, migrations=(MIGRATIONS[0], broken_schema_two)).migrate()

    connection = Database(path, migrations=(MIGRATIONS[0],)).connect()
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(subjects)")
        }
        assert "end_date" not in columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'subject_quarters_v2'"
        ).fetchone() is None
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
