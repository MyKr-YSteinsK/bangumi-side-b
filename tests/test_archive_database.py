"""Clean archive SQLite schema creation and invariant coverage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bgm_side_b import archive_database
from bgm_side_b.archive_database import Database, UnknownSchemaError

EXPECTED_TABLES = {
    "subjects",
    "subject_titles",
    "subject_infobox",
    "subject_tags",
    "subject_sources",
    "subject_quarters",
    "subject_covers",
    "subject_review_issues",
    "sync_states",
    "database_metadata",
}


def _subject_values(media_format: str = "TV") -> tuple[object, ...]:
    return (
        101,
        "Original",
        "中文名",
        "简介",
        media_format,
        "2026-04-01",
        None,
        12,
        7.5,
        100,
        "infobox_country",
        "日本",
    )


def _insert_subject(connection: sqlite3.Connection, media_format: str = "TV") -> None:
    connection.execute(
        """
        INSERT INTO subjects (
            id, name_original, name_cn, summary_raw, media_format, air_date,
            end_date, episode_count, rating_score, rating_count,
            japanese_evidence_type, japanese_evidence_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _subject_values(media_format),
    )


def test_fresh_database_has_exact_schema_metadata_and_foreign_keys(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()

    connection = database.connect()
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT key, value FROM database_metadata")
        }
        assert tables == EXPECTED_TABLES
        assert metadata == {
            "schema_family": "bangumi-side-b-archive",
            "schema_version": "1",
        }
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_subject_and_quarter_constraints_reject_invalid_facts(tmp_path: Path) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    connection = database.connect()
    try:
        for media_format in ("WEB", "OVA", "OTHER"):
            with pytest.raises(sqlite3.IntegrityError):
                _insert_subject(connection, media_format)
        _insert_subject(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, assignment_source,
                    assignment_evidence
                ) VALUES (101, 2026, 2, 'automatic', 'air_date')
                """
            )
        connection.execute(
            """
            INSERT INTO subject_quarters (
                subject_id, year, quarter_month, assignment_source,
                assignment_evidence
            ) VALUES (101, 2026, 4, 'automatic', 'air_date')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, assignment_source,
                    assignment_evidence
                ) VALUES (101, 2026, 7, 'manual', 'review')
                """
            )
    finally:
        connection.close()


def test_counts_and_cover_dimensions_must_be_valid(tmp_path: Path) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    connection = database.connect()
    try:
        invalid = list(_subject_values())
        invalid[7] = -1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subjects (
                    id, name_original, name_cn, summary_raw, media_format, air_date,
                    end_date, episode_count, rating_score, rating_count,
                    japanese_evidence_type, japanese_evidence_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                invalid,
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO sync_states (
                    year, quarter_month, facts_status, covers_status,
                    subject_count, missing_cover_count, last_attempt_at
                ) VALUES (2026, 4, 'complete', 'incomplete', 1, 2, 'now')
                """
            )
    finally:
        connection.close()


def test_source_evidence_is_optional_only_for_unknown_source(tmp_path: Path) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    connection = database.connect()
    try:
        _insert_subject(connection)
        connection.execute(
            "INSERT INTO subject_sources VALUES (101, '来源未知', NULL, NULL)"
        )
        connection.execute("DELETE FROM subject_sources")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO subject_sources VALUES (101, '来源未知', '', '')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO subject_sources VALUES (101, '漫画改', NULL, NULL)"
            )
    finally:
        connection.close()


def test_foreign_key_cascade_removes_every_subject_child(tmp_path: Path) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    connection = database.connect()
    try:
        _insert_subject(connection)
        connection.execute(
            "INSERT INTO subject_titles VALUES (101, 'Alias', 0)"
        )
        connection.execute(
            "INSERT INTO subject_infobox VALUES (101, 0, '国家/地区', '\"日本\"')"
        )
        connection.execute("INSERT INTO subject_tags VALUES (101, '奇幻', 0)")
        connection.execute(
            "INSERT INTO subject_sources VALUES (101, '原创动画', 'infobox', '原创')"
        )
        connection.execute(
            """
            INSERT INTO subject_quarters
            VALUES (101, 2026, 4, 'automatic', 'air_date')
            """
        )
        connection.execute(
            """
            INSERT INTO subject_covers VALUES (
                101, 'https://example.invalid/cover', 'large', ?, 1200, 1800, 10
            )
            """,
            ("a" * 64,),
        )
        connection.execute(
            """
            INSERT INTO subject_review_issues VALUES (
                101, 'quarter_boundary', 2026, 4, '2026-03-31', '{}', 'now'
            )
            """
        )

        connection.execute("DELETE FROM subjects WHERE id = 101")

        for table in EXPECTED_TABLES - {"sync_states", "database_metadata"}:
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0
    finally:
        connection.close()


def test_reopen_is_idempotent_and_unknown_or_newer_schema_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "archive.sqlite3"
    database = Database(path)
    database.initialize()
    database.initialize()
    connection = database.connect()
    connection.execute(
        "UPDATE database_metadata SET value = '2' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(UnknownSchemaError, match="unsupported"):
        database.connect()
    with pytest.raises(UnknownSchemaError, match="unsupported"):
        database.initialize()

    unknown = tmp_path / "unknown.sqlite3"
    raw = sqlite3.connect(unknown)
    raw.execute("CREATE TABLE user_data (value TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(UnknownSchemaError, match="do not match"):
        Database(unknown).initialize()


def test_failed_schema_creation_rolls_back_every_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "archive.sqlite3"
    monkeypatch.setattr(
        archive_database,
        "_SCHEMA_STATEMENTS",
        (*archive_database._SCHEMA_STATEMENTS, "CREATE TABLE broken ("),
    )

    with pytest.raises(sqlite3.OperationalError):
        Database(path).initialize()

    connection = sqlite3.connect(path)
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        assert tables == []
    finally:
        connection.close()
