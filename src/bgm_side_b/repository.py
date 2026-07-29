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
class EpisodeRecord:
    """One main-story episode retained in its API response order."""

    episode_id: int
    episode_number: float | None
    sort_number: float | None
    name: str | None
    name_cn: str | None
    air_date: date | None
    duration_seconds: int | None
    raw_duration: str | None
    position: int


@dataclass(frozen=True)
class CharacterRecord:
    """One global displayable character fact snapshot."""

    character_id: int
    original_name: str | None
    chinese_name: str | None
    summary: str | None


@dataclass(frozen=True)
class PersonRecord:
    """One global voice-actor fact snapshot without any image fields."""

    person_id: int
    original_name: str | None
    chinese_name: str | None


@dataclass(frozen=True)
class SubjectCharacterRecord:
    """A main-character relation in one subject-local roles snapshot."""

    character_id: int
    role: str | None
    position: int


@dataclass(frozen=True)
class CharacterVoiceRecord:
    """One subject-scoped cast relation in the API actor order."""

    character_id: int
    person_id: int
    language: str | None
    position: int


@dataclass(frozen=True)
class StoredSubject:
    """Subject facts required to decide whether episode refresh is necessary."""

    subject_id: int
    media_format: str
    air_date: date | None
    episode_count: int | None
    total_episode_count: int | None
    end_date: date | None


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


