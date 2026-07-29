"""Small, direct SQLite access for subject facts and sync state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from bgm_side_b.database import Database


@dataclass(frozen=True)
class SubjectRecord:
    """Stable subject facts plus its refreshable rating fields."""

    subject_id: int
    media_format: str
    summary: str | None
    air_date: date | None
    episode_count: int | None
    rating_score: float | None
    rating_count: int | None
    total_episode_count: int | None = None
    end_date: date | None = None
    availability_status: str = "available"


@dataclass(frozen=True)
class SubjectTitle:
    """One preferred, original, or alias title in an ordered snapshot."""

    title_kind: str
    title: str


@dataclass(frozen=True)
class SubjectInfoboxItem:
    """One Infobox key and its JSON-serialisable structured value."""

    key: str
    value: Any


@dataclass(frozen=True)
class RawTag:
    """A raw community tag with its reported count."""

    name: str
    count: int | None


@dataclass(frozen=True)
class SubjectSource:
    """A source classification and its exact supporting evidence."""

    source: str
    evidence_type: str | None = None
    evidence_value: str | None = None


@dataclass(frozen=True)
class SubjectQuarter:
    """One unique subject appearance in a quarter."""

    year: int
    month: int
    appearance_kind: str
    evidence_type: str | None = None
    evidence_value: str | None = None
    position: int = 0


@dataclass(frozen=True)
class SyncState:
    """The latest result for one entity/data-type synchronisation unit."""

    entity_type: str
    entity_id: int
    data_type: str
    status: str
    last_attempt_at: str
    last_success_at: str | None = None
    failure_count: int = 0
    error_code: str | None = None
    error_summary: str | None = None


class SubjectRepository:
    """Repository methods scoped to subjects and their direct fact snapshots."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Provide a write transaction that commits or rolls back as one unit."""
        connection = self.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def upsert_subject(
        self, connection: sqlite3.Connection, subject: SubjectRecord
    ) -> None:
        """Insert or replace a complete stable subject snapshot."""
        _validate_subject(subject)
        timestamp = _utc_now()
        connection.execute(
            """
            INSERT INTO subjects (
                id, media_format, summary, air_date, episode_count,
                total_episode_count, end_date, availability_status, rating_score,
                rating_count, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                media_format = excluded.media_format,
                summary = excluded.summary,
                air_date = excluded.air_date,
                episode_count = excluded.episode_count,
                total_episode_count = excluded.total_episode_count,
                end_date = excluded.end_date,
                availability_status = excluded.availability_status,
                rating_score = excluded.rating_score,
                rating_count = excluded.rating_count,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (
                subject.subject_id,
                subject.media_format,
                subject.summary,
                _date_value(subject.air_date),
                subject.episode_count,
                subject.total_episode_count,
                _date_value(subject.end_date),
                subject.availability_status,
                subject.rating_score,
                subject.rating_count,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )

    def refresh_rating(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        rating_score: float | None,
        rating_count: int | None,
    ) -> None:
        """Refresh only volatile rating fields without touching stable facts."""
        if subject_id <= 0 or (rating_count is not None and rating_count < 0):
            raise ValueError("subject id and rating count must be valid")
        connection.execute(
            """
            UPDATE subjects
            SET rating_score = ?, rating_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (rating_score, rating_count, _utc_now(), subject_id),
        )

    def replace_titles(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        titles: Sequence[SubjectTitle],
    ) -> None:
        """Replace the complete ordered title snapshot for one subject."""
        for title in titles:
            if title.title_kind not in {"preferred", "original", "alias"}:
                raise ValueError("title kind must be preferred, original, or alias")
            if not title.title:
                raise ValueError("title must not be empty")
        connection.execute(
            "DELETE FROM subject_titles WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_titles (subject_id, title_kind, title, position)
            VALUES (?, ?, ?, ?)
            """,
            (
                (subject_id, title.title_kind, title.title, position)
                for position, title in enumerate(titles)
            ),
        )

    def replace_infobox(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        items: Sequence[SubjectInfoboxItem],
    ) -> None:
        """Replace Infobox values with compact, normalised JSON values only."""
        connection.execute(
            "DELETE FROM subject_infobox_items WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_infobox_items (
                subject_id, position, item_key, value_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    position,
                    item.key,
                    json.dumps(item.value, ensure_ascii=False, separators=(",", ":")),
                )
                for position, item in enumerate(items)
            ),
        )

    def replace_raw_tags(
        self, connection: sqlite3.Connection, subject_id: int, tags: Sequence[RawTag]
    ) -> None:
        """Replace raw tags while preserving their API order and reported counts."""
        if any(tag.count is not None and tag.count < 0 for tag in tags):
            raise ValueError("tag count must not be negative")
        connection.execute(
            "DELETE FROM subject_raw_tags WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_raw_tags (subject_id, position, tag_name, tag_count)
            VALUES (?, ?, ?, ?)
            """,
            (
                (subject_id, position, tag.name, tag.count)
                for position, tag in enumerate(tags)
            ),
        )

    def replace_sources(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        sources: Sequence[SubjectSource],
    ) -> None:
        """Replace source rows and their exact evidence values."""
        connection.execute(
            "DELETE FROM subject_sources WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_sources (
                subject_id, source, evidence_type, evidence_value
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    source.source,
                    source.evidence_type,
                    source.evidence_value,
                )
                for source in sources
            ),
        )

    def replace_quarters(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        quarters: Sequence[SubjectQuarter],
    ) -> None:
        """Replace every quarter relationship for legacy callers only."""
        _validate_quarters(quarters)
        connection.execute(
            "DELETE FROM subject_quarters WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_quarters (
                subject_id, year, month, appearance_kind, evidence_type,
                evidence_value, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    quarter.year,
                    quarter.month,
                    quarter.appearance_kind,
                    quarter.evidence_type,
                    quarter.evidence_value,
                    quarter.position,
                )
                for quarter in quarters
            ),
        )

    def replace_permanent_quarter(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        quarter: SubjectQuarter,
    ) -> None:
        """Update permanent ownership without touching continuing appearances."""
        _validate_quarters((quarter,))
        if quarter.appearance_kind not in {"new", "movie"}:
            raise ValueError("permanent quarter kind must be new or movie")
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE subject_id = ? AND appearance_kind IN ('new', 'movie')
            """,
            (subject_id,),
        )
        self._insert_quarters(connection, subject_id, (quarter,))

    def replace_continuing_quarters(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        quarters: Sequence[SubjectQuarter],
    ) -> None:
        """Rebuild continuing appearances without touching permanent ownership."""
        _validate_quarters(quarters)
        if any(quarter.appearance_kind != "continuing" for quarter in quarters):
            raise ValueError("continuing quarter kind must be continuing")
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE subject_id = ? AND appearance_kind = 'continuing'
            """,
            (subject_id,),
        )
        self._insert_quarters(connection, subject_id, quarters)

    def _insert_quarters(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        quarters: Sequence[SubjectQuarter],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO subject_quarters (
                subject_id, year, month, appearance_kind, evidence_type,
                evidence_value, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    quarter.year,
                    quarter.month,
                    quarter.appearance_kind,
                    quarter.evidence_type,
                    quarter.evidence_value,
                    quarter.position,
                )
                for quarter in quarters
            ),
        )

    def write_sync_state(
        self, connection: sqlite3.Connection, state: SyncState
    ) -> None:
        """Write the latest state for one entity and data type."""
        connection.execute(
            """
            INSERT INTO sync_states (
                entity_type, entity_id, data_type, status, last_attempt_at,
                last_success_at, failure_count, error_code, error_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id, data_type) DO UPDATE SET
                status = excluded.status,
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                failure_count = excluded.failure_count,
                error_code = excluded.error_code,
                error_summary = excluded.error_summary
            """,
            (
                state.entity_type,
                state.entity_id,
                state.data_type,
                state.status,
                state.last_attempt_at,
                state.last_success_at,
                state.failure_count,
                state.error_code,
                state.error_summary,
            ),
        )

    def subject_exists(self, subject_id: int) -> bool:
        """Return whether the subject currently exists in the fact store."""
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM subjects WHERE id = ?", (subject_id,)
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def get_sync_state(
        self, entity_type: str, entity_id: int, data_type: str
    ) -> SyncState | None:
        """Read the latest uniquely keyed synchronisation state."""
        connection = self.database.connect()
        try:
            row = connection.execute(
                """
                SELECT entity_type, entity_id, data_type, status, last_attempt_at,
                       last_success_at, failure_count, error_code, error_summary
                FROM sync_states
                WHERE entity_type = ? AND entity_id = ? AND data_type = ?
                """,
                (entity_type, entity_id, data_type),
            ).fetchone()
            return SyncState(**dict(row)) if row is not None else None
        finally:
            connection.close()

    def delete_subject(self, connection: sqlite3.Connection, subject_id: int) -> bool:
        """Physically delete a blacklisted subject and its orphaned shared entities."""
        connection.execute(
            "DELETE FROM media_files WHERE owner_type = 'subject' AND owner_id = ?",
            (subject_id,),
        )
        deleted = connection.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
        if deleted.rowcount == 0:
            return False
        orphaned_character_ids = connection.execute(
            """
            SELECT id FROM characters
            WHERE NOT EXISTS (
                SELECT 1 FROM subject_characters
                WHERE subject_characters.character_id = characters.id
            )
            """
        ).fetchall()
        connection.executemany(
            "DELETE FROM media_files WHERE owner_type = 'character' AND owner_id = ?",
            ((row["id"],) for row in orphaned_character_ids),
        )
        connection.execute(
            """
            DELETE FROM characters
            WHERE NOT EXISTS (
                SELECT 1 FROM subject_characters
                WHERE subject_characters.character_id = characters.id
            )
            """
        )
        connection.execute(
            """
            DELETE FROM persons
            WHERE NOT EXISTS (
                SELECT 1 FROM character_voices
                WHERE character_voices.person_id = persons.id
            )
            """
        )
        return True

    def delete_blacklisted_subjects_in_quarter(
        self,
        connection: sqlite3.Connection,
        subject_ids: frozenset[int],
        year: int,
        month: int,
    ) -> int:
        """Delete only blacklisted subjects already related to one sync quarter."""
        if not subject_ids:
            return 0
        placeholders = ", ".join("?" for _ in subject_ids)
        rows = connection.execute(
            f"""
            SELECT subject_id FROM subject_quarters
            WHERE year = ? AND month = ? AND subject_id IN ({placeholders})
            """,
            (year, month, *sorted(subject_ids)),
        ).fetchall()
        return sum(self.delete_subject(connection, row["subject_id"]) for row in rows)


def _validate_subject(subject: SubjectRecord) -> None:
    if subject.subject_id <= 0:
        raise ValueError("subject id must be positive")
    if not subject.media_format:
        raise ValueError("subject format must not be empty")
    if subject.episode_count is not None and subject.episode_count < 0:
        raise ValueError("episode count must not be negative")
    if subject.total_episode_count is not None and subject.total_episode_count < 0:
        raise ValueError("total episode count must not be negative")
    if subject.availability_status not in {"available", "unavailable"}:
        raise ValueError("availability status must be available or unavailable")
    if subject.rating_count is not None and subject.rating_count < 0:
        raise ValueError("rating count must not be negative")


def _date_value(value: date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("air date must not contain a time")
    return value.isoformat()


def _validate_quarters(quarters: Sequence[SubjectQuarter]) -> None:
    allowed_kinds = {"new", "continuing", "movie", "ova", "other"}
    for quarter in quarters:
        if quarter.month not in {1, 4, 7, 10}:
            raise ValueError("quarter month must be one of 1, 4, 7, or 10")
        if quarter.appearance_kind not in allowed_kinds:
            raise ValueError("quarter appearance kind is invalid")
        if quarter.position < 0:
            raise ValueError("quarter position must not be negative")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
