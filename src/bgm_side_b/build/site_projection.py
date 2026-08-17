"""Deterministic projections for the single generated static site.

The projection layer deliberately knows about the clean archive schema, but it
does not expose SQL or SQLite rows to templates.  It turns the persisted fact
store into small immutable records and JSON-ready dictionaries.  Every sort and
serialization rule is explicit so the same facts produce byte-identical output.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from unicodedata import normalize

from bgm_side_b.config import TagRules
from bgm_side_b.database import Database
from bgm_side_b.domain import Quarter
from bgm_side_b.rules import display_summary, normalize_aliases

SCHEMA_VERSION = 1
PROJECTION_VERSION = "site-projection-v1"


class ProjectionError(RuntimeError):
    """Raised when facts cannot safely be projected to the public site."""


@dataclass(frozen=True)
class AppearanceFact:
    """One persisted quarter appearance for a subject."""

    quarter: Quarter
    kind: str
    assignment_source: str
    evidence_type: str
    evidence_value: str

    def __post_init__(self) -> None:
        if self.kind not in {"premiere", "continuing"}:
            raise ValueError("unsupported appearance kind")
        if self.kind == "continuing" and self.quarter is None:  # pragma: no cover
            raise ValueError("continuing appearance requires a quarter")


@dataclass(frozen=True)
class CoverFact:
    """Stored cover metadata plus the verified workspace-relative source."""

    content_hash: str
    size_bytes: int
    source_path: Path


@dataclass(frozen=True)
class SubjectFact:
    """Repository read model used by all public projections."""

    subject_id: int
    name_original: str
    name_cn: str | None
    summary_raw: str | None
    media_format: str
    air_date: date | None
    end_date: date | None
    episode_count: int | None
    rating_score: float | None
    rating_count: int | None
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    source: str
    appearances: tuple[AppearanceFact, ...]
    cover: CoverFact | None


@dataclass(frozen=True)
class SyncFactState:
    """The build-relevant portion of a per-quarter sync state."""

    facts_status: str
    covers_status: str


@dataclass(frozen=True)
class ArchiveFacts:
    """A complete, read-only snapshot of the current clean archive database."""

    subjects: tuple[SubjectFact, ...]
    sync_states: tuple[tuple[Quarter, SyncFactState], ...]
    review_quarters: tuple[Quarter, ...] = ()

    @property
    def state_by_quarter(self) -> MappingProxyType[Quarter, SyncFactState]:
        return MappingProxyType(dict(self.sync_states))

    @cached_property
    def by_quarter(
        self,
    ) -> MappingProxyType[
        Quarter, tuple[tuple[SubjectFact, AppearanceFact], ...]
    ]:
        grouped: defaultdict[Quarter, list[tuple[SubjectFact, AppearanceFact]]] = (
            defaultdict(list)
        )
        for subject in self.subjects:
            for appearance in subject.appearances:
                grouped[appearance.quarter].append((subject, appearance))
        return MappingProxyType(
            {
                quarter: tuple(
                    sorted(values, key=lambda item: _subject_sort_key(item[0]))
                )
                for quarter, values in sorted(grouped.items())
            }
        )


class ArchiveFactsReader:
    """Read one validated SQLite snapshot with a bounded set of queries."""

    def __init__(self, database: Database, workspace_directory: Path) -> None:
        self.database = database
        self.workspace_directory = workspace_directory.resolve()

    def read(self, excluded_subject_ids: frozenset[int] = frozenset()) -> ArchiveFacts:
        if not self.database.path.is_file():
            raise ProjectionError("database is missing")
        connection = self.database.connect()
        try:
            connection.execute("PRAGMA query_only = ON")
            self._assert_no_blacklist_residue(connection, excluded_subject_ids)
            subjects = self._read_subjects(connection, excluded_subject_ids)
            states = tuple(
                (
                    Quarter(row["year"], row["quarter_month"]),
                    SyncFactState(row["facts_status"], row["covers_status"]),
                )
                for row in connection.execute(
                    """
                    SELECT year, quarter_month, facts_status, covers_status
                    FROM sync_states ORDER BY year, quarter_month
                    """
                )
            )
            review_quarters = tuple(
                Quarter(row["candidate_year"], row["candidate_quarter"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT candidate_year, candidate_quarter
                    FROM subject_review_issues
                    WHERE candidate_year IS NOT NULL
                      AND candidate_quarter IS NOT NULL
                    ORDER BY candidate_year, candidate_quarter
                    """
                )
            )
            return ArchiveFacts(subjects, states, review_quarters)
        except sqlite3.Error as error:
            raise ProjectionError(
                "database cannot be read for site projection"
            ) from error
        finally:
            connection.close()

    def _assert_no_blacklist_residue(
        self, connection: sqlite3.Connection, excluded_subject_ids: frozenset[int]
    ) -> None:
        if not excluded_subject_ids:
            return
        placeholders = ", ".join("?" for _ in excluded_subject_ids)
        residue = connection.execute(
            f"SELECT id FROM subjects WHERE id IN ({placeholders}) ORDER BY id",
            tuple(sorted(excluded_subject_ids)),
        ).fetchall()
        if residue:
            ids = ", ".join(str(row["id"]) for row in residue)
            raise ProjectionError(f"blacklist residue in SQLite: {ids}")

    def _read_subjects(
        self,
        connection: sqlite3.Connection,
        excluded_subject_ids: frozenset[int],
    ) -> tuple[SubjectFact, ...]:
        rows = connection.execute(
            """
            SELECT id, name_original, name_cn, summary_raw, media_format,
                   air_date, end_date, episode_count, rating_score, rating_count
            FROM subjects ORDER BY id
            """
        ).fetchall()
        if not rows:
            return ()
        aliases = _group_strings(
            connection.execute(
                """
                SELECT subject_id, title FROM subject_titles
                ORDER BY subject_id, position
                """
            ).fetchall(),
            "title",
        )
        tags = _group_strings(
            connection.execute(
                """
                SELECT subject_id, tag_name FROM subject_tags
                ORDER BY subject_id, position
                """
            ).fetchall(),
            "tag_name",
        )
        source_rows = connection.execute(
            """
            SELECT subject_id, source_type FROM subject_sources
            ORDER BY subject_id
            """
        ).fetchall()
        sources = {row["subject_id"]: row["source_type"] for row in source_rows}
        appearance_rows = connection.execute(
            """
            SELECT subject_id, year, quarter_month, appearance_kind,
                   assignment_source, evidence_type, evidence_value
            FROM subject_quarters
            ORDER BY subject_id, year, quarter_month, appearance_kind
            """
        ).fetchall()
        appearances: defaultdict[int, list[AppearanceFact]] = defaultdict(list)
        for row in appearance_rows:
            appearances[row["subject_id"]].append(
                AppearanceFact(
                    Quarter(row["year"], row["quarter_month"]),
                    row["appearance_kind"],
                    row["assignment_source"],
                    row["evidence_type"],
                    row["evidence_value"],
                )
            )
        cover_rows = connection.execute(
            """
            SELECT subject_id, content_hash, size_bytes
            FROM subject_covers
            ORDER BY subject_id
            """
        ).fetchall()
        covers = {
            row["subject_id"]: _cover_fact(
                self.workspace_directory,
                row["subject_id"],
                row["content_hash"],
                row["size_bytes"],
            )
            for row in cover_rows
        }
        return tuple(
            SubjectFact(
                subject_id=row["id"],
                name_original=row["name_original"],
                name_cn=row["name_cn"],
                summary_raw=row["summary_raw"],
                media_format=row["media_format"],
                air_date=_parse_date(row["air_date"]),
                end_date=_parse_date(row["end_date"]),
                episode_count=_public_episode_count(row["episode_count"]),
                rating_score=row["rating_score"],
                rating_count=row["rating_count"],
                aliases=tuple(aliases.get(row["id"], ())),
                tags=tuple(tags.get(row["id"], ())),
                source=sources.get(row["id"], "unknown"),
                appearances=tuple(appearances.get(row["id"], ())),
                cover=covers.get(row["id"]),
            )
            for row in rows
            if row["id"] not in excluded_subject_ids
        )