@dataclass(frozen=True)
class MediaRecord:
    """One workspace-relative verified media cache record."""

    owner_type: str
    owner_id: int
    media_kind: str
    source_url: str | None
    local_path: str | None
    content_hash: str | None
    size_bytes: int | None
    mime_type: str | None
    width: int | None
    height: int | None
    downloaded_at: str | None
    verified_at: str | None
    status: str


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
                rating_score = COALESCE(excluded.rating_score, subjects.rating_score),
                rating_count = COALESCE(excluded.rating_count, subjects.rating_count),
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

    def replace_main_episodes(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        episodes: Sequence[EpisodeRecord],
    ) -> None:
        """Replace one subject's complete main-story episode snapshot."""
        _validate_episodes(episodes)
        connection.execute("DELETE FROM episodes WHERE subject_id = ?", (subject_id,))
        connection.executemany(
            """
            INSERT INTO episodes (
                id, subject_id, episode_type, episode_number, sort_number, name,
                name_cn, air_date, duration_seconds, raw_duration, position
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    episode.episode_id,
                    subject_id,
                    episode.episode_number,
                    episode.sort_number,
                    episode.name,
                    episode.name_cn,
                    _date_value(episode.air_date),
                    episode.duration_seconds,
                    episode.raw_duration,
                    episode.position,
                )
                for episode in episodes
            ),
        )

    def upsert_character(
        self, connection: sqlite3.Connection, character: CharacterRecord
    ) -> None:
        """Insert global character facts without overwriting known values with gaps."""
        _validate_display_entity(
            character.character_id, character.original_name, character.chinese_name
        )
        timestamp = _utc_now()
        connection.execute(
            """
            INSERT INTO characters (
                id, original_name, chinese_name, summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                original_name = COALESCE(
                    excluded.original_name, characters.original_name
                ),
                chinese_name = COALESCE(
                    excluded.chinese_name, characters.chinese_name
                ),
                summary = COALESCE(excluded.summary, characters.summary),
                updated_at = excluded.updated_at
            """,
            (
                character.character_id,
                character.original_name,
                character.chinese_name,
                character.summary,
                timestamp,
                timestamp,
            ),
        )

    def upsert_person(
        self, connection: sqlite3.Connection, person: PersonRecord
    ) -> None:
        """Insert global person facts without storing or requesting person images."""
        _validate_display_entity(
            person.person_id, person.original_name, person.chinese_name
        )
        timestamp = _utc_now()
        connection.execute(
            """
            INSERT INTO persons (
                id, original_name, chinese_name, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                original_name = COALESCE(excluded.original_name, persons.original_name),
                chinese_name = COALESCE(excluded.chinese_name, persons.chinese_name),
                updated_at = excluded.updated_at
            """,
            (
                person.person_id,
                person.original_name,
                person.chinese_name,
                timestamp,
                timestamp,
            ),
        )

    def replace_roles_snapshot(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        characters: Sequence[SubjectCharacterRecord],
        voices: Sequence[CharacterVoiceRecord],
    ) -> None:
        """Replace one subject's main roles and cast without touching other works."""
        _validate_roles_snapshot(characters, voices)
        connection.execute(
            "DELETE FROM subject_characters WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_characters (subject_id, character_id, role, position)
            VALUES (?, ?, ?, ?)
            """,
            (
                (subject_id, item.character_id, item.role, item.position)
                for item in characters
            ),
        )
        connection.executemany(
            """
            INSERT INTO character_voices (
                subject_id, character_id, person_id, language, position
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    item.character_id,
                    item.person_id,
                    item.language,
                    item.position,
                )
                for item in voices
            ),
        )
        self.cleanup_orphaned_entities(connection)

    def cleanup_orphaned_entities(self, connection: sqlite3.Connection) -> None:
        """Remove only global entities no subject-local relation still references."""
        connection.execute(
            """
            DELETE FROM persons
            WHERE NOT EXISTS (
                SELECT 1 FROM character_voices
                WHERE character_voices.person_id = persons.id
            )
            """
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

    def get_media_record(
        self, owner_type: str, owner_id: int, media_kind: str
    ) -> MediaRecord | None:
        """Read one media record by its immutable owner and kind key."""
        connection = self.database.connect()
        try:
            row = connection.execute(
                """
                SELECT owner_type, owner_id, media_kind, source_url, local_path,
                       content_hash, size_bytes, mime_type, width, height,
                       downloaded_at, verified_at, status
                FROM media_files
                WHERE owner_type = ? AND owner_id = ? AND media_kind = ?
                """,
                (owner_type, owner_id, media_kind),
            ).fetchone()
            return MediaRecord(**dict(row)) if row is not None else None
        finally:
            connection.close()

    def upsert_media_record(
        self, connection: sqlite3.Connection, record: MediaRecord
    ) -> None:
        """Store one verified or failed media result without absolute paths."""
        _validate_media_record(record)
        connection.execute(
            """
            INSERT INTO media_files (
                owner_type, owner_id, media_kind, source_url, local_path,
                content_hash, size_bytes, mime_type, width, height, downloaded_at,
                verified_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_type, owner_id, media_kind) DO UPDATE SET
                source_url = excluded.source_url,
                local_path = excluded.local_path,
                content_hash = excluded.content_hash,
                size_bytes = excluded.size_bytes,
                mime_type = excluded.mime_type,
                width = excluded.width,
                height = excluded.height,
                downloaded_at = excluded.downloaded_at,
                verified_at = excluded.verified_at,
                status = excluded.status
            """,
            (
                record.owner_type,
                record.owner_id,
                record.media_kind,
                record.source_url,
                record.local_path,
                record.content_hash,
                record.size_bytes,
                record.mime_type,
                record.width,
                record.height,
                record.downloaded_at,
                record.verified_at,
                record.status,
            ),
        )

    def list_orphaned_media_records(self) -> tuple[MediaRecord, ...]:
        """List media whose subject or character owner no longer exists."""
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT owner_type, owner_id, media_kind, source_url, local_path,
                       content_hash, size_bytes, mime_type, width, height,
                       downloaded_at, verified_at, status
                FROM media_files AS media
                WHERE (owner_type = 'subject' AND NOT EXISTS (
                    SELECT 1 FROM subjects WHERE subjects.id = media.owner_id
                )) OR (owner_type = 'character' AND NOT EXISTS (
                    SELECT 1 FROM characters WHERE characters.id = media.owner_id
                ))
                ORDER BY owner_type, owner_id, media_kind
                """
            ).fetchall()
            return tuple(MediaRecord(**dict(row)) for row in rows)
        finally:
            connection.close()

    def delete_media_record(
        self, connection: sqlite3.Connection, record: MediaRecord
    ) -> None:
        """Remove a media row after its file was removed or became untrustworthy."""
        connection.execute(
            """
            DELETE FROM media_files
            WHERE owner_type = ? AND owner_id = ? AND media_kind = ?
            """,
            (record.owner_type, record.owner_id, record.media_kind),
        )

    def role_details_need_refresh(self, subject_id: int) -> bool:
        """Return whether a current role relation lacks successful detail data."""
        connection = self.database.connect()
        try:
            character_needs_refresh = connection.execute(
                """
                SELECT 1
                FROM subject_characters AS relation
                LEFT JOIN sync_states AS state
                    ON state.entity_type = 'character'
                    AND state.entity_id = relation.character_id
                    AND state.data_type = 'character_detail'
                WHERE relation.subject_id = ?
                  AND (state.status IS NULL OR state.status != 'success')
                LIMIT 1
                """,
                (subject_id,),
            ).fetchone()
            if character_needs_refresh is not None:
                return True
            person_needs_refresh = connection.execute(
                """
                SELECT 1
                FROM character_voices AS voice
                LEFT JOIN sync_states AS state
                    ON state.entity_type = 'person'
                    AND state.entity_id = voice.person_id
                    AND state.data_type = 'person_detail'
                WHERE voice.subject_id = ?
                  AND (state.status IS NULL OR state.status != 'success')
                LIMIT 1
                """,
                (subject_id,),
            ).fetchone()
            return person_needs_refresh is not None
        finally:
            connection.close()

    def list_subject_character_ids(self, subject_id: int) -> tuple[int, ...]:
        """Return main characters currently related to one subject in API order."""
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT character_id FROM subject_characters
                WHERE subject_id = ? ORDER BY position, character_id
                """,
                (subject_id,),
            ).fetchall()
            return tuple(row["character_id"] for row in rows)
        finally:
            connection.close()

    def get_stored_subject(self, subject_id: int) -> StoredSubject | None:
        """Return the persisted facts used by incremental episode decisions."""
        connection = self.database.connect()
        try:
            row = connection.execute(
                """
                SELECT id, media_format, air_date, episode_count,
                       total_episode_count, end_date
                FROM subjects WHERE id = ?
                """,
                (subject_id,),
            ).fetchone()
            if row is None:
                return None
            return StoredSubject(
                subject_id=row["id"],
                media_format=row["media_format"],
                air_date=_stored_date(row["air_date"]),
                episode_count=row["episode_count"],
                total_episode_count=row["total_episode_count"],
                end_date=_stored_date(row["end_date"]),
            )
        finally:
            connection.close()

    def list_tv_subject_ids(self) -> tuple[int, ...]:
        """Return already stored TV subjects in stable ID order."""
        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT id FROM subjects WHERE media_format = 'tv' ORDER BY id"
            ).fetchall()
            return tuple(row["id"] for row in rows)
        finally:
            connection.close()

    def main_episode_count(self, subject_id: int) -> int:
        """Return the count of persisted main-story episodes only."""
        connection = self.database.connect()
        try:
            return connection.execute(
                """
                SELECT COUNT(*) FROM episodes
                WHERE subject_id = ? AND episode_type = 0
                """,
                (subject_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def continuing_quarter_count(self, subject_id: int) -> int:
        """Return persisted continuing-quarter relations for one subject."""
        connection = self.database.connect()
        try:
            return connection.execute(
                """
                SELECT COUNT(*) FROM subject_quarters
                WHERE subject_id = ? AND appearance_kind = 'continuing'
                """,
                (subject_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def main_episode_air_dates(self, subject_id: int) -> tuple[date, ...]:
        """Read complete main-story air dates in stable episode order."""
        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT air_date FROM episodes
                WHERE subject_id = ? AND episode_type = 0 AND air_date IS NOT NULL
                ORDER BY position, episode_number, sort_number, air_date, id
                """,
                (subject_id,),
            ).fetchall()
            return tuple(
                parsed
                for row in rows
                if (parsed := _stored_date(row["air_date"])) is not None
            )
        finally:
            connection.close()

    def delete_subject(self, connection: sqlite3.Connection, subject_id: int) -> bool:
        """Physically delete a blacklisted subject and its orphaned shared entities."""
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
        orphaned_person_ids = connection.execute(
            """
            SELECT id FROM persons
            WHERE NOT EXISTS (
                SELECT 1 FROM character_voices
                WHERE character_voices.person_id = persons.id
            )
            """
        ).fetchall()
        connection.execute(
            "DELETE FROM sync_states WHERE entity_type = 'subject' AND entity_id = ?",
            (subject_id,),
        )
        connection.executemany(
            "DELETE FROM sync_states WHERE entity_type = 'character' AND entity_id = ?",
            ((row["id"],) for row in orphaned_character_ids),
        )
        connection.executemany(
            "DELETE FROM sync_states WHERE entity_type = 'person' AND entity_id = ?",
            ((row["id"],) for row in orphaned_person_ids),
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


def _validate_episodes(episodes: Sequence[EpisodeRecord]) -> None:
    seen_ids: set[int] = set()
    for episode in episodes:
        if episode.episode_id <= 0 or episode.episode_id in seen_ids:
            raise ValueError("episode ids must be unique positive integers")
        if episode.position < 0:
            raise ValueError("episode position must not be negative")
        if episode.duration_seconds is not None and episode.duration_seconds <= 0:
            raise ValueError("episode duration must be positive when present")
        seen_ids.add(episode.episode_id)


def _validate_display_entity(
    entity_id: int, original_name: str | None, chinese_name: str | None
) -> None:
    if entity_id <= 0:
        raise ValueError("entity id must be positive")
    if not original_name and not chinese_name:
        raise ValueError("display entity must have an original or Chinese name")


def _validate_media_record(record: MediaRecord) -> None:
    if record.owner_type not in {"subject", "character"}:
        raise ValueError("media owner type must be subject or character")
    if record.owner_id <= 0:
        raise ValueError("media owner id must be positive")
    expected_kind = "cover" if record.owner_type == "subject" else "character_image"
    if record.media_kind != expected_kind:
        raise ValueError("media kind does not match its owner type")
    if record.status not in {"success", "failed", "stale"}:
        raise ValueError("media status must be success, failed, or stale")
    if record.local_path is not None:
        path = record.local_path.replace("\\", "/")
        if path.startswith("/") or ":" in path or ".." in path.split("/"):
            raise ValueError("media path must be workspace-relative")
    if record.size_bytes is not None and record.size_bytes < 0:
        raise ValueError("media size must not be negative")


def _validate_roles_snapshot(
    characters: Sequence[SubjectCharacterRecord], voices: Sequence[CharacterVoiceRecord]
) -> None:
    character_ids: set[int] = set()
    for character in characters:
        if character.character_id <= 0 or character.character_id in character_ids:
            raise ValueError("role characters must have unique positive ids")
        if character.position < 0:
            raise ValueError("role position must not be negative")
        character_ids.add(character.character_id)
    voice_keys: set[tuple[int, int]] = set()
    for voice in voices:
        key = (voice.character_id, voice.person_id)
        if (
            voice.character_id not in character_ids
            or voice.person_id <= 0
            or key in voice_keys
            or voice.position < 0
        ):
            raise ValueError("voice relations must reference snapshot characters")
        voice_keys.add(key)


def _stored_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
