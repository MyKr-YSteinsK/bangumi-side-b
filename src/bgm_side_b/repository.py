"""Direct SQLite access for clean subject fact snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath

from bgm_side_b.database import Database
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
    SourceDecision,
    SourceType,
)

_ID_QUERY_CHUNK_SIZE = 400


@dataclass(frozen=True)
class SubjectRecord:
    """The complete persisted row for an admitted or review-only subject."""

    subject_id: int
    name_original: str
    name_cn: str | None
    summary_raw: str | None
    media_format: MediaFormat
    air_date: date | None
    end_date: date | None
    episode_count: int | None
    rating_score: float | None
    rating_count: int | None
    japanese: JapaneseDecision

    def __post_init__(self) -> None:
        if self.subject_id <= 0:
            raise ValueError("subject id must be positive")
        if not self.name_original.strip():
            raise ValueError("subject original name must not be empty")
        if self.episode_count is not None and self.episode_count <= 0:
            raise ValueError("episode count must be positive when present")
        if self.rating_score is not None and not 0 <= self.rating_score <= 10:
            raise ValueError("rating score must be between 0 and 10")
        if self.rating_count is not None and self.rating_count < 0:
            raise ValueError("rating count must not be negative")
        if self.japanese.classification is JapaneseClassification.REJECTED_NON_JAPANESE:
            raise ValueError("rejected non-Japanese subjects must not be persisted")


@dataclass(frozen=True)
class InfoboxItem:
    item_key: str
    value: object


@dataclass(frozen=True)
class QuarterAppearance:
    """One persisted premiere or verified continuing quarter appearance."""

    quarter: Quarter
    appearance_kind: QuarterAppearanceKind
    assignment_source: QuarterAssignmentSource
    evidence_type: str
    evidence_value: str

    def __post_init__(self) -> None:
        if not self.evidence_type.strip() or not self.evidence_value.strip():
            raise ValueError("quarter appearance evidence must not be empty")


@dataclass(frozen=True)
class CoverRecord:
    source_url: str
    source_variant: str
    content_hash: str
    width: int
    height: int
    size_bytes: int


@dataclass(frozen=True)
class ReviewIssue:
    issue_code: str
    candidate_quarter: Quarter | None
    observed_value: str | None
    details: Mapping[str, object]
    detected_at: str


@dataclass(frozen=True)
class ReviewQueueItem:
    """One persisted issue paired with its locally stored subject facts."""

    subject: SubjectRecord
    issue: ReviewIssue


@dataclass(frozen=True)
class QuarterSyncState:
    quarter: Quarter
    facts_status: str
    covers_status: str
    subject_count: int
    missing_cover_count: int
    last_attempt_at: str
    last_success_at: str | None


@dataclass(frozen=True)
class SubjectSnapshot:
    subject: SubjectRecord
    aliases: tuple[str, ...] = ()
    infobox: tuple[InfoboxItem, ...] = ()
    tags: tuple[str, ...] = ()
    source: SourceDecision = field(
        default_factory=lambda: SourceDecision(SourceType.UNKNOWN)
    )
    premiere: QuarterAppearance | None = None
    continuing: tuple[QuarterAppearance, ...] = ()
    cover: CoverRecord | None = None
    review_issues: tuple[ReviewIssue, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.premiere is not None
            and self.premiere.appearance_kind is not QuarterAppearanceKind.PREMIERE
        ):
            raise ValueError("snapshot premiere must have premiere appearance kind")
        if any(
            item.appearance_kind is not QuarterAppearanceKind.CONTINUING
            for item in self.continuing
        ):
            raise ValueError("snapshot continuing rows must have continuing kind")
        if self.subject.media_format is MediaFormat.MOVIE and self.continuing:
            raise ValueError("movies cannot have continuing appearances")
        quarters = tuple(item.quarter for item in self.appearances)
        if len(quarters) != len(set(quarters)):
            raise ValueError("a subject cannot have duplicate quarter appearances")

    @property
    def appearances(self) -> tuple[QuarterAppearance, ...]:
        """Return the premiere followed by deterministic continuing appearances."""
        premiere = () if self.premiere is None else (self.premiere,)
        return premiere + tuple(
            sorted(self.continuing, key=lambda item: item.quarter)
        )


class SubjectRepository:
    """Small transaction-neutral operations over clean archive facts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.database.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_subject_snapshot(
        self, connection: sqlite3.Connection, snapshot: SubjectSnapshot
    ) -> None:
        """Replace every fact child for one subject inside the caller transaction."""
        self.upsert_subject(connection, snapshot.subject)
        self.replace_aliases(
            connection, snapshot.subject.subject_id, snapshot.aliases
        )
        self.replace_infobox(
            connection, snapshot.subject.subject_id, snapshot.infobox
        )
        self.replace_tags(connection, snapshot.subject.subject_id, snapshot.tags)
        self.replace_source(connection, snapshot.subject.subject_id, snapshot.source)
        self.replace_appearances(
            connection, snapshot.subject.subject_id, snapshot.appearances
        )
        self.replace_cover(connection, snapshot.subject.subject_id, snapshot.cover)
        self.replace_review_issues(
            connection, snapshot.subject.subject_id, snapshot.review_issues
        )

    def upsert_subject(
        self, connection: sqlite3.Connection, subject: SubjectRecord
    ) -> None:
        connection.execute(
            """
            INSERT INTO subjects (
                id, name_original, name_cn, summary_raw, media_format, air_date,
                end_date, episode_count, rating_score, rating_count,
                japanese_evidence_type, japanese_evidence_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name_original = excluded.name_original,
                name_cn = excluded.name_cn,
                summary_raw = excluded.summary_raw,
                media_format = excluded.media_format,
                air_date = excluded.air_date,
                end_date = excluded.end_date,
                episode_count = excluded.episode_count,
                rating_score = excluded.rating_score,
                rating_count = excluded.rating_count,
                japanese_evidence_type = excluded.japanese_evidence_type,
                japanese_evidence_value = excluded.japanese_evidence_value
            """,
            (
                subject.subject_id,
                subject.name_original,
                subject.name_cn,
                subject.summary_raw,
                subject.media_format.value,
                _date_value(subject.air_date),
                _date_value(subject.end_date),
                subject.episode_count,
                subject.rating_score,
                subject.rating_count,
                subject.japanese.evidence_type,
                subject.japanese.evidence_value,
            ),
        )

    def replace_aliases(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        aliases: Sequence[str],
    ) -> None:
        connection.execute(
            "DELETE FROM subject_titles WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_titles (subject_id, title, position)
            VALUES (?, ?, ?)
            """,
            ((subject_id, title, position) for position, title in enumerate(aliases)),
        )

    def replace_infobox(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        items: Sequence[InfoboxItem],
    ) -> None:
        connection.execute(
            "DELETE FROM subject_infobox WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_infobox (subject_id, position, item_key, value_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    position,
                    item.item_key,
                    json.dumps(
                        item.value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                for position, item in enumerate(items)
            ),
        )

    def replace_tags(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        tags: Sequence[str],
    ) -> None:
        connection.execute(
            "DELETE FROM subject_tags WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_tags (subject_id, tag_name, position)
            VALUES (?, ?, ?)
            """,
            ((subject_id, tag, position) for position, tag in enumerate(tags)),
        )

    def replace_source(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        source: SourceDecision,
    ) -> None:
        connection.execute(
            "DELETE FROM subject_sources WHERE subject_id = ?", (subject_id,)
        )
        connection.execute(
            """
            INSERT INTO subject_sources (
                subject_id, source_type, evidence_type, evidence_value
            ) VALUES (?, ?, ?, ?)
            """,
            (
                subject_id,
                source.source_type.value,
                source.evidence_type,
                source.evidence_value,
            ),
        )

    def replace_appearances(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        appearances: Sequence[QuarterAppearance],
    ) -> None:
        """Replace a subject's complete explicit appearance set atomically."""
        connection.execute(
            "DELETE FROM subject_quarters WHERE subject_id = ?", (subject_id,)
        )
        self._insert_appearances(connection, subject_id, appearances)

    def replace_premiere(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        premiere: QuarterAppearance | None,
    ) -> None:
        """Replace only the premiere row and preserve other continuing rows."""
        if premiere is not None:
            _require_kind(premiere, QuarterAppearanceKind.PREMIERE)
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE subject_id = ? AND appearance_kind = 'premiere'
            """,
            (subject_id,),
        )
        if premiere is None:
            return
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE subject_id = ? AND year = ? AND quarter_month = ?
            """,
            (subject_id, premiere.quarter.year, premiere.quarter.month),
        )
        self._insert_appearances(connection, subject_id, (premiere,))

    def upsert_continuing_appearance(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        appearance: QuarterAppearance,
    ) -> None:
        """Create or refresh one continuing row without touching a premiere."""
        _require_kind(appearance, QuarterAppearanceKind.CONTINUING)
        existing = connection.execute(
            """
            SELECT appearance_kind FROM subject_quarters
            WHERE subject_id = ? AND year = ? AND quarter_month = ?
            """,
            (subject_id, appearance.quarter.year, appearance.quarter.month),
        ).fetchone()
        if existing is not None and existing["appearance_kind"] == "premiere":
            raise ValueError("a continuing appearance cannot replace a premiere")
        self._insert_appearances(connection, subject_id, (appearance,))

    def remove_continuing_appearance(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        quarter: Quarter,
    ) -> None:
        """Remove one continuing row, leaving the premiere untouched."""
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE subject_id = ? AND year = ? AND quarter_month = ?
              AND appearance_kind = 'continuing'
            """,
            (subject_id, quarter.year, quarter.month),
        )

    def replace_automatic_continuing_for_quarter(
        self,
        connection: sqlite3.Connection,
        quarter: Quarter,
        appearances: Sequence[tuple[int, QuarterAppearance]],
    ) -> None:
        """Atomically reconcile automatic continuing rows for one target quarter."""
        for subject_id, appearance in appearances:
            if subject_id <= 0 or appearance.quarter != quarter:
                raise ValueError("continuing appearance must match its target quarter")
            _require_kind(appearance, QuarterAppearanceKind.CONTINUING)
            if appearance.assignment_source is not QuarterAssignmentSource.AUTOMATIC:
                raise ValueError(
                    "bulk continuing replacement accepts automatic rows only"
                )
        connection.execute(
            """
            DELETE FROM subject_quarters
            WHERE year = ? AND quarter_month = ?
              AND appearance_kind = 'continuing' AND assignment_source = 'automatic'
            """,
            (quarter.year, quarter.month),
        )
        for subject_id, appearance in appearances:
            self.upsert_continuing_appearance(connection, subject_id, appearance)

    def _insert_appearances(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        appearances: Sequence[QuarterAppearance],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO subject_quarters (
                subject_id, year, quarter_month, appearance_kind, assignment_source,
                evidence_type, evidence_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_id, year, quarter_month) DO UPDATE SET
                appearance_kind = excluded.appearance_kind,
                assignment_source = excluded.assignment_source,
                evidence_type = excluded.evidence_type,
                evidence_value = excluded.evidence_value
            """,
            (
                (
                    subject_id,
                    appearance.quarter.year,
                    appearance.quarter.month,
                    appearance.appearance_kind.value,
                    appearance.assignment_source.value,
                    appearance.evidence_type,
                    appearance.evidence_value,
                )
                for appearance in appearances
            ),
        )

    def replace_cover(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        cover: CoverRecord | None,
    ) -> None:
        connection.execute(
            "DELETE FROM subject_covers WHERE subject_id = ?", (subject_id,)
        )
        if cover is not None:
            connection.execute(
                """
                INSERT INTO subject_covers (
                    subject_id, source_url, source_variant, content_hash,
                    width, height, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_id,
                    cover.source_url,
                    cover.source_variant,
                    cover.content_hash,
                    cover.width,
                    cover.height,
                    cover.size_bytes,
                ),
            )

    def replace_review_issues(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        issues: Sequence[ReviewIssue],
    ) -> None:
        connection.execute(
            "DELETE FROM subject_review_issues WHERE subject_id = ?", (subject_id,)
        )
        connection.executemany(
            """
            INSERT INTO subject_review_issues (
                subject_id, issue_code, candidate_year, candidate_quarter,
                observed_value, details_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    subject_id,
                    issue.issue_code,
                    issue.candidate_quarter.year if issue.candidate_quarter else None,
                    issue.candidate_quarter.month if issue.candidate_quarter else None,
                    issue.observed_value,
                    json.dumps(
                        dict(issue.details),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    issue.detected_at,
                )
                for issue in issues
            ),
        )

    def delete_review_issues_for_quarter(
        self,
        connection: sqlite3.Connection,
        quarter: Quarter,
        issue_codes: Iterable[str],
    ) -> None:
        """Delete selected issue families only within one candidate quarter."""
        codes = tuple(sorted(set(issue_codes)))
        if not codes:
            return
        placeholders = ", ".join("?" for _ in codes)
        connection.execute(
            f"""
            DELETE FROM subject_review_issues
            WHERE candidate_year = ? AND candidate_quarter = ?
              AND issue_code IN ({placeholders})
            """,
            (quarter.year, quarter.month, *codes),
        )

    def get_subject_facts(self, subject_id: int) -> SubjectSnapshot | None:
        connection = self.database.connect()
        try:
            return self._subject_facts_many(connection, (subject_id,)).get(subject_id)
        finally:
            connection.close()

    def get_subject_facts_many(
        self, subject_ids: Iterable[int]
    ) -> dict[int, SubjectSnapshot]:
        """Read existing snapshots with bounded bulk queries per child table."""
        connection = self.database.connect()
        try:
            return self._subject_facts_many(connection, subject_ids)
        finally:
            connection.close()

    def _subject_facts_many(
        self,
        connection: sqlite3.Connection,
        subject_ids: Iterable[int],
    ) -> dict[int, SubjectSnapshot]:
        snapshots: dict[int, SubjectSnapshot] = {}
        for requested_ids in _id_chunks(subject_ids):
            placeholders = ", ".join("?" for _ in requested_ids)
            subject_rows = tuple(
                connection.execute(
                    f"SELECT * FROM subjects WHERE id IN ({placeholders}) ORDER BY id",
                    requested_ids,
                )
            )
            found_ids = tuple(row["id"] for row in subject_rows)
            if not found_ids:
                continue
            found_placeholders = ", ".join("?" for _ in found_ids)
            aliases: dict[int, list[str]] = {subject_id: [] for subject_id in found_ids}
            for row in connection.execute(
                f"""
                SELECT subject_id, title FROM subject_titles
                WHERE subject_id IN ({found_placeholders})
                ORDER BY subject_id, position
                """,
                found_ids,
            ):
                aliases[row["subject_id"]].append(row["title"])
            infobox: dict[int, list[InfoboxItem]] = {
                subject_id: [] for subject_id in found_ids
            }
            for row in connection.execute(
                f"""
                SELECT subject_id, item_key, value_json FROM subject_infobox
                WHERE subject_id IN ({found_placeholders})
                ORDER BY subject_id, position
                """,
                found_ids,
            ):
                infobox[row["subject_id"]].append(
                    InfoboxItem(row["item_key"], json.loads(row["value_json"]))
                )
            tags: dict[int, list[str]] = {subject_id: [] for subject_id in found_ids}
            for row in connection.execute(
                f"""
                SELECT subject_id, tag_name FROM subject_tags
                WHERE subject_id IN ({found_placeholders})
                ORDER BY subject_id, position
                """,
                found_ids,
            ):
                tags[row["subject_id"]].append(row["tag_name"])
            sources = {
                row["subject_id"]: _source_from_row(row)
                for row in connection.execute(
                    f"""
                    SELECT * FROM subject_sources
                    WHERE subject_id IN ({found_placeholders})
                    """,
                    found_ids,
                )
            }
            appearances: dict[int, list[QuarterAppearance]] = {
                subject_id: [] for subject_id in found_ids
            }
            for row in connection.execute(
                f"""
                SELECT * FROM subject_quarters
                WHERE subject_id IN ({found_placeholders})
                ORDER BY subject_id, appearance_kind = 'premiere' DESC,
                         year, quarter_month
                """,
                found_ids,
            ):
                appearances[row["subject_id"]].append(_appearance_from_row(row))
            covers = {
                row["subject_id"]: _cover_from_row(row)
                for row in connection.execute(
                    f"""
                    SELECT * FROM subject_covers
                    WHERE subject_id IN ({found_placeholders})
                    """,
                    found_ids,
                )
            }
            reviews: dict[int, list[ReviewIssue]] = {
                subject_id: [] for subject_id in found_ids
            }
            for row in connection.execute(
                f"""
                SELECT * FROM subject_review_issues
                WHERE subject_id IN ({found_placeholders})
                ORDER BY subject_id, issue_code
                """,
                found_ids,
            ):
                reviews[row["subject_id"]].append(_review_issue_from_row(row))
            for row in subject_rows:
                subject_id = row["id"]
                subject_appearances = tuple(appearances[subject_id])
                snapshots[subject_id] = SubjectSnapshot(
                    subject=_subject_from_row(row),
                    aliases=tuple(aliases[subject_id]),
                    infobox=tuple(infobox[subject_id]),
                    tags=tuple(tags[subject_id]),
                    source=sources.get(subject_id, SourceDecision(SourceType.UNKNOWN)),
                    premiere=next(
                        (
                            item
                            for item in subject_appearances
                            if item.appearance_kind
                            is QuarterAppearanceKind.PREMIERE
                        ),
                        None,
                    ),
                    continuing=tuple(
                        item
                        for item in subject_appearances
                        if item.appearance_kind
                        is QuarterAppearanceKind.CONTINUING
                    ),
                    cover=covers.get(subject_id),
                    review_issues=tuple(reviews[subject_id]),
                )
        return snapshots

    def get_premiere_appearance(self, subject_id: int) -> QuarterAppearance | None:
        """Return a subject's unique permanent premiere appearance, if assigned."""
        connection = self.database.connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM subject_quarters
                WHERE subject_id = ? AND appearance_kind = 'premiere'
                """,
                (subject_id,),
            ).fetchone()
            return None if row is None else _appearance_from_row(row)
        finally:
            connection.close()

    def list_subjects_appearing_in_quarter(
        self,
        quarter: Quarter,
        *,
        appearance_kind: QuarterAppearanceKind | None = None,
    ) -> tuple[SubjectSnapshot, ...]:
        """List snapshots whose explicit appearances include ``quarter``."""
        connection = self.database.connect()
        try:
            parameters: tuple[object, ...] = (quarter.year, quarter.month)
            kind_clause = ""
            if appearance_kind is not None:
                kind_clause = " AND appearance_kind = ?"
                parameters += (appearance_kind.value,)
            subject_ids = tuple(
                row["subject_id"]
                for row in connection.execute(
                    f"""
                    SELECT subject_id FROM subject_quarters
                    WHERE year = ? AND quarter_month = ?
                    {kind_clause}
                    ORDER BY subject_id
                    """,
                    parameters,
                )
            )
            snapshots = self._subject_facts_many(connection, subject_ids)
            return tuple(
                snapshots[subject_id]
                for subject_id in subject_ids
                if subject_id in snapshots
            )
        finally:
            connection.close()

    def list_tv_subjects_appearing_in_previous_quarter(
        self, target_quarter: Quarter
    ) -> tuple[SubjectSnapshot, ...]:
        """Return the carry-forward TV candidates for a target quarter."""
        previous = _previous_quarter(target_quarter)
        return tuple(
            snapshot
            for snapshot in self.list_subjects_appearing_in_quarter(previous)
            if snapshot.subject.media_format is MediaFormat.TV
        )

    def affected_quarters(self, subject_ids: frozenset[int]) -> tuple[Quarter, ...]:
        if not subject_ids:
            return ()
        connection = self.database.connect()
        try:
            quarters = {
                Quarter(row["year"], row["quarter_month"])
                for chunk in _id_chunks(subject_ids)
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT year, quarter_month FROM subject_quarters
                    WHERE subject_id IN ({", ".join("?" for _ in chunk)})
                    """,
                    chunk,
                )
            }
            return tuple(sorted(quarters))
        finally:
            connection.close()

    def delete_subjects(
        self, connection: sqlite3.Connection, subject_ids: frozenset[int]
    ) -> int:
        if not subject_ids:
            return 0
        deleted = 0
        for chunk in _id_chunks(subject_ids):
            result = connection.execute(
                f"DELETE FROM subjects WHERE id IN "
                f"({', '.join('?' for _ in chunk)})",
                chunk,
            )
            deleted += result.rowcount
        return deleted

    def write_sync_state(
        self, connection: sqlite3.Connection, state: QuarterSyncState
    ) -> None:
        connection.execute(
            """
            INSERT INTO sync_states (
                year, quarter_month, facts_status, covers_status, subject_count,
                missing_cover_count, last_attempt_at, last_success_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(year, quarter_month) DO UPDATE SET
                facts_status = excluded.facts_status,
                covers_status = excluded.covers_status,
                subject_count = excluded.subject_count,
                missing_cover_count = excluded.missing_cover_count,
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at
            """,
            (
                state.quarter.year,
                state.quarter.month,
                state.facts_status,
                state.covers_status,
                state.subject_count,
                state.missing_cover_count,
                state.last_attempt_at,
                state.last_success_at,
            ),
        )

    def get_sync_state(self, quarter: Quarter) -> QuarterSyncState | None:
        connection = self.database.connect()
        try:
            row = connection.execute(
                """
                SELECT facts_status, covers_status, subject_count,
                       missing_cover_count, last_attempt_at, last_success_at
                FROM sync_states WHERE year = ? AND quarter_month = ?
                """,
                (quarter.year, quarter.month),
            ).fetchone()
            if row is None:
                return None
            return QuarterSyncState(
                quarter,
                row["facts_status"],
                row["covers_status"],
                row["subject_count"],
                row["missing_cover_count"],
                row["last_attempt_at"],
                row["last_success_at"],
            )
        finally:
            connection.close()

    def list_review_issues(
        self, quarter: Quarter | None = None
    ) -> tuple[ReviewQueueItem, ...]:
        """Return deterministic unresolved review rows without mutating SQLite."""
        connection = self.database.connect()
        try:
            parameters: tuple[object, ...] = ()
            where = ""
            if quarter is not None:
                where = "WHERE issue.candidate_year = ? AND issue.candidate_quarter = ?"
                parameters = (quarter.year, quarter.month)
            rows = tuple(
                connection.execute(
                    f"""
                    SELECT issue.*, subject.id FROM subject_review_issues AS issue
                    JOIN subjects AS subject ON subject.id = issue.subject_id
                    {where}
                    ORDER BY issue.candidate_year, issue.candidate_quarter,
                             issue.issue_code, issue.subject_id
                    """,
                    parameters,
                )
            )
            snapshots = self._subject_facts_many(
                connection, (row["subject_id"] for row in rows)
            )
            items: list[ReviewQueueItem] = []
            for row in rows:
                snapshot = snapshots.get(row["subject_id"])
                if snapshot is None:
                    raise RuntimeError("review issue subject is missing")
                items.append(
                    ReviewQueueItem(
                        snapshot.subject,
                        _review_issue_from_row(row),
                    )
                )
            return tuple(items)
        finally:
            connection.close()