@dataclass(frozen=True)
class SubjectProjection:
    """One subject/appearance record safe for JSON serialization."""

    subject_id: int
    preferred_title: str
    original_title: str | None
    aliases: tuple[str, ...]
    media_format: str
    episode_count: int | None
    air_date: str | None
    end_date: str | None
    rating_score: float | None
    rating_count: int | None
    source: str
    allowed_tags: tuple[str, ...]
    display_summary: str | None
    cover_url: str | None
    cover_hash: str | None
    appearance_kind: str
    quarter: str
    premiere_quarter: str | None
    bangumi_url: str

    def to_dict(self, *, list_tags: bool = False) -> dict[str, object]:
        tags = self.allowed_tags[:2] if list_tags else self.allowed_tags
        return {
            "subject_id": self.subject_id,
            "preferred_title": self.preferred_title,
            "original_title": self.original_title,
            "aliases": list(self.aliases),
            "media_format": self.media_format,
            "episode_count": self.episode_count,
            "air_date": self.air_date,
            "end_date": self.end_date,
            "rating_score": self.rating_score,
            "rating_count": self.rating_count,
            "source": self.source,
            "allowed_tags": list(tags),
            "display_summary": self.display_summary,
            "cover_url": self.cover_url,
            "appearance_kind": self.appearance_kind,
            "quarter": self.quarter,
            "premiere_quarter": self.premiere_quarter,
            "bangumi_url": self.bangumi_url,
        }


