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

    assert database.migrate() == (1,)
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
        2,
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
        assert [row["version"] for row in versions] == [1]
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

    for version in range(2, 8):
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
