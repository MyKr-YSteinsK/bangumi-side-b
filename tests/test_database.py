"""Clean archive SQLite schema creation and invariant coverage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bgm_side_b import database
from bgm_side_b.database import Database, UnknownSchemaError

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
            "schema_version": "2",
        }
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_normal_connections_skip_full_integrity_until_explicit_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = Database(path)
    archive.initialize()
    statements: list[str] = []
    native_connect = sqlite3.connect

    class TracedConnection(sqlite3.Connection):
        def execute(  # type: ignore[override]
            self, sql: str, parameters: tuple[object, ...] = ()
        ) -> sqlite3.Cursor:
            statements.append(" ".join(sql.lower().split()))
            return super().execute(sql, parameters)

    def traced_connect(database_path: Path) -> sqlite3.Connection:
        return native_connect(database_path, factory=TracedConnection)

    monkeypatch.setattr(database.sqlite3, "connect", traced_connect)

    for _ in range(3):
        connection = archive.connect()
        connection.close()
    assert statements.count("pragma integrity_check") == 0
    assert statements.count("pragma foreign_key_check") == 0

    archive.verify_integrity()
    assert statements.count("pragma integrity_check") == 1
    assert statements.count("pragma foreign_key_check") == 1

    statements.clear()
    archive.initialize()
    assert statements.count("pragma integrity_check") == 1
    assert statements.count("pragma foreign_key_check") == 1


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
                    subject_id, year, quarter_month, appearance_kind,
                    assignment_source, evidence_type, evidence_value
                ) VALUES (
                    101, 2026, 2, 'premiere', 'automatic', 'air_date', '2026-04-01'
                )
                """
            )
        connection.execute(
            """
            INSERT INTO subject_quarters (
                subject_id, year, quarter_month, appearance_kind,
                assignment_source, evidence_type, evidence_value
            ) VALUES (101, 2026, 4, 'premiere', 'automatic', 'air_date', '2026-04-01')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, appearance_kind,
                    assignment_source, evidence_type, evidence_value
                ) VALUES (
                    101, 2026, 7, 'premiere', 'manual', 'manual_override', 'review'
                )
                """
            )
    finally:
        connection.close()


def test_movie_cannot_have_a_continuing_appearance(tmp_path: Path) -> None:
    database = Database(tmp_path / "archive.sqlite3")
    database.initialize()
    connection = database.connect()
    try:
        values = list(_subject_values("MOVIE"))
        values[0] = 202
        connection.execute(
            """
            INSERT INTO subjects (
                id, name_original, name_cn, summary_raw, media_format, air_date,
                end_date, episode_count, rating_score, rating_count,
                japanese_evidence_type, japanese_evidence_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError, match="movies cannot"):
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, appearance_kind,
                    assignment_source, evidence_type, evidence_value
                ) VALUES (202, 2026, 7, 'continuing', 'automatic',
                          'main_episode_airdate', '2026-07-04')
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
            VALUES (101, 2026, 4, 'premiere', 'automatic', 'air_date', '2026-04-01')
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
        "UPDATE database_metadata SET value = '1' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(UnknownSchemaError, match="old development"):
        database.connect()
    with pytest.raises(UnknownSchemaError, match="old development"):
        database.initialize()

    raw = sqlite3.connect(path)
    raw.execute("UPDATE database_metadata SET value = '3' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()
    with pytest.raises(UnknownSchemaError, match="unsupported"):
        database.connect()

    unknown = tmp_path / "unknown.sqlite3"
    raw = sqlite3.connect(unknown)
    raw.execute("CREATE TABLE user_data (value TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(UnknownSchemaError, match="do not match"):
        Database(unknown).initialize()


def test_quarter_appearance_contract_requires_named_indexes_and_triggers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "archive.sqlite3"
    database = Database(path)
    database.initialize()
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX idx_subject_quarters_one_premiere")
    connection.commit()
    connection.close()

    with pytest.raises(UnknownSchemaError, match="quarter appearance contract"):
        database.connect()


def test_failed_schema_creation_rolls_back_every_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "archive.sqlite3"
    monkeypatch.setattr(
        database,
        "_SCHEMA_STATEMENTS",
        (*database._SCHEMA_STATEMENTS, "CREATE TABLE broken ("),
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