@dataclass(frozen=True)
class QuarterProjection:
    """The complete public payload for one archive quarter."""

    quarter: str
    tv_premiere: tuple[SubjectProjection, ...]
    tv_continuing: tuple[SubjectProjection, ...]
    movie_premiere: tuple[SubjectProjection, ...]
    warnings: tuple[str, ...] = ()
    fingerprint: str = ""

    @property
    def subject_ids(self) -> frozenset[int]:
        return frozenset(
            item.subject_id
            for group in (self.tv_premiere, self.tv_continuing, self.movie_premiere)
            for item in group
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "quarter": self.quarter,
            "revision": self.fingerprint,
            "tv": {
                "premiere": [item.to_dict() for item in self.tv_premiere],
                "continuing": [
                    item.to_dict() for item in self.tv_continuing
                ],
            },
            "movie": {
                "premiere": [
                    item.to_dict() for item in self.movie_premiere
                ]
            },
        }


@dataclass(frozen=True)
class YearCatalogProjection:
    """Light list/search records used to locate quarter-owned detail payloads."""

    year: int
    records: tuple[dict[str, object], ...]
    fingerprint: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "revision": self.fingerprint,
            "records": list(self.records),
        }


@dataclass(frozen=True)
class ArchiveIndexProjection:
    """Small global navigation/count index."""

    years: tuple[int, ...]
    quarters: tuple[dict[str, object], ...]
    latest_quarter: str | None
    fingerprint: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "years": list(self.years),
            "quarters": list(self.quarters),
            "latest_quarter": self.latest_quarter,
            "revision": self.fingerprint,
        }


def project_quarter(
    facts: ArchiveFacts,
    quarter: Quarter,
    tag_rules: TagRules,
    workspace_directory: Path,
) -> QuarterProjection:
    """Project the eligible appearances of one quarter."""
    grouped = facts.by_quarter.get(quarter, ())
    warnings: list[str] = []
    premiere_by_subject: dict[int, str | None] = {}
    for subject, _ in grouped:
        premiere = next(
            (item.quarter for item in subject.appearances if item.kind == "premiere"),
            None,
        )
        premiere_by_subject[subject.subject_id] = _quarter_label(premiere)
    projected: list[SubjectProjection] = []
    for subject, appearance in grouped:
        projected.append(
            _project_subject(
                subject,
                appearance,
                tag_rules,
                workspace_directory,
                premiere_by_subject[subject.subject_id],
                warnings,
            )
        )
    tv_premiere = _sorted_group(
        item
        for item in projected
        if item.media_format == "TV" and item.appearance_kind == "premiere"
    )
    tv_continuing = _sorted_group(
        item
        for item in projected
        if item.media_format == "TV" and item.appearance_kind == "continuing"
    )
    movie_premiere = _sorted_group(
        item
        for item in projected
        if item.media_format == "MOVIE" and item.appearance_kind == "premiere"
    )
    if any(
        item.media_format == "MOVIE" and item.appearance_kind == "continuing"
        for item in projected
    ):
        raise ProjectionError(f"movie continuing appearance in {quarter}")
    return QuarterProjection(
        _quarter_label(quarter),
        tuple(tv_premiere),
        tuple(tv_continuing),
        tuple(movie_premiere),
        tuple(sorted(set(warnings))),
    )


def project_year(
    year: int, quarters: tuple[QuarterProjection, ...]
) -> YearCatalogProjection:
    """Project display-safe appearance records needed for archive browsing."""
    records: list[dict[str, object]] = []
    for quarter in sorted(quarters, key=lambda item: item.quarter):
        for subject in (
            *quarter.tv_premiere,
            *quarter.tv_continuing,
            *quarter.movie_premiere,
        ):
            records.append(
                {
                    "id": subject.subject_id,
                    "quarter": quarter.quarter,
                    "appearance": subject.appearance_kind,
                    "media": subject.media_format,
                    "preferred_title": subject.preferred_title,
                    "original_title": subject.original_title,
                    "aliases": list(subject.aliases),
                    "air_date": subject.air_date,
                    "episode_count": subject.episode_count,
                    "score": subject.rating_score,
                    "rating_count": subject.rating_count,
                    "source": subject.source,
                    "allowed_tags": list(subject.allowed_tags),
                    "cover": subject.cover_url,
                }
            )
    return YearCatalogProjection(year, tuple(records))


