"""Fixed-query, read-only SQLite snapshots for static build projection."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from bgm_side_b.legacy_database import MIGRATIONS, Database


class BuildDataError(RuntimeError):
    """Raised when a database cannot safely supply static build facts."""


@dataclass(frozen=True)
class SubjectFacts:
    """Raw immutable facts for one subject, never exposed to templates."""

    subject_id: int
    subject_type: int | None
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
    country_infobox: tuple[tuple[str, str], ...]
    cover: sqlite3.Row | None
    episodes: tuple[sqlite3.Row, ...]


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
        navigation: tuple[tuple[int, int], ...] | None = None,
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
            navigation_rows = (
                navigation
                if navigation is not None
                else _navigation_rows(connection, excluded_subject_ids)
            )
            if not subject_ids:
                return QuarterFacts(year, month, schema_version, navigation_rows, ())

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
            infobox = _group_rows(
                _rows_for_subjects(
                    connection,
                    """
                    SELECT subject_id, item_key, value_json, position
                    FROM subject_infobox_items
                    WHERE subject_id IN ({placeholders})
                    ORDER BY subject_id, position
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
            subjects = tuple(
                SubjectFacts(
                    subject_id=row["id"],
                    subject_type=row["subject_type"],
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
                    country_infobox=tuple(
                        (item["item_key"], value)
                        for item in infobox.get(row["id"], ())
                        if isinstance((value := _json_string(item["value_json"])), str)
                    ),
                    cover=covers.get(row["id"]),
                    episodes=tuple(episodes.get(row["id"], ())),
                )
                for row in subject_rows
            )
            return QuarterFacts(year, month, schema_version, navigation_rows, subjects)
        except sqlite3.Error as error:
            raise BuildDataError("database cannot be read for build") from error
        finally:
            connection.close()

    def list_quarters(
        self, excluded_subject_ids: frozenset[int] = frozenset()
    ) -> tuple[tuple[int, int], ...]:
        """Return all buildable database quarters without opening a write path."""
        if not self.database.path.is_file():
            raise BuildDataError("database is missing")
        connection = self.database.connect()
        try:
            connection.execute("PRAGMA query_only = ON")
            _schema_version(connection)
            return _navigation_rows(connection, excluded_subject_ids)
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
                           WHEN 'new' THEN 1 ELSE 2 END,
                           quarter.position, quarter.subject_id
                   ) AS relation_rank
            FROM subject_quarters AS quarter
            WHERE quarter.year = ? AND quarter.month = ?
              AND quarter.appearance_kind = 'new'
        )
        SELECT subject.id, subject.subject_type, quarter_subjects.appearance_kind,
               quarter_subjects.position, subject.media_format, subject.summary,
               subject.air_date,
               subject.end_date,
               subject.episode_count, subject.total_episode_count, subject.rating_score,
               subject.rating_count
        FROM quarter_subjects
        JOIN subjects AS subject ON subject.id = quarter_subjects.subject_id
        WHERE quarter_subjects.relation_rank = 1
          AND subject.availability_status = 'available'
          AND subject.media_format = 'tv'
          {exclusions}
        ORDER BY quarter_subjects.position, subject.id
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
          AND subject.media_format = 'tv'
          AND quarter.appearance_kind = 'new'
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


def _json_string(value: object) -> str | None:
    """Decode only stored string Infobox values; structured values are ineligible."""
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) else None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
