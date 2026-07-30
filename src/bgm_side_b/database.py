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
    requires_foreign_keys_off: bool = False


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
    Migration(
        2,
        "enriched synchronization fact schema",
        (
            """
            ALTER TABLE subjects ADD COLUMN end_date TEXT
            """,
            """
            ALTER TABLE subjects ADD COLUMN total_episode_count INTEGER CHECK (
                total_episode_count IS NULL OR total_episode_count >= 0
            )
            """,
            """
            ALTER TABLE subjects ADD COLUMN availability_status TEXT NOT NULL
                DEFAULT 'available'
                CHECK (availability_status IN ('available', 'unavailable'))
            """,
            """
            ALTER TABLE subjects ADD COLUMN first_seen_at TEXT
            """,
            """
            ALTER TABLE subjects ADD COLUMN last_seen_at TEXT
            """,
            """
            UPDATE subjects
            SET first_seen_at = created_at, last_seen_at = updated_at
            """,
            """
            CREATE TABLE subject_quarters_v2 (
                subject_id INTEGER NOT NULL,
                year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 9999),
                month INTEGER NOT NULL CHECK (month IN (1, 4, 7, 10)),
                appearance_kind TEXT NOT NULL CHECK (
                    appearance_kind IN ('new', 'continuing', 'movie', 'ova', 'other')
                ),
                evidence_type TEXT,
                evidence_value TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (subject_id, year, month, appearance_kind),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            INSERT INTO subject_quarters_v2 (
                subject_id, year, month, appearance_kind, evidence_type,
                evidence_value, position
            )
            SELECT subject_id, year, month,
                   CASE WHEN appearance_kind IN (
                       'new', 'continuing', 'movie', 'ova', 'other'
                   ) THEN appearance_kind ELSE 'other' END,
                   NULL, NULL, 0
            FROM subject_quarters
            """,
            """
            CREATE TABLE episodes_v2 (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                subject_id INTEGER NOT NULL,
                episode_type INTEGER NOT NULL,
                episode_number REAL,
                sort_number REAL,
                name TEXT,
                name_cn TEXT,
                air_date TEXT,
                duration_seconds INTEGER CHECK (
                    duration_seconds IS NULL OR duration_seconds > 0
                ),
                raw_duration TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
            """,
            """
            INSERT INTO episodes_v2 (
                id, subject_id, episode_type, episode_number, sort_number,
                name, name_cn, air_date, duration_seconds, raw_duration, position
            )
            SELECT id, subject_id, episode_type, NULL, sort_number, title, NULL,
                   air_date, NULL, NULL,
                   ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY id) - 1
            FROM episodes
            """,
            """
            CREATE TABLE characters_v2 (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                original_name TEXT,
                chinese_name TEXT,
                summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO characters_v2 (
                id, original_name, chinese_name, summary, created_at, updated_at
            )
            SELECT id, name, NULL, NULL, created_at, updated_at FROM characters
            """,
            """
            CREATE TABLE persons_v2 (
                id INTEGER PRIMARY KEY CHECK (id > 0),
                original_name TEXT,
                chinese_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO persons_v2 (
                id, original_name, chinese_name, created_at, updated_at
            )
            SELECT id, name, NULL, created_at, updated_at FROM persons
            """,
            """
            CREATE TABLE subject_characters_v2 (
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
            INSERT INTO subject_characters_v2 (
                subject_id, character_id, role, position
            )
            SELECT subject_id, character_id, role, position FROM subject_characters
            """,
            """
            CREATE TABLE character_voices_v2 (
                subject_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                person_id INTEGER NOT NULL,
                language TEXT,
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (subject_id, character_id, person_id),
                FOREIGN KEY (subject_id, character_id)
                    REFERENCES subject_characters(subject_id, character_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (person_id) REFERENCES persons(id)
            )
            """,
            """
            INSERT INTO character_voices_v2 (
                subject_id, character_id, person_id, language, position
            )
            SELECT subject_id, character_id, person_id, NULL, position
            FROM character_voices
            """,
            """
            CREATE TABLE media_files_v2 (
                id INTEGER PRIMARY KEY,
                owner_type TEXT NOT NULL CHECK (owner_type IN ('subject', 'character')),
                owner_id INTEGER NOT NULL CHECK (owner_id > 0),
                media_kind TEXT NOT NULL CHECK (
                    media_kind IN ('cover', 'character_image')
                ),
                source_url TEXT,
                local_path TEXT,
                content_hash TEXT,
                size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
                mime_type TEXT,
                width INTEGER CHECK (width IS NULL OR width > 0),
                height INTEGER CHECK (height IS NULL OR height > 0),
                downloaded_at TEXT,
                verified_at TEXT,
                status TEXT NOT NULL,
                UNIQUE (owner_type, owner_id, media_kind)
            )
            """,
            """
            INSERT INTO media_files_v2 (
                id, owner_type, owner_id, media_kind, source_url, local_path,
                content_hash, size_bytes, mime_type, width, height, downloaded_at,
                verified_at, status
            )
            SELECT id,
                   CASE WHEN subject_id IS NOT NULL
                        THEN 'subject' ELSE 'character' END,
                   CASE WHEN subject_id IS NOT NULL
                        THEN subject_id ELSE character_id END,
                   CASE WHEN media_kind = 'cover'
                        THEN 'cover' ELSE 'character_image' END,
                   source_url,
                   CASE
                       WHEN local_path GLOB '[A-Za-z]:*'
                            OR substr(local_path, 1, 1) IN ('/', '\\') THEN NULL
                       ELSE replace(local_path, '\\', '/')
                   END,
                   content_hash, NULL, NULL, width, height, NULL, NULL, status
            FROM media_files
            WHERE subject_id IS NOT NULL OR character_id IS NOT NULL
            """,
            """
            CREATE TABLE sync_states_v2 (
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL CHECK (entity_id > 0),
                data_type TEXT NOT NULL,
                status TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                last_success_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
                error_code TEXT,
                error_summary TEXT,
                PRIMARY KEY (entity_type, entity_id, data_type)
            )
            """,
            """
            INSERT INTO sync_states_v2 (
                entity_type, entity_id, data_type, status, last_attempt_at,
                last_success_at, failure_count, error_code, error_summary
            )
            SELECT entity_type, entity_id,
                   CASE WHEN data_type = 'details' THEN 'subject_detail'
                        ELSE data_type END,
                   status, attempted_at,
                   CASE WHEN status = 'success' THEN completed_at ELSE NULL END,
                   CASE WHEN status = 'failed' THEN 1 ELSE 0 END,
                   error_code, error_summary
            FROM sync_states
            """,
            "DROP TABLE character_voices",
            "DROP TABLE subject_characters",
            "DROP TABLE media_files",
            "DROP TABLE characters",
            "DROP TABLE persons",
            "DROP TABLE subject_quarters",
            "DROP TABLE episodes",
            "DROP TABLE sync_states",
            "ALTER TABLE characters_v2 RENAME TO characters",
            "ALTER TABLE persons_v2 RENAME TO persons",
            "ALTER TABLE subject_quarters_v2 RENAME TO subject_quarters",
            "ALTER TABLE episodes_v2 RENAME TO episodes",
            "ALTER TABLE subject_characters_v2 RENAME TO subject_characters",
            "ALTER TABLE character_voices_v2 RENAME TO character_voices",
            "ALTER TABLE media_files_v2 RENAME TO media_files",
            "ALTER TABLE sync_states_v2 RENAME TO sync_states",
        ),
        requires_foreign_keys_off=True,
    ),
    Migration(
        3,
        "store Bangumi subject type for release classification",
        (
            """
            ALTER TABLE subjects ADD COLUMN subject_type INTEGER CHECK (
                subject_type IS NULL OR subject_type > 0
            )
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

            requires_foreign_keys_off = any(
                migration.requires_foreign_keys_off for migration in pending
            )
            if requires_foreign_keys_off:
                connection.execute("PRAGMA foreign_keys = OFF")
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
                self._verify_integrity(connection)
                connection.commit()
            except sqlite3.Error as error:
                connection.rollback()
                message = "migration transaction failed and was rolled back"
                raise MigrationError(message) from error
            finally:
                if requires_foreign_keys_off:
                    connection.execute("PRAGMA foreign_keys = ON")

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

    def _verify_integrity(self, connection: sqlite3.Connection) -> None:
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if foreign_key_errors or integrity != "ok":
            raise sqlite3.DatabaseError("migration produced an invalid database")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