def cover_relative_path(subject_id: int) -> PurePosixPath:
    """Derive the only supported cover path without storing it in SQLite."""
    if subject_id <= 0:
        raise ValueError("subject id must be positive")
    return PurePosixPath("covers", f"{subject_id}.webp")


def _date_value(value: date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("archive dates must not contain a time")
    return value.isoformat()


def _stored_date(value: object) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) else None


def _stored_episode_count(value: object) -> int | None:
    """Expose legacy zero episode counts as unknown without rewriting SQLite."""
    return value if isinstance(value, int) and value > 0 else None


def _stored_japanese_classification(evidence_type: object) -> JapaneseClassification:
    if isinstance(evidence_type, str) and evidence_type.startswith("unresolved_"):
        return JapaneseClassification.UNRESOLVED
    return JapaneseClassification.ACCEPTED_JAPANESE


def _id_chunks(subject_ids: Iterable[int]) -> Iterator[tuple[int, ...]]:
    ordered = tuple(sorted(set(subject_ids)))
    for offset in range(0, len(ordered), _ID_QUERY_CHUNK_SIZE):
        yield ordered[offset : offset + _ID_QUERY_CHUNK_SIZE]


def _subject_from_row(row: sqlite3.Row) -> SubjectRecord:
    return SubjectRecord(
        row["id"],
        row["name_original"],
        row["name_cn"],
        row["summary_raw"],
        MediaFormat(row["media_format"]),
        _stored_date(row["air_date"]),
        _stored_date(row["end_date"]),
        _stored_episode_count(row["episode_count"]),
        row["rating_score"],
        row["rating_count"],
        JapaneseDecision(
            _stored_japanese_classification(row["japanese_evidence_type"]),
            row["japanese_evidence_type"],
            row["japanese_evidence_value"],
        ),
    )


