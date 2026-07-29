"""Numbered, transactional SQLite migrations for Bangumi Side B."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATABASE_PATH = Path("workspace/data/bangumi-side-b.sqlite3")


class MigrationError(RuntimeError):
    """Raised when a migration cannot be applied atomically."""


class UnknownMigrationVersionError(MigrationError):
    """Raised when a database was created by an unsupported schema version."""


@dataclass(frozen=True)
class Migration:
    """A numbered schema migration with individually executable statements."""

    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        1,
        "initial fact schema",
        (
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE database_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE subjects (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                media_format TEXT NOT NULL,
                summary TEXT,
                air_date TEXT,
                episode_count INTEGER CHECK (
                    episode_count IS NULL OR episode_count >= 0
                ),
                rating_score REAL,
                rating_count INTEGER CHECK (rating_count IS NULL OR rating_count >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE subject_titles (
                subject_id INTEGER NOT NULL,
                title_kind TEXT NOT NULL CHECK (
                    title_kind IN ('preferred', 'original', 'alias')
                ),
                title TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (subject_id, title_kind, title),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE subject_infobox_items (
                subject_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                item_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY (subject_id, position),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE subject_raw_tags (
                subject_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                tag_name TEXT NOT NULL,
                tag_count INTEGER CHECK (tag_count IS NULL OR tag_count >= 0),
                PRIMARY KEY (subject_id, position),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE subject_sources (
                id INTEGER PRIMARY KEY,
                subject_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                evidence_type TEXT,
                evidence_value TEXT,
                UNIQUE (subject_id, source, evidence_type, evidence_value),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE subject_quarters (
                subject_id INTEGER NOT NULL,
                year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 9999),
                month INTEGER NOT NULL CHECK (month IN (1, 4, 7, 10)),
                appearance_kind TEXT NOT NULL,
                PRIMARY KEY (subject_id, year, month),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE episodes (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                subject_id INTEGER NOT NULL,
                episode_type INTEGER NOT NULL,
                sort_number REAL,
                air_date TEXT,
                title TEXT,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE characters (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE persons (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE subject_characters (
                subject_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                role TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (subject_id, character_id),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (character_id) REFERENCES characters(id)
            )
            """,
            """
            CREATE TABLE character_voices (
                subject_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (subject_id, character_id, person_id),
                FOREIGN KEY (subject_id, character_id)
                    REFERENCES subject_characters(subject_id, character_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (person_id) REFERENCES persons(id)
            )
            """,
            """
            CREATE TABLE media_files (
                id INTEGER PRIMARY KEY,
                subject_id INTEGER,
                character_id INTEGER,
                media_kind TEXT NOT NULL,
                source_url TEXT,
                local_path TEXT,
                content_hash TEXT,
                width INTEGER CHECK (width IS NULL OR width > 0),
                height INTEGER CHECK (height IS NULL OR height > 0),
                status TEXT NOT NULL,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE sync_states (
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL CHECK (entity_id > 0),
                data_type TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                completed_at TEXT,
                error_code TEXT,
                error_summary TEXT,
                PRIMARY KEY (entity_type, entity_id, data_type)
            )
            """,
            """
            INSERT INTO database_metadata (key, value, updated_at)
            VALUES ('schema_family', 'bangumi-side-b', '1970-01-01T00:00:00Z')
            """,
        ),
    ),
)


class Database:
    """Own a SQLite database path and apply its known migrations safely."""

    def __init__(
        self,
        path: Path = DEFAULT_DATABASE_PATH,
        *,
        backup_directory: Path | None = None,
        migrations: tuple[Migration, ...] = MIGRATIONS,
    ) -> None:
        self.path = path
        self.backup_directory = backup_directory or path.parent.parent / "backups"
        self.migrations = migrations
        self._validate_migrations()

    def connect(self) -> sqlite3.Connection:
        """Open a connection with foreign-key enforcement enabled."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def migrate(self) -> tuple[int, ...]:
        """Apply pending migrations atomically and return their versions."""
        existed = self.path.exists() and self.path.stat().st_size > 0
        connection = self.connect()
        try:
            applied = self._applied_versions(connection)
            known_versions = {migration.version for migration in self.migrations}
            unknown_versions = applied - known_versions
            if unknown_versions:
                versions = ", ".join(
                    str(version) for version in sorted(unknown_versions)
                )
                raise UnknownMigrationVersionError(
                    f"database contains unknown migration version(s): {versions}"
                )

            pending = tuple(
                migration
                for migration in self.migrations
                if migration.version not in applied
            )
            if not pending:
                return ()
            if existed:
                self._backup(connection)

            try:
                connection.execute("BEGIN IMMEDIATE")
                for migration in pending:
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations (version, name, applied_at)
                        VALUES (?, ?, ?)
                        """,
                        (migration.version, migration.name, _utc_now()),
                    )
                connection.commit()
            except sqlite3.Error as error:
                connection.rollback()
                message = "migration transaction failed and was rolled back"
                raise MigrationError(message) from error

            self._trim_backups()
            return tuple(migration.version for migration in pending)
        finally:
            connection.close()

    def _applied_versions(self, connection: sqlite3.Connection) -> set[int]:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        if table is None:
            return set()
        return {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }

    def _backup(self, connection: sqlite3.Connection) -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{self.path.stem}-before-migration-"
            f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}{self.path.suffix}"
        )
        backup_path = self.backup_directory / filename
        destination = sqlite3.connect(backup_path)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        self._trim_backups()
        return backup_path

    def _trim_backups(self) -> None:
        pattern = f"{self.path.stem}-before-migration-*{self.path.suffix}"
        backups = sorted(
            self.backup_directory.glob(pattern),
            key=lambda backup: backup.stat().st_mtime_ns,
            reverse=True,
        )
        for backup in backups[5:]:
            backup.unlink()

    def _validate_migrations(self) -> None:
        versions = tuple(migration.version for migration in self.migrations)
        if any(version <= 0 for version in versions):
            raise ValueError("migration versions must be positive")
        if versions != tuple(sorted(versions)) or len(set(versions)) != len(versions):
            raise ValueError("migration versions must be unique and ascending")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
