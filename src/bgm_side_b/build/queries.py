"""Fixed-query, read-only SQLite snapshots for static build projection."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from bgm_side_b.database import MIGRATIONS, Database


class BuildDataError(RuntimeError):
    """Raised when a database cannot safely supply static build facts."""


@dataclass(frozen=True)
class SubjectFacts:
    """Raw immutable facts for one subject, never exposed to templates."""

    subject_id: int
    section: str
    section_position: int
    media_format: str
    summary: str | None
    air_date: date | None
    end_date: date | None
    episode_count: int | None
    total_episode_count: int | None
    rating_score: float | None
    rating_count: int | None
    titles: tuple[tuple[str, str], ...]
    raw_tags: tuple[str, ...]
    sources: tuple[str, ...]
    cover: sqlite3.Row | None
    episodes: tuple[sqlite3.Row, ...]
    characters: tuple[sqlite3.Row, ...]
    voices: tuple[sqlite3.Row, ...]
    character_media: tuple[sqlite3.Row, ...]


@dataclass(frozen=True)
class QuarterFacts:
    """A complete bounded query snapshot for a quarter build."""

    year: int
    month: int
    schema_version: int
    navigation: tuple[tuple[int, int], ...]
    subjects: tuple[SubjectFacts, ...]


class BuildQueries:
    """Read one complete static-build snapshot without per-card connections."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def load_quarter(
        self,
        year: int,
        month: int,
        *,
        excluded_subject_ids: frozenset[int] = frozenset(),
    ) -> QuarterFacts:
        """Load all card and detail facts with a fixed number of SQL queries."""
        if not self.database.path.is_file():
            raise BuildDataError("database is missing")
        connection = self.database.connect()
        try:
            connection.execute("PRAGMA query_only = ON")
            schema_version = _schema_version(connection)
            subject_rows = _subject_rows(connection, year, month, excluded_subject_ids)
            subject_ids = tuple(row["id"] for row in subject_rows)
            navigation = _navigation_rows(connection, excluded_subject_ids)
            if not subject_ids:
                return QuarterFacts(year, month, schema_version, navigation, ())

            titles = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT subject_id, title_kind, title, position
                    FROM subject_titles
                    WHERE subject_id IN ({placeholders})
                    ORDER BY subject_id, position, title_kind, title
                    """,
                    subject_ids,
                )
            )
            tags = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT subject_id, tag_name, position
                    FROM subject_raw_tags
                    WHERE subject_id IN ({placeholders})
                    ORDER BY subject_id, position, tag_name
                    """,
                    subject_ids,
                )
            )
            sources = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT subject_id, source, id
                    FROM subject_sources
                    WHERE subject_id IN ({placeholders})
                    ORDER BY subject_id, id
                    """,
                    subject_ids,
                )
            )
            covers = _owner_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT owner_id, media_kind, local_path, content_hash, size_bytes,
                           mime_type, width, height, status
                    FROM media_files
                    WHERE owner_type = 'subject' AND media_kind = 'cover'
                      AND owner_id IN ({placeholders})
                    ORDER BY owner_id
                    """,
                    subject_ids,
                )
            )
            episodes = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT subject_id, id, episode_number, sort_number, name, name_cn,
                           air_date, duration_seconds, position
                    FROM episodes
                    WHERE subject_id IN ({placeholders}) AND episode_type = 0
                    ORDER BY subject_id, position, episode_number, sort_number, id
                    """,
                    subject_ids,
                )
            )
            characters = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT relation.subject_id, relation.character_id, relation.role,
                           relation.position, character.original_name,
                           character.chinese_name, character.summary
                    FROM subject_characters AS relation
                    JOIN characters AS character ON character.id = relation.character_id
                    WHERE relation.subject_id IN ({placeholders})
                    ORDER BY relation.subject_id, relation.position,
                             relation.character_id
                    """,
                    subject_ids,
                )
            )
            voices = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT voice.subject_id, voice.character_id, voice.person_id,
                           voice.language, voice.position, person.original_name,
                           person.chinese_name
                    FROM character_voices AS voice
                    JOIN persons AS person ON person.id = voice.person_id
                    WHERE voice.subject_id IN ({placeholders})
                    ORDER BY voice.subject_id, voice.character_id, voice.position,
                             voice.person_id
                    """,
                    subject_ids,
                )
            )
            character_ids = tuple(
                row["character_id"]
                for rows in characters.values()
                for row in rows
            )
            character_media = _owner_rows(
                _rows_for_owners(
                    connection,
                    """
                    SELECT owner_id, media_kind, local_path, content_hash, size_bytes,
                           mime_type, width, height, status
                    FROM media_files
                    WHERE owner_type = 'character' AND media_kind = 'character_image'
                      AND owner_id IN ({placeholders})
                    ORDER BY owner_id
                    """,
                    character_ids,
                )
            )
            subjects = tuple(
                SubjectFacts(
                    subject_id=row["id"],
                    section=row["appearance_kind"],
                    section_position=row["position"],
                    media_format=row["media_format"],
                    summary=row["summary"],
                    air_date=_parse_date(row["air_date"]),
                    end_date=_parse_date(row["end_date"]),
                    episode_count=row["episode_count"],
                    total_episode_count=row["total_episode_count"],
                    rating_score=row["rating_score"],
                    rating_count=row["rating_count"],
                    titles=tuple(
                        (title["title_kind"], title["title"])
                        for title in titles.get(row["id"], ())
                    ),
                    raw_tags=tuple(tag["tag_name"] for tag in tags.get(row["id"], ())),
                    sources=tuple(
                        source["source"] for source in sources.get(row["id"], ())
                    ),
                    cover=covers.get(row["id"]),
                    episodes=tuple(episodes.get(row["id"], ())),
                    characters=tuple(characters.get(row["id"], ())),
                    voices=tuple(voices.get(row["id"], ())),
                    character_media=tuple(
                        media
                        for character in characters.get(row["id"], ())
                        if (media := character_media.get(character["character_id"]))
                        is not None
                    ),
                )
                for row in subject_rows
            )
            return QuarterFacts(year, month, schema_version, navigation, subjects)
        except sqlite3.Error as error:
            raise BuildDataError("database cannot be read for build") from error
        finally:
            connection.close()


def _schema_version(connection: sqlite3.Connection) -> int:
    """Reject absent or older schemas instead of attempting a build migration."""
    try:
        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()
    except sqlite3.Error as error:
        raise BuildDataError("database schema is unsupported") from error
    version = row["version"]
    expected = MIGRATIONS[-1].version
    if version != expected:
        raise BuildDataError("database schema is unsupported")
    return version


def _subject_rows(
    connection: sqlite3.Connection,
    year: int,
    month: int,
    excluded_subject_ids: frozenset[int],
) -> tuple[sqlite3.Row, ...]:
    exclusions, exclusion_values = _exclusion_sql(excluded_subject_ids, "subject.id")
    rows = connection.execute(
        f"""
        WITH quarter_subjects AS (
            SELECT quarter.subject_id, quarter.appearance_kind, quarter.position,
                   ROW_NUMBER() OVER (
                       PARTITION BY quarter.subject_id
                       ORDER BY CASE quarter.appearance_kind
                           WHEN 'new' THEN 1 WHEN 'continuing' THEN 2
                           WHEN 'movie' THEN 3
                           ELSE 4 END, quarter.position, quarter.subject_id
                   ) AS relation_rank
            FROM subject_quarters AS quarter
            WHERE quarter.year = ? AND quarter.month = ?
              AND quarter.appearance_kind IN ('new', 'continuing', 'movie')
        )
        SELECT subject.id, quarter_subjects.appearance_kind, quarter_subjects.position,
               subject.media_format, subject.summary, subject.air_date,
               subject.end_date,
               subject.episode_count, subject.total_episode_count, subject.rating_score,
               subject.rating_count
        FROM quarter_subjects
        JOIN subjects AS subject ON subject.id = quarter_subjects.subject_id
        WHERE quarter_subjects.relation_rank = 1
          AND subject.availability_status = 'available'
          AND subject.media_format IN ('tv', 'movie')
          {exclusions}
        ORDER BY CASE quarter_subjects.appearance_kind
            WHEN 'new' THEN 1 WHEN 'continuing' THEN 2 WHEN 'movie' THEN 3 ELSE 4 END,
            quarter_subjects.position, subject.id
        """,
        (year, month, *exclusion_values),
    ).fetchall()
    return tuple(rows)


def _navigation_rows(
    connection: sqlite3.Connection, excluded_subject_ids: frozenset[int]
) -> tuple[tuple[int, int], ...]:
    exclusions, exclusion_values = _exclusion_sql(excluded_subject_ids, "subject.id")
    rows = connection.execute(
        f"""
        SELECT DISTINCT quarter.year, quarter.month
        FROM subject_quarters AS quarter
        JOIN subjects AS subject ON subject.id = quarter.subject_id
        WHERE subject.availability_status = 'available'
          AND subject.media_format IN ('tv', 'movie')
          {exclusions}
        ORDER BY quarter.year, quarter.month
        """,
        exclusion_values,
    ).fetchall()
    return tuple((row["year"], row["month"]) for row in rows)


def _exclusion_sql(
    subject_ids: frozenset[int], column: str
) -> tuple[str, tuple[int, ...]]:
    values = tuple(sorted(subject_ids))
    if not values:
        return "", ()
    placeholders = ", ".join("?" for _ in values)
    return f"AND {column} NOT IN ({placeholders})", values


def _rows_for_subjects(
    connection: sqlite3.Connection, query: str, subject_ids: tuple[int, ...]
) -> tuple[sqlite3.Row, ...]:
    return _rows_for_owners(connection, query, subject_ids)


def _rows_for_owners(
    connection: sqlite3.Connection, query: str, owner_ids: tuple[int, ...]
) -> tuple[sqlite3.Row, ...]:
    if not owner_ids:
        return ()
    placeholders = ", ".join("?" for _ in owner_ids)
    return tuple(connection.execute(query.format(placeholders=placeholders), owner_ids))


def _group_rows(rows: tuple[sqlite3.Row, ...]) -> dict[int, tuple[sqlite3.Row, ...]]:
    grouped: defaultdict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row["subject_id"]].append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _owner_rows(rows: tuple[sqlite3.Row, ...]) -> dict[int, sqlite3.Row]:
    return {row["owner_id"]: row for row in rows}


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