def project_archive_index(
    quarters: tuple[QuarterProjection, ...],
) -> ArchiveIndexProjection:
    """Project counts and navigation without copying subject records."""
    ordered = tuple(sorted(quarters, key=lambda item: item.quarter))
    entries = tuple(
        {
            "quarter": item.quarter,
            "year": int(item.quarter[:4]),
            "tv_premiere": len(item.tv_premiere),
            "tv_continuing": len(item.tv_continuing),
            "movie_premiere": len(item.movie_premiere),
            "count": len(item.subject_ids),
            "revision": item.fingerprint,
        }
        for item in ordered
    )
    years = tuple(sorted({int(item.quarter[:4]) for item in ordered}))
    return ArchiveIndexProjection(
        years,
        entries,
        ordered[-1].quarter if ordered else None,
    )


def json_bytes(value: object) -> bytes:
    """Serialize a projection with one stable UTF-8 JSON policy."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _project_subject(
    subject: SubjectFact,
    appearance: AppearanceFact,
    tag_rules: TagRules,
    workspace_directory: Path,
    premiere_quarter: str | None,
    warnings: list[str],
) -> SubjectProjection:
    original = _clean(subject.name_original)
    chinese = _clean(subject.name_cn)
    preferred = chinese or original
    if preferred is None:
        raise ProjectionError(f"subject {subject.subject_id} has no usable title")
    original_title = original if original and original != preferred else None
    aliases = normalize_aliases(
        subject.aliases, excluded=(preferred, original_title or "")
    )
    selected_tags = _allowed_tags(subject.tags, tag_rules)
    cover_url, cover_hash = _verified_cover(subject, workspace_directory, warnings)
    return SubjectProjection(
        subject.subject_id,
        preferred,
        original_title,
        aliases,
        subject.media_format,
        subject.episode_count,
        _date_label(subject.air_date),
        _date_label(subject.end_date),
        subject.rating_score,
        subject.rating_count,
        subject.source,
        selected_tags,
        display_summary(subject.summary_raw),
        cover_url,
        cover_hash,
        appearance.kind,
        _quarter_label(appearance.quarter) or "",
        premiere_quarter,
        f"https://bgm.tv/subject/{subject.subject_id}",
    )


def _allowed_tags(values: tuple[str, ...], rules: TagRules) -> tuple[str, ...]:
    allowed = set(rules.allowed_tags)
    selected: set[str] = set()
    for value in values:
        normalized = normalize("NFKC", value).strip()
        if normalized in allowed:
            selected.add(normalized)
    return tuple(tag for tag in rules.allowed_tags if tag in selected)


def _verified_cover(
    subject: SubjectFact,
    workspace_directory: Path,
    warnings: list[str],
) -> tuple[str | None, str | None]:
    cover = subject.cover
    if cover is None:
        warnings.append(f"subject {subject.subject_id} has no cover")
        return None, None
    try:
        source = cover.source_path.resolve()
        if not source.is_relative_to(workspace_directory.resolve()):
            raise ValueError
        if not source.is_file() or source.stat().st_size != cover.size_bytes:
            raise ValueError
    except (OSError, ValueError):
        warnings.append(f"subject {subject.subject_id} has an invalid cover")
        return None, None
    return (
        f"covers/{subject.subject_id}.webp?v={cover.content_hash}",
        cover.content_hash,
    )


def _cover_fact(
    workspace: Path, subject_id: int, content_hash: str, size_bytes: int
) -> CoverFact:
    return CoverFact(
        content_hash,
        size_bytes,
        workspace / "covers" / f"{subject_id}.webp",
    )


def _sorted_group(items: object) -> list[SubjectProjection]:
    return sorted(items, key=_projection_sort_key)  # type: ignore[arg-type]


def _projection_sort_key(item: SubjectProjection) -> tuple[object, ...]:
    return (
        item.rating_score is None,
        -(item.rating_score or 0),
        -(item.rating_count or 0),
        item.air_date or "9999-12-31",
        item.subject_id,
    )


def _subject_sort_key(subject: SubjectFact) -> tuple[object, ...]:
    return (
        subject.rating_score is None,
        -(subject.rating_score or 0),
        -(subject.rating_count or 0),
        _date_label(subject.air_date) or "9999-12-31",
        subject.subject_id,
    )


def _group_strings(rows: list[sqlite3.Row], field: str) -> dict[int, tuple[str, ...]]:
    grouped: defaultdict[int, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["subject_id"]].append(row[field])
    return {subject_id: tuple(values) for subject_id, values in grouped.items()}


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = normalize("NFKC", value).strip()
    return value or None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _public_episode_count(value: object) -> int | None:
    """Treat legacy zero/invalid values as unknown in public projections."""
    return value if isinstance(value, int) and value > 0 else None


def _date_label(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _quarter_label(value: Quarter | None) -> str | None:
    return None if value is None else f"{value.year:04d}-{value.month:02d}"


def safe_relative_path(value: str) -> PurePosixPath:
    """Validate a generated relative path before it reaches the writer."""
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ProjectionError("generated path must be relative")
    return path