def _source_from_row(row: sqlite3.Row) -> SourceDecision:
    return SourceDecision(
        SourceType(row["source_type"]),
        row["evidence_type"],
        row["evidence_value"],
    )


def _cover_from_row(row: sqlite3.Row) -> CoverRecord:
    return CoverRecord(
        row["source_url"],
        row["source_variant"],
        row["content_hash"],
        row["width"],
        row["height"],
        row["size_bytes"],
    )


def _review_issue_from_row(row: sqlite3.Row) -> ReviewIssue:
    return ReviewIssue(
        row["issue_code"],
        (
            Quarter(row["candidate_year"], row["candidate_quarter"])
            if row["candidate_year"] is not None
            else None
        ),
        row["observed_value"],
        json.loads(row["details_json"]),
        row["detected_at"],
    )


def _appearance_from_row(row: sqlite3.Row) -> QuarterAppearance:
    return QuarterAppearance(
        Quarter(row["year"], row["quarter_month"]),
        QuarterAppearanceKind(row["appearance_kind"]),
        QuarterAssignmentSource(row["assignment_source"]),
        row["evidence_type"],
        row["evidence_value"],
    )


def _require_kind(
    appearance: QuarterAppearance, expected: QuarterAppearanceKind
) -> None:
    if appearance.appearance_kind is not expected:
        raise ValueError(f"appearance must have {expected.value} kind")


def _previous_quarter(quarter: Quarter) -> Quarter:
    if quarter.month == 1:
        return Quarter(quarter.year - 1, 10)
    return Quarter(quarter.year, quarter.month - 3)
