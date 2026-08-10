"""Clean SQLite schema v2 for the archive fact store."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class DatabaseError(RuntimeError):
    """Raised when an archive database cannot be safely opened or initialized."""


class UnknownSchemaError(DatabaseError):
    """Raised when a database is not the exact supported schema family/version."""


_SCHEMA_FAMILY = "bangumi-side-b-archive"
_SCHEMA_VERSION = "2"
_EXPECTED_TABLES = frozenset(
    {
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
)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE subjects (
        id INTEGER PRIMARY KEY CHECK (id > 0),
        name_original TEXT NOT NULL CHECK (length(trim(name_original)) > 0),
        name_cn TEXT,
        summary_raw TEXT,
        media_format TEXT NOT NULL CHECK (media_format IN ('TV', 'MOVIE')),
        air_date TEXT,
        end_date TEXT,
        episode_count INTEGER CHECK (episode_count IS NULL OR episode_count >= 0),
        rating_score REAL CHECK (
            rating_score IS NULL OR (rating_score >= 0 AND rating_score <= 10)
        ),
        rating_count INTEGER CHECK (rating_count IS NULL OR rating_count >= 0),
        japanese_evidence_type TEXT NOT NULL
            CHECK (length(trim(japanese_evidence_type)) > 0),
        japanese_evidence_value TEXT NOT NULL
            CHECK (length(trim(japanese_evidence_value)) > 0)
    )
    """,
    """
    CREATE TABLE subject_titles (
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        title TEXT NOT NULL CHECK (length(trim(title)) > 0),
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (subject_id, position),
        UNIQUE (subject_id, title)
    )
    """,
    """
    CREATE TABLE subject_infobox (
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK (position >= 0),
        item_key TEXT NOT NULL CHECK (length(trim(item_key)) > 0),
        value_json TEXT NOT NULL CHECK (length(value_json) > 0),
        PRIMARY KEY (subject_id, position)
    )
    """,
    """
    CREATE TABLE subject_tags (
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        tag_name TEXT NOT NULL CHECK (length(trim(tag_name)) > 0),
        position INTEGER NOT NULL CHECK (position >= 0),
        PRIMARY KEY (subject_id, position),
        UNIQUE (subject_id, tag_name)
    )
    """,
    """
    CREATE TABLE subject_sources (
        subject_id INTEGER PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
        source_type TEXT NOT NULL CHECK (source_type IN (
            '漫画改', '轻小说改', '小说改', '游戏改', '视觉小说改',
            '原创动画', '其他改编', '来源未知'
        )),
        evidence_type TEXT,
        evidence_value TEXT,
        CHECK ((evidence_type IS NULL) = (evidence_value IS NULL)),
        CHECK (
            evidence_type IS NULL
            OR (length(trim(evidence_type)) > 0 AND length(trim(evidence_value)) > 0)
        ),
        CHECK (source_type = '来源未知' OR evidence_type IS NOT NULL)
    )
    """,
    """
    CREATE TABLE subject_quarters (
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        year INTEGER NOT NULL CHECK (year > 0),
        quarter_month INTEGER NOT NULL CHECK (quarter_month IN (1, 4, 7, 10)),
        appearance_kind TEXT NOT NULL
            CHECK (appearance_kind IN ('premiere', 'continuing')),
        assignment_source TEXT NOT NULL
            CHECK (assignment_source IN ('automatic', 'manual')),
        evidence_type TEXT NOT NULL CHECK (length(trim(evidence_type)) > 0),
        evidence_value TEXT NOT NULL CHECK (length(trim(evidence_value)) > 0),
        PRIMARY KEY (subject_id, year, quarter_month)
    )
    """,
    """
    CREATE TABLE subject_covers (
        subject_id INTEGER PRIMARY KEY REFERENCES subjects(id) ON DELETE CASCADE,
        source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
        source_variant TEXT NOT NULL CHECK (length(trim(source_variant)) > 0),
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        width INTEGER NOT NULL CHECK (width > 0),
        height INTEGER NOT NULL CHECK (height > 0),
        size_bytes INTEGER NOT NULL CHECK (size_bytes > 0)
    )
    """,
    """
    CREATE TABLE subject_review_issues (
        subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        issue_code TEXT NOT NULL CHECK (length(trim(issue_code)) > 0),
        candidate_year INTEGER,
        candidate_quarter INTEGER,
        observed_value TEXT,
        details_json TEXT NOT NULL CHECK (length(details_json) > 0),
        detected_at TEXT NOT NULL CHECK (length(trim(detected_at)) > 0),
        PRIMARY KEY (subject_id, issue_code),
        CHECK (
            (candidate_year IS NULL AND candidate_quarter IS NULL)
            OR (
                candidate_year IS NOT NULL
                AND candidate_quarter IS NOT NULL
                AND candidate_year > 0
                AND candidate_quarter IN (1, 4, 7, 10)
            )
        )
    )
    """,
    """
    CREATE TABLE sync_states (
        year INTEGER NOT NULL CHECK (year > 0),
        quarter_month INTEGER NOT NULL CHECK (quarter_month IN (1, 4, 7, 10)),
        facts_status TEXT NOT NULL CHECK (facts_status IN ('complete', 'incomplete')),
        covers_status TEXT NOT NULL
            CHECK (covers_status IN ('complete', 'incomplete')),
        subject_count INTEGER NOT NULL CHECK (subject_count >= 0),
        missing_cover_count INTEGER NOT NULL CHECK (
            missing_cover_count >= 0 AND missing_cover_count <= subject_count
        ),
        last_attempt_at TEXT NOT NULL CHECK (length(trim(last_attempt_at)) > 0),
        last_success_at TEXT,
        PRIMARY KEY (year, quarter_month)
    )
    """,
    """
    CREATE TABLE database_metadata (
        key TEXT PRIMARY KEY CHECK (length(trim(key)) > 0),
        value TEXT NOT NULL CHECK (length(value) > 0)
    )
    """,
    """
    CREATE INDEX idx_subject_quarters_appearance
    ON subject_quarters(year, quarter_month)
    """,
    """
    CREATE UNIQUE INDEX idx_subject_quarters_one_premiere
    ON subject_quarters(subject_id) WHERE appearance_kind = 'premiere'
    """,
    """
    CREATE TRIGGER reject_movie_continuing_insert
    BEFORE INSERT ON subject_quarters
    WHEN NEW.appearance_kind = 'continuing'
      AND (SELECT media_format FROM subjects WHERE id = NEW.subject_id) = 'MOVIE'
    BEGIN
        SELECT RAISE(ABORT, 'movies cannot have continuing appearances');
    END
    """,
    """
    CREATE TRIGGER reject_movie_continuing_update
    BEFORE UPDATE OF appearance_kind, subject_id ON subject_quarters
    WHEN NEW.appearance_kind = 'continuing'
      AND (SELECT media_format FROM subjects WHERE id = NEW.subject_id) = 'MOVIE'
    BEGIN
        SELECT RAISE(ABORT, 'movies cannot have continuing appearances');
    END
    """,
    """
    CREATE INDEX idx_subject_review_issues_quarter
    ON subject_review_issues(candidate_year, candidate_quarter)
    """,
    """
    CREATE INDEX idx_subject_review_issues_code
    ON subject_review_issues(issue_code)
    """,
)


