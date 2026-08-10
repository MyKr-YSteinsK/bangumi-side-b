"""Direct SQLite access for clean subject fact snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
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
    QuarterAssignmentSource,
    SourceDecision,
    SourceType,
)


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
        if self.episode_count is not None and self.episode_count < 0:
            raise ValueError("episode count must not be negative")
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
class QuarterOwnership:
    quarter: Quarter
    assignment_source: QuarterAssignmentSource
    assignment_evidence: str

    def __post_init__(self) -> None:
        if not self.assignment_evidence.strip():
            raise ValueError("quarter assignment evidence must not be empty")


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
    quarter: QuarterOwnership | None = None
    cover: CoverRecord | None = None
    review_issues: tuple[ReviewIssue, ...] = ()


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
        self.replace_archive_quarter(
            connection, snapshot.subject.subject_id, snapshot.quarter
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

    def replace_archive_quarter(
        self,
        connection: sqlite3.Connection,
        subject_id: int,
        ownership: QuarterOwnership | None,
    ) -> None:
        connection.execute(
            "DELETE FROM subject_quarters WHERE subject_id = ?", (subject_id,)
        )
        if ownership is not None:
            connection.execute(
                """
                INSERT INTO subject_quarters (
                    subject_id, year, quarter_month, assignment_source,
                    assignment_evidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    subject_id,
                    ownership.quarter.year,
                    ownership.quarter.month,
                    ownership.assignment_source.value,
                    ownership.assignment_evidence,
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

    def get_subject_facts(self, subject_id: int) -> SubjectSnapshot | None:
        connection = self.database.connect()
        try:
            return self._subject_facts(connection, subject_id)
        finally:
            connection.close()

    def list_quarter_facts(self, quarter: Quarter) -> tuple[SubjectSnapshot, ...]:
        connection = self.database.connect()
        try:
            subject_ids = (
                row["subject_id"]
                for row in connection.execute(
                    """
                    SELECT subject_id FROM subject_quarters
                    WHERE year = ? AND quarter_month = ? ORDER BY subject_id
                    """,
                    (quarter.year, quarter.month),
                )
            )
            return tuple(
                snapshot
                for subject_id in subject_ids
                if (snapshot := self._subject_facts(connection, subject_id)) is not None
            )
        finally:
            connection.close()

    def affected_quarters(self, subject_ids: frozenset[int]) -> tuple[Quarter, ...]:
        if not subject_ids:
            return ()
        connection = self.database.connect()
        try:
            placeholders = ", ".join("?" for _ in subject_ids)
            rows = connection.execute(
                f"""
                SELECT DISTINCT year, quarter_month FROM subject_quarters
                WHERE subject_id IN ({placeholders})
                ORDER BY year, quarter_month
                """,
                tuple(sorted(subject_ids)),
            )
            return tuple(Quarter(row["year"], row["quarter_month"]) for row in rows)
        finally:
            connection.close()

    def delete_subjects(
        self, connection: sqlite3.Connection, subject_ids: frozenset[int]
    ) -> int:
        if not subject_ids:
            return 0
        placeholders = ", ".join("?" for _ in subject_ids)
        result = connection.execute(
            f"DELETE FROM subjects WHERE id IN ({placeholders})",
            tuple(sorted(subject_ids)),
        )
        return result.rowcount

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
            rows = connection.execute(
                f"""
                SELECT issue.*, subject.id FROM subject_review_issues AS issue
                JOIN subjects AS subject ON subject.id = issue.subject_id
                {where}
                ORDER BY issue.candidate_year, issue.candidate_quarter,
                         issue.issue_code, issue.subject_id
                """,
                parameters,
            )
            items: list[ReviewQueueItem] = []
            for row in rows:
                subject = self._subject_facts(connection, row["subject_id"])
                if subject is None:
                    raise RuntimeError("review issue subject is missing")
                candidate_quarter = (
                    Quarter(row["candidate_year"], row["candidate_quarter"])
                    if row["candidate_year"] is not None
                    else None
                )
                items.append(
                    ReviewQueueItem(
                        subject.subject,
                        ReviewIssue(
                            row["issue_code"],
                            candidate_quarter,
                            row["observed_value"],
                            json.loads(row["details_json"]),
                            row["detected_at"],
                        ),
                    )
                )
            return tuple(items)
        finally:
            connection.close()

    def _subject_facts(
        self, connection: sqlite3.Connection, subject_id: int
    ) -> SubjectSnapshot | None:
        row = connection.execute(
            "SELECT * FROM subjects WHERE id = ?", (subject_id,)
        ).fetchone()
        if row is None:
            return None
        aliases = tuple(
            child["title"]
            for child in connection.execute(
                """
                SELECT title FROM subject_titles
                WHERE subject_id = ? ORDER BY position
                """,
                (subject_id,),
            )
        )
        infobox = tuple(
            InfoboxItem(child["item_key"], json.loads(child["value_json"]))
            for child in connection.execute(
                """
                SELECT item_key, value_json FROM subject_infobox
                WHERE subject_id = ? ORDER BY position
                """,
                (subject_id,),
            )
        )
        tags = tuple(
            child["tag_name"]
            for child in connection.execute(
                """
                SELECT tag_name FROM subject_tags
                WHERE subject_id = ? ORDER BY position
                """,
                (subject_id,),
            )
        )
        source_row = connection.execute(
            "SELECT * FROM subject_sources WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        source = (
            SourceDecision(SourceType.UNKNOWN)
            if source_row is None
            else SourceDecision(
                SourceType(source_row["source_type"]),
                source_row["evidence_type"],
                source_row["evidence_value"],
            )
        )
        quarter_row = connection.execute(
            "SELECT * FROM subject_quarters WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        ownership = (
            None
            if quarter_row is None
            else QuarterOwnership(
                Quarter(quarter_row["year"], quarter_row["quarter_month"]),
                QuarterAssignmentSource(quarter_row["assignment_source"]),
                quarter_row["assignment_evidence"],
            )
        )
        cover_row = connection.execute(
            "SELECT * FROM subject_covers WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        cover = (
            None
            if cover_row is None
            else CoverRecord(
                cover_row["source_url"],
                cover_row["source_variant"],
                cover_row["content_hash"],
                cover_row["width"],
                cover_row["height"],
                cover_row["size_bytes"],
            )
        )
        review_issues = tuple(
            ReviewIssue(
                issue["issue_code"],
                (
                    Quarter(issue["candidate_year"], issue["candidate_quarter"])
                    if issue["candidate_year"] is not None
                    else None
                ),
                issue["observed_value"],
                json.loads(issue["details_json"]),
                issue["detected_at"],
            )
            for issue in connection.execute(
                """
                SELECT * FROM subject_review_issues
                WHERE subject_id = ? ORDER BY issue_code
                """,
                (subject_id,),
            )
        )
        subject = SubjectRecord(
            row["id"],
            row["name_original"],
            row["name_cn"],
            row["summary_raw"],
            MediaFormat(row["media_format"]),
            _stored_date(row["air_date"]),
            _stored_date(row["end_date"]),
            row["episode_count"],
            row["rating_score"],
            row["rating_count"],
            JapaneseDecision(
                _stored_japanese_classification(row["japanese_evidence_type"]),
                row["japanese_evidence_type"],
                row["japanese_evidence_value"],
            ),
        )
        return SubjectSnapshot(
            subject,
            aliases,
            infobox,
            tags,
            source,
            ownership,
            cover,
            review_issues,
        )


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


def _stored_japanese_classification(evidence_type: object) -> JapaneseClassification:
    if isinstance(evidence_type, str) and evidence_type.startswith("unresolved_"):
        return JapaneseClassification.UNRESOLVED
    return JapaneseClassification.ACCEPTED_JAPANESE
