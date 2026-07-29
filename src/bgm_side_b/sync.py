# ruff: noqa: E501
"""Subject-only synchronisation orchestration and safe JSON reports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bgm_side_b.api import (
    ApiInfoboxItem,
    BangumiApiClient,
    BangumiApiError,
    CandidateSubject,
    DiscoveryResult,
    QuarterlyDiscovery,
    SubjectDetail,
)
from bgm_side_b.config import ProjectSettings, SourceRules, TagRules
from bgm_side_b.repository import (
    RawTag,
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectSource,
    SubjectTitle,
    SyncState,
)
from bgm_side_b.rules import (
    InfoboxItem,
    derive_sources,
    expand_years,
    is_quarter_month,
    is_supported_format,
    normalise_aliases,
    normalize_format,
    preferred_title,
    quarter_for_date,
)


@dataclass(frozen=True)
class SyncScope:
    """One or more full years, optionally narrowed to one quarter month."""

    years: tuple[int, ...]
    quarter_month: int | None

    @property
    def quarters(self) -> tuple[tuple[int, int], ...]:
        months = (self.quarter_month,) if self.quarter_month else (1, 4, 7, 10)
        return tuple((year, month) for year in self.years for month in months)

    @property
    def label(self) -> str:
        year_label = str(self.years[0]) if len(self.years) == 1 else f"{self.years[0]}-{self.years[-1]}"
        return f"{year_label}-{self.quarter_month:02d}" if self.quarter_month else year_label


def parse_sync_scope(values: list[str]) -> SyncScope:
    """Parse ``YEAR [QUARTER]`` or one inclusive ``START-END`` range."""
    if len(values) not in {1, 2}:
        raise ValueError("sync accepts YEAR [QUARTER_MONTH] or START-END")
    if len(values) == 2:
        year = _parse_year(values[0])
        month = _parse_month(values[1])
        return SyncScope((year,), month)
    value = values[0]
    if "-" in value:
        parts = value.split("-", 1)
        years = expand_years(_parse_year(parts[0]), _parse_year(parts[1]))
    else:
        years = (_parse_year(value),)
    return SyncScope(years, None)


@dataclass
class QuarterStats:
    """Per-quarter safe report counters."""

    year: int
    month: int
    discovered: int = 0
    duplicates: int = 0
    blacklisted: int = 0
    unsupported: int = 0
    details_requested: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    missing_date: int = 0
    ownership_mismatch: int = 0
    failed: int = 0
    retries: int = 0
    warnings: list[str] = field(default_factory=list)
    failures: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class SyncRun:
    """Completed sync counts, report paths, and the process exit code."""

    quarter_stats: tuple[QuarterStats, ...]
    sync_report: Path
    tag_audit_report: Path
    exit_code: int


class SubjectSynchronizer:
    """Connect discovery, detail DTOs, rules, and subject-only persistence."""

    def __init__(
        self,
        repository: SubjectRepository,
        api: BangumiApiClient,
        settings: ProjectSettings,
        tag_rules: TagRules,
        source_rules: SourceRules,
        *,
        discovery: QuarterlyDiscovery | None = None,
        reports_directory: Path = Path("workspace/reports"),
    ) -> None:
        self.repository = repository
        self.api = api
        self.settings = settings
        self.tag_rules = tag_rules
        self.source_rules = source_rules
        self.discovery = discovery or QuarterlyDiscovery(api)
        self.reports_directory = reports_directory

    def run(self, scope: SyncScope, *, force: bool = False) -> SyncRun:
        """Synchronise subject facts for a validated scope and write safe reports."""
        self.repository.database.migrate()
        all_stats: list[QuarterStats] = []
        for year, month in scope.quarters:
            stats = self._sync_quarter(year, month, force)
            all_stats.append(stats)
        sync_report = self._write_sync_report(scope, force, all_stats)
        audit_report = self._write_tag_audit_report()
        exit_code = 1 if any(stats.failed for stats in all_stats) else 0
        return SyncRun(tuple(all_stats), sync_report, audit_report, exit_code)

    def _cleanup_blacklisted_subjects(self, year: int, month: int) -> None:
        if not self.settings.excluded_subject_ids:
            return
        with self.repository.transaction() as connection:
            self.repository.delete_blacklisted_subjects_in_quarter(
                connection,
                self.settings.excluded_subject_ids,
                year,
                month,
            )

    def _sync_quarter(self, year: int, month: int, force: bool) -> QuarterStats:
        stats = QuarterStats(year, month)
        self._cleanup_blacklisted_subjects(year, month)
        result = self.discovery.discover(year, month, self.settings.excluded_subject_ids)
        self._apply_discovery(result, stats)
        for candidate in result.candidates:
            self._sync_candidate(candidate, year, month, force, stats)
        return stats

    def _apply_discovery(self, result: DiscoveryResult, stats: QuarterStats) -> None:
        discovered = result.statistics
        stats.discovered = discovered.discovered
        stats.duplicates = discovered.duplicates
        stats.blacklisted = discovered.blacklisted
        stats.unsupported = discovered.unsupported
        stats.details_requested = discovered.needs_detail
        stats.failed += discovered.failed
        for failure in result.failures:
            stats.failures.append(
                {
                    "stage": "discovery",
                    "month": failure.month,
                    "category": failure.category,
                    "code": failure.code,
                    "summary": failure.summary,
                }
            )

    def _sync_candidate(
        self,
        candidate: CandidateSubject,
        target_year: int,
        target_month: int,
        force: bool,
        stats: QuarterStats,
    ) -> None:
        if not force and self._refresh_stable_subject(candidate):
            stats.skipped += 1
            return
        try:
            detail = self.api.get_subject(candidate.subject_id)
        except BangumiApiError as error:
            self._record_failure(candidate.subject_id, error, stats)
            return
        self._store_detail(detail, target_year, target_month, stats)

    def _refresh_stable_subject(self, candidate: CandidateSubject) -> bool:
        state = self.repository.get_sync_state(
            "subject", candidate.subject_id, "subject_detail"
        )
        if state is None or state.status != "success" or not self.repository.subject_exists(candidate.subject_id):
            return False
        with self.repository.transaction() as connection:
            self.repository.refresh_rating(
                connection,
                candidate.subject_id,
                candidate.rating_score,
                candidate.rating_total,
            )
        return True

    def _store_detail(
        self,
        detail: SubjectDetail,
        target_year: int,
        target_month: int,
        stats: QuarterStats,
    ) -> None:
        media_format = normalize_format(detail.platform)
        if media_format not in {"tv", "movie"} or not is_supported_format(detail.platform):
            stats.unsupported += 1
            return
        if detail.air_date is None:
            stats.missing_date += 1
            return
        if quarter_for_date(detail.air_date).year != target_year or quarter_for_date(detail.air_date).month != target_month:
            stats.ownership_mismatch += 1
            return
        title = preferred_title(detail.name_cn, detail.name)
        if title is None:
            self._record_failure(
                detail.subject_id,
                BangumiApiError("missing_title", "subject has no usable title"),
                stats,
            )
            return
        was_present = self.repository.subject_exists(detail.subject_id)
        source_result = derive_sources(_source_infobox(detail.infobox), (tag.name for tag in detail.tags), self.source_rules)
        titles = _titles(detail, title)
        sources = _sources_for_result(source_result.sources, source_result.evidence, self.source_rules)
        appearance_kind = "new" if media_format == "tv" else "movie"
        try:
            with self.repository.transaction() as connection:
                self.repository.upsert_subject(
                    connection,
                    SubjectRecord(
                        detail.subject_id,
                        media_format,
                        _normalise_summary(detail.summary),
                        detail.air_date,
                        detail.eps,
                        detail.rating_score,
                        detail.rating_total,
                    ),
                )
                self.repository.replace_titles(connection, detail.subject_id, titles)
                self.repository.replace_infobox(
                    connection,
                    detail.subject_id,
                    [SubjectInfoboxItem(item.key, item.value) for item in detail.infobox],
                )
                self.repository.replace_raw_tags(
                    connection,
                    detail.subject_id,
                    [RawTag(tag.name, tag.count) for tag in detail.tags],
                )
                self.repository.replace_sources(connection, detail.subject_id, sources)
                self.repository.replace_permanent_quarter(
                    connection,
                    detail.subject_id,
                    SubjectQuarter(
                        target_year,
                        target_month,
                        appearance_kind,
                        "air_date",
                        detail.air_date.isoformat(),
                    ),
                )
                self.repository.write_sync_state(
                    connection,
                    SyncState(
                        "subject",
                        detail.subject_id,
                        "subject_detail",
                        "success",
                        _utc_timestamp(),
                        _utc_timestamp(),
                    ),
                )
        except (ValueError, TypeError) as error:
            self._record_failure(
                detail.subject_id,
                BangumiApiError("store_error", "subject data could not be stored"),
                stats,
            )
            stats.warnings.append(type(error).__name__)
            return
        if was_present:
            stats.updated += 1
        else:
            stats.created += 1
        stats.warnings.extend(source_result.warnings)

    def _record_failure(
        self, subject_id: int, error: BangumiApiError, stats: QuarterStats
    ) -> None:
        stats.failed += 1
        stats.failures.append(
            {"stage": "detail", "subject_id": subject_id, "code": error.code, "summary": error.summary}
        )
        previous = self.repository.get_sync_state(
            "subject", subject_id, "subject_detail"
        )
        with self.repository.transaction() as connection:
            self.repository.write_sync_state(
                connection,
                SyncState(
                    "subject",
                    subject_id,
                    "subject_detail",
                    "failed",
                    _utc_timestamp(),
                    failure_count=(previous.failure_count if previous else 0) + 1,
                    error_code=error.code,
                    error_summary=error.summary,
                ),
            )

    def _write_sync_report(
        self, scope: SyncScope, force: bool, stats: list[QuarterStats]
    ) -> Path:
        payload = {
            "command": "sync",
            "version": _version(),
            "generated_at": _utc_timestamp(),
            "scope": {"years": list(scope.years), "quarter_month": scope.quarter_month, "force": force},
            "quarters": [asdict(item) for item in stats],
        }
        return _write_json(self.reports_directory, f"sync-{scope.label}", payload)

    def _write_tag_audit_report(self) -> Path:
        rows = self._tag_audit_rows()
        payload = {"generated_at": _utc_timestamp(), "tags": rows}
        return _write_json(self.reports_directory, "tag-audit", payload)

    def _tag_audit_rows(self) -> list[dict[str, object]]:
        connection = self.repository.database.connect()
        try:
            tags = connection.execute(
                """
                SELECT tag_name, COUNT(DISTINCT subject_id) AS subject_count,
                       SUM(COALESCE(tag_count, 0)) AS count_total
                FROM subject_raw_tags
                GROUP BY tag_name
                ORDER BY tag_name
                """
            ).fetchall()
            rows: list[dict[str, object]] = []
            allowed = set(self.tag_rules.allowed_tags)
            for tag in tags:
                raw_tag = tag["tag_name"]
                mapped = self.tag_rules.aliases.get(raw_tag, raw_tag)
                samples = connection.execute(
                    """
                    SELECT subject_id FROM subject_raw_tags
                    WHERE tag_name = ? ORDER BY subject_id LIMIT 5
                    """,
                    (raw_tag,),
                ).fetchall()
                rows.append(
                    {
                        "raw_tag": raw_tag,
                        "subject_count": tag["subject_count"],
                        "count_total": tag["count_total"],
                        "examples": [row["subject_id"] for row in samples],
                        "mapped_to": mapped if mapped != raw_tag or mapped in allowed else None,
                        "displayed": mapped in allowed,
                        "whitelist_status": "allowed" if mapped in allowed else "not_allowed",
                    }
                )
            return rows
        finally:
            connection.close()


def _source_infobox(items: Iterable[ApiInfoboxItem]) -> tuple[InfoboxItem, ...]:
    """Expand only explicit structured strings without guessing their meaning."""
    values: list[InfoboxItem] = []
    for item in items:
        for value in _infobox_string_values(item.value):
            values.append(InfoboxItem(item.key, value))
    return tuple(values)


def _infobox_string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list):
        return ()
    return tuple(
        item["v"]
        for item in value
        if isinstance(item, dict) and isinstance(item.get("v"), str)
    )


def _titles(detail: SubjectDetail, title: str) -> list[SubjectTitle]:
    titles = [SubjectTitle("preferred", title)]
    original = detail.name.strip() if detail.name else ""
    if original and original != title:
        titles.append(SubjectTitle("original", original))
    aliases = normalise_aliases(_infobox_aliases(detail.infobox), title)
    titles.extend(SubjectTitle("alias", alias) for alias in aliases if alias != original)
    return titles


def _infobox_aliases(items: Iterable[ApiInfoboxItem]) -> list[str]:
    aliases: list[str] = []
    for item in items:
        if item.key != "别名":
            continue
        if isinstance(item.value, str):
            aliases.append(item.value)
        elif isinstance(item.value, list):
            aliases.extend(
                value["v"] for value in item.value if isinstance(value, dict) and isinstance(value.get("v"), str)
            )
    return aliases


def _sources_for_result(sources: tuple[str, ...], evidence: tuple[object, ...], rules: SourceRules) -> list[SubjectSource]:
    mappings = {"infobox": rules.infobox_values, "tag": rules.tag_values}
    rows: list[SubjectSource] = []
    for source in sources:
        matched = [item for item in evidence if mappings[item.evidence_type].get(item.value) == source]
        if source == "unknown":
            matched = list(evidence)
        if matched:
            rows.extend(SubjectSource(source, item.evidence_type, item.value) for item in matched)
        else:
            rows.append(SubjectSource(source))
    return rows


def _normalise_summary(value: str | None) -> str | None:
    if value is None:
        return None
    paragraphs = re.split(r"\n[ \t]*\n+", value.replace("\r\n", "\n").replace("\r", "\n"))
    normalised = [
        " ".join(" ".join(line.split()) for line in paragraph.split("\n")).strip()
        for paragraph in paragraphs
    ]
    return "\n\n".join(paragraph for paragraph in normalised if paragraph) or None


def _parse_year(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ValueError("year must be an integer") from error


def _parse_month(value: str) -> int:
    try:
        month = int(value)
    except ValueError as error:
        raise ValueError("quarter month must be an integer") from error
    if not is_quarter_month(month):
        raise ValueError("quarter month must be one of 1, 4, 7, or 10")
    return month


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(directory: Path, prefix: str, payload: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{prefix}-{timestamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _version() -> str:
    from bgm_side_b import __version__

    return __version__