class Database:
    """Create and open only the clean, current archive schema."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        """Create schema v2 atomically, or verify an existing exact schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._raw_connect()
        try:
            existing = _user_tables(connection)
            if existing:
                _validate_schema(connection)
                _verify_integrity(connection)
                return
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.executemany(
                    "INSERT INTO database_metadata (key, value) VALUES (?, ?)",
                    (
                        ("schema_family", _SCHEMA_FAMILY),
                        ("schema_version", _SCHEMA_VERSION),
                    ),
                )
                _validate_schema(connection)
                _verify_integrity(connection)
            except BaseException:
                connection.rollback()
                raise
            connection.commit()
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        """Open a validated schema v2 connection with foreign keys enabled."""
        connection = self._raw_connect()
        try:
            _validate_schema(connection)
            _verify_integrity(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def _raw_connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _user_tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row["name"]
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        )
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = _user_tables(connection)
    if tables != _EXPECTED_TABLES:
        raise UnknownSchemaError("database tables do not match archive schema v1")
    metadata = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM database_metadata")
    }
    if metadata.get("schema_family") != _SCHEMA_FAMILY:
        raise UnknownSchemaError("database schema family or version is unsupported")
    if metadata.get("schema_version") != _SCHEMA_VERSION:
        if metadata.get("schema_version") == "1":
            raise UnknownSchemaError("unsupported old development schema")
        raise UnknownSchemaError("database schema family or version is unsupported")
    _validate_subject_quarters_contract(connection)


def _validate_subject_quarters_contract(connection: sqlite3.Connection) -> None:
    """Reject lookalike development databases before callers issue normal SQL."""
    columns = tuple(
        row["name"] for row in connection.execute("PRAGMA table_info(subject_quarters)")
    )
    if columns != (
        "subject_id",
        "year",
        "quarter_month",
        "appearance_kind",
        "assignment_source",
        "evidence_type",
        "evidence_value",
    ):
        raise UnknownSchemaError("database quarter appearance contract is unsupported")
    indexes = {
        row["name"]: (bool(row["unique"]), bool(row["partial"]))
        for row in connection.execute("PRAGMA index_list(subject_quarters)")
    }
    if indexes.get("idx_subject_quarters_appearance") != (False, False):
        raise UnknownSchemaError("database quarter appearance contract is unsupported")
    if indexes.get("idx_subject_quarters_one_premiere") != (True, True):
        raise UnknownSchemaError("database quarter appearance contract is unsupported")
    index_columns = {
        name: tuple(
            row["name"] for row in connection.execute(f"PRAGMA index_info({name})")
        )
        for name in (
            "idx_subject_quarters_appearance",
            "idx_subject_quarters_one_premiere",
        )
    }
    if index_columns != {
        "idx_subject_quarters_appearance": ("year", "quarter_month"),
        "idx_subject_quarters_one_premiere": ("subject_id",),
    }:
        raise UnknownSchemaError("database quarter appearance contract is unsupported")
    triggers = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    if not {
        "reject_movie_continuing_insert",
        "reject_movie_continuing_update",
    }.issubset(triggers):
        raise UnknownSchemaError("database quarter appearance contract is unsupported")


def _verify_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise DatabaseError("database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DatabaseError("database foreign key check failed")
