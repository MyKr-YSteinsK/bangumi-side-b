# ruff: noqa: E501
"""Subject-only synchronisation orchestration and safe JSON reports."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from bgm_side_b.api import (
    ApiEpisode,
    ApiInfoboxItem,
    ApiPersonSummary,
    BangumiApiClient,
    BangumiApiError,
    CandidateSubject,
    CharacterDetail,
    DiscoveryResult,
    PersonDetail,
    QuarterlyDiscovery,
    RelatedCharacter,
    SubjectDetail,
)
from bgm_side_b.config import ProjectSettings, SourceRules, TagRules
from bgm_side_b.media import MediaCache, MediaTarget
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.repository import (
    CharacterRecord,
    CharacterVoiceRecord,
    EpisodeRecord,
    PersonRecord,
    RawTag,
    SubjectCharacterRecord,
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectSource,
    SubjectTitle,
    SyncState,
)
from bgm_side_b.rules import (
    CountryDecision,
    InfoboxItem,
    decide_country,
    derive_sources,
    expand_years,
    is_quarter_month,
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


def validate_release_scope(scope: SyncScope, settings: ProjectSettings) -> None:
    """Reject every sync request outside the checked-in release scope."""
    configured = tuple(
        (int(item[:4]), int(item[5:])) for item in settings.scope.release_quarters
    )
    if scope.quarters != configured:
        raise ValueError(
            "当前发布范围只允许 2026-04；修改 config/bangumi.toml 后才能同步其他季度。"
        )


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
    subject_details_requested: int = 0
    created: int = 0
    updated: int = 0
    ratings_updated: int = 0
    skipped: int = 0
    missing_date: int = 0
    ownership_mismatch: int = 0
    country_included_japan: int = 0
    country_excluded_not_japan: int = 0
    country_excluded_missing: int = 0
    country_excluded_unparseable: int = 0
    country_excluded_conflict: int = 0
    failed: int = 0
    retries: int = 0
    episodes_requested: int = 0
    episodes_stored: int = 0
    episode_conflicts: int = 0
    continuations_created: int = 0
    continuing_details_requested: int = 0
    continuing_ratings_updated: int = 0
    continuation_invalid_before_air: int = 0
    continuation_after_end_date: int = 0
    continuation_end_before_air: int = 0
    continuation_quarters_created: int = 0
    roles_requested: int = 0
    main_characters_stored: int = 0
    persons_stored: int = 0
    voice_relations_stored: int = 0
    covers_downloaded: int = 0
    character_images_downloaded: int = 0
    media_downloaded: int = 0
    media_skipped: int = 0
    media_failed: int = 0
    cleanup_deleted: int = 0
    cleanup_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    failures: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class SyncRun:
    """Completed sync counts, report paths, and the process exit code."""

    quarter_stats: tuple[QuarterStats, ...]
    sync_report: Path
    tag_audit_report: Path
    country_audit_report: Path
    exit_code: int


class _SyncInterrupted(KeyboardInterrupt):
    """Carry the safe partial quarter summary to the top-level report writer."""

    def __init__(self, stats: QuarterStats) -> None:
        self.stats = stats


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
        media_cache: MediaCache | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.repository = repository
        self.api = api
        self.settings = settings
        self.tag_rules = tag_rules
        self.source_rules = source_rules
        self.reporter = reporter or NullProgressReporter()
        if isinstance(api, BangumiApiClient):
            api.reporter = self.reporter
        self.discovery = discovery or QuarterlyDiscovery(api, self.reporter)
        self.reports_directory = reports_directory
        self.media_cache = media_cache or MediaCache(
            repository, api, reports_directory.parent, self.reporter
        )
        self._country_audit_rows: list[dict[str, object]] = []

    def run(
        self, scope: SyncScope, *, force: bool = False, force_images: bool = False
    ) -> SyncRun:
        """Synchronise subject facts for a validated scope and write safe reports."""
        validate_release_scope(scope, self.settings)
        started_at = _utc_timestamp()
        self._country_audit_rows = []
        quarters = scope.quarters
        self.reporter.start(
            stage="scope",
            message="开始同步",
            current=scope.label,
            counters={
                "季度": len(quarters),
                "并发": getattr(self.api, "concurrency", 1),
                "超时": getattr(self.api, "timeout_seconds", "默认"),
                "重试": getattr(self.api, "max_retries", 0),
                "强制": "是" if force else "否",
                "强制图片": "是" if force_images else "否",
                "类别": "TV",
                "国家过滤": self.settings.country_filter.required_country,
                "角色": "否",
                "续播": "否",
            },
        )
        self.reporter.stage(stage="database", message="正在检查 SQLite schema")
        self.repository.database.migrate()
        all_stats: list[QuarterStats] = []
        interrupted = False
        try:
            for position, (year, month) in enumerate(quarters, start=1):
                stats = self._sync_quarter(
                    year,
                    month,
                    force,
                    force_images,
                    position=position,
                    total_quarters=len(quarters),
                )
                all_stats.append(stats)
        except _SyncInterrupted as error:
            error.stats.warnings.append("interrupted")
            all_stats.append(error.stats)
            interrupted = True
            self.reporter.warning(
                stage="interrupted",
                message="已收到 Ctrl+C，停止安排新任务；当前事务将完成或回滚。",
            )
        sync_report = self._write_sync_report(
            scope, force, force_images, started_at, all_stats
        )
        audit_report = self._write_tag_audit_report()
        country_audit_report = self._write_country_audit_report()
        exit_code = 130 if interrupted else int(any(stats.failed for stats in all_stats))
        if exit_code == 0:
            from bgm_side_b.release.candidate import advance_data_generation

            advance_data_generation(self.reports_directory.parent)
        self.reporter.complete(
            stage="summary",
            message="已中断" if interrupted else "同步完成",
            counters={
                "季度": len(all_stats),
                "失败": sum(item.failed for item in all_stats),
                "重试": sum(item.retries for item in all_stats),
                "图片下载": sum(item.media_downloaded for item in all_stats),
                "缓存命中": sum(item.media_skipped for item in all_stats),
                "日本TV收录": sum(
                    item.country_included_japan for item in all_stats
                ),
                "非日本排除": sum(
                    item.country_excluded_not_japan for item in all_stats
                ),
                "国家缺失": sum(
                    item.country_excluded_missing for item in all_stats
                ),
                "国家冲突": sum(
                    item.country_excluded_conflict for item in all_stats
                ),
            },
        )
        return SyncRun(
            tuple(all_stats), sync_report, audit_report, country_audit_report, exit_code
        )

    def _cleanup_blacklisted_subjects(
        self, year: int, month: int, stats: QuarterStats
    ) -> None:
        if not self.settings.excluded_subject_ids:
            return
        with self.repository.transaction() as connection:
            stats.blacklisted += self.repository.delete_blacklisted_subjects_in_quarter(
                connection,
                self.settings.excluded_subject_ids,
                year,
                month,
            )
        cleanup = self.media_cache.cleanup_orphaned()
        stats.cleanup_deleted += cleanup.deleted
        if cleanup.failures:
            stats.failed += len(cleanup.failures)
            stats.media_failed += len(cleanup.failures)
            stats.cleanup_failed += len(cleanup.failures)
            stats.failures.extend(
                {
                    "stage": media_kind,
                    "code": code,
                    "summary": "orphaned media cleanup failed",
                }
                for media_kind, code in cleanup.failures
            )

    def _sync_quarter(
        self,
        year: int,
        month: int,
        force: bool,
        force_images: bool,
        *,
        position: int,
        total_quarters: int,
    ) -> QuarterStats:
        stats = QuarterStats(year, month)
        started_at = time.monotonic()
        quarter = f"{year}-{month:02d}"
        try:
            self.reporter.stage(
                stage="blacklist-cleanup",
                message="正在清理黑名单",
                completed=position,
                total=total_quarters,
                quarter=quarter,
            )
            self._cleanup_blacklisted_subjects(year, month, stats)
            self.reporter.stage(
                stage="discovery",
                message="正在发现季度候选",
                completed=position,
                total=total_quarters,
                quarter=quarter,
            )
            retries_before = self._json_retries()
            result = self.discovery.discover(
                year, month, self.settings.excluded_subject_ids
            )
            stats.retries += self._json_retries() - retries_before
            self._apply_discovery(result, stats)
            self.reporter.stage(
                stage="candidate-summary",
                message="候选发现完成",
                completed=position,
                total=total_quarters,
                quarter=quarter,
                counters={
                    "候选": len(result.candidates),
                    "重复": stats.duplicates,
                    "失败": stats.failed,
                },
            )
            candidate_total = len(result.candidates)
            for candidate_position, candidate in enumerate(result.candidates, start=1):
                if candidate.subject_id in self.settings.excluded_subject_ids:
                    stats.blacklisted += 1
                    continue
                self._sync_candidate(
                    candidate,
                    year,
                    month,
                    force,
                    force_images,
                    stats,
                    completed=candidate_position,
                    total=candidate_total,
                )
        except KeyboardInterrupt as error:
            if isinstance(error, _SyncInterrupted):
                raise
            raise _SyncInterrupted(stats) from error
        self.reporter.stage(
            stage="quarter-summary",
            message="季度完成",
            completed=position,
            total=total_quarters,
            quarter=quarter,
            counters={
                "创建": stats.created,
                "更新": stats.updated,
                "失败": stats.failed,
                "重试": stats.retries,
                "章节": stats.episodes_stored,
                "日本TV收录": stats.country_included_japan,
                "非日本排除": stats.country_excluded_not_japan,
                "国家缺失": stats.country_excluded_missing,
                "国家冲突": stats.country_excluded_conflict,
                "封面": stats.covers_downloaded,
                "耗时秒": round(time.monotonic() - started_at, 1),
            },
        )
        return stats

    def _apply_discovery(self, result: DiscoveryResult, stats: QuarterStats) -> None:
        discovered = result.statistics
        stats.discovered = discovered.discovered
        stats.duplicates = discovered.duplicates
        stats.blacklisted += discovered.blacklisted
        stats.unsupported = discovered.unsupported
        stats.failed += discovered.failed
        for failure in result.failures:
            self.reporter.warning(
                stage="discovery",
                message=failure.summary,
                quarter=f"{stats.year}-{failure.month:02d}",
                current="TV",
            )
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
        force_images: bool,
        stats: QuarterStats,
        *,
        completed: int,
        total: int,
    ) -> None:
        stats.details_requested += 1
        stats.subject_details_requested += 1
        self.reporter.progress(
            stage="subject-detail",
            message=candidate.name_cn or candidate.name or "正在读取作品详情",
            completed=completed,
            total=total,
            entity_type="subject",
            entity_id=candidate.subject_id,
        )
        retries_before = self._json_retries()
        try:
            detail = self.api.get_subject(candidate.subject_id)
        except BangumiApiError as error:
            self._record_failure(
                candidate.subject_id,
                error,
                stats,
                retry_count=self._json_retries() - retries_before,
            )
            return
        finally:
            stats.retries += self._json_retries() - retries_before
        if self._store_detail(detail, target_year, target_month, stats):
            if self._should_refresh_episodes(
                detail.subject_id, target_year, target_month, force
            ):
                self._sync_subject_episodes(detail.subject_id, stats)
            self._sync_media_for_subject(
                detail.subject_id,
                detail.images.largest_available,
                force_images,
                stats,
            )

    def _store_detail(
        self,
        detail: SubjectDetail,
        target_year: int,
        target_month: int,
        stats: QuarterStats,
    ) -> bool:
        media_format = normalize_format(detail.platform)
        if media_format != "tv":
            stats.unsupported += 1
            return False
        if detail.air_date is None:
            stats.missing_date += 1
            return False
        if (
            quarter_for_date(detail.air_date).year != target_year
            or quarter_for_date(detail.air_date).month != target_month
        ):
            stats.ownership_mismatch += 1
            return False
        title = preferred_title(detail.name_cn, detail.name)
        if title is None:
            self._record_failure(
                detail.subject_id,
                BangumiApiError("missing_title", "subject has no usable title"),
                stats,
            )
            return False
        country = decide_country(
            _source_infobox(detail.infobox), self.settings.country_filter
        )
        self._record_country_audit(detail, title, country)
        if country.decision != "included_japan":
            if country.decision == "excluded_not_japan":
                stats.country_excluded_not_japan += 1
            elif country.decision == "excluded_missing_country":
                stats.country_excluded_missing += 1
            elif country.decision == "excluded_unparseable_country":
                stats.country_excluded_unparseable += 1
            else:
                stats.country_excluded_conflict += 1
            return False
        stats.country_included_japan += 1
        was_present = self.repository.subject_exists(detail.subject_id)
        source_result = derive_sources(_source_infobox(detail.infobox), (tag.name for tag in detail.tags), self.source_rules)
        titles = _titles(detail, title)
        sources = _sources_for_result(source_result.sources, source_result.evidence, self.source_rules)
        appearance_kind = "new"
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
                        total_episode_count=detail.total_episodes,
                        end_date=_end_date_from_infobox(
                            detail.infobox, self.settings.end_date_infobox_keys
                        ),
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
                self.repository.write_sync_state(
                    connection,
                    SyncState(
                        "subject",
                        detail.subject_id,
                        "rating",
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
            return False
        if was_present:
            stats.updated += 1
        else:
            stats.created += 1
        if detail.rating_score is not None or detail.rating_total is not None:
            stats.ratings_updated += 1
        stats.warnings.extend(source_result.warnings)
        return True

    def _sync_existing_tv(
        self,
        subject_id: int,
        target_year: int,
        target_month: int,
        force: bool,
        stats: QuarterStats,
    ) -> None:
        if self._appears_in_quarter(subject_id, target_year, target_month):
            self._refresh_continuing_detail(subject_id, target_year, target_month, stats)
        if self._should_refresh_episodes(subject_id, target_year, target_month, force):
            self._sync_subject_episodes(subject_id, stats)

    def _appears_in_quarter(self, subject_id: int, year: int, month: int) -> bool:
        subject = self.repository.get_stored_subject(subject_id)
        if subject is None or subject.air_date is None:
            return False
        permanent = quarter_for_date(subject.air_date)
        if (year, month) <= (permanent.year, permanent.month):
            return False
        if subject.end_date is not None and subject.end_date >= subject.air_date:
            end = quarter_for_date(subject.end_date)
            if (year, month) <= (end.year, end.month):
                return True
        return any(
            (quarter_for_date(value).year, quarter_for_date(value).month) == (year, month)
            for value in self.repository.main_episode_air_dates(subject_id)
            if value >= subject.air_date
        )

    def _refresh_continuing_detail(
        self, subject_id: int, year: int, month: int, stats: QuarterStats
    ) -> None:
        stats.continuing_details_requested += 1
        self.reporter.progress(
            stage="continuation",
            message="正在刷新续播作品详情",
            entity_type="subject",
            entity_id=subject_id,
            quarter=f"{year}-{month:02d}",
        )
        try:
            detail = self.api.get_subject(subject_id)
        except BangumiApiError as error:
            self._record_failure(subject_id, error, stats)
            return
        if self._store_detail(detail, year, month, stats):
            if detail.rating_score is not None or detail.rating_total is not None:
                stats.continuing_ratings_updated += 1

    def _should_refresh_episodes(
        self, subject_id: int, target_year: int, target_month: int, force: bool
    ) -> bool:
        subject = self.repository.get_stored_subject(subject_id)
        if subject is None:
            return False
        state = self.repository.get_sync_state("subject", subject_id, "episodes")
        if force or state is None or state.status != "success":
            return True
        if subject.media_format != "tv":
            return False
        current_count = self.repository.main_episode_count(subject_id)
        declared_counts = {
            count
            for count in (subject.episode_count, subject.total_episode_count)
            if count is not None
        }
        if any(current_count < count for count in declared_counts):
            return True
        if subject.end_date is not None:
            return False
        if len(declared_counts) != 1 or current_count < next(iter(declared_counts)):
            return True
        air_dates = self.repository.main_episode_air_dates(subject_id)
        if not air_dates:
            return True
        last_quarter = quarter_for_date(max(air_dates))
        return (last_quarter.year, last_quarter.month) >= (target_year, target_month)

    def _sync_subject_episodes(self, subject_id: int, stats: QuarterStats) -> None:
        previous = self.repository.get_sync_state("subject", subject_id, "episodes")
        metrics = getattr(self.api, "metrics", None)
        before_failures = getattr(metrics, "json_item_failures", 0)
        retries_before = self._json_retries()
        stats.episodes_requested += 1
        self.reporter.progress(
            stage="episodes",
            message="正在读取章节",
            entity_type="subject",
            entity_id=subject_id,
        )
        try:
            api_episodes = self.api.get_episodes(subject_id)
        except BangumiApiError as error:
            self._record_episode_failure(
                subject_id,
                error,
                previous,
                stats,
                retry_count=self._json_retries() - retries_before,
            )
            return
        finally:
            stats.retries += self._json_retries() - retries_before
        parse_failures = getattr(metrics, "json_item_failures", 0)
        if parse_failures > before_failures:
            stats.warnings.append(
                f"episode_parse_failures:{parse_failures - before_failures}"
            )
        episodes = tuple(_episode_record(episode) for episode in api_episodes)
        try:
            with self.repository.transaction() as connection:
                self.repository.replace_main_episodes(connection, subject_id, episodes)
                self.repository.write_sync_state(
                    connection,
                    SyncState(
                        "subject",
                        subject_id,
                        "episodes",
                        "success",
                        _utc_timestamp(),
                        _utc_timestamp(),
                    ),
                )
        except (ValueError, TypeError) as error:
            self._record_episode_failure(
                subject_id,
                BangumiApiError("episode_store_error", "episodes could not be stored"),
                previous,
                stats,
            )
            stats.warnings.append(type(error).__name__)
            return
        self._warn_episode_count_conflict(subject_id, stats)
        stats.episodes_stored += len(episodes)

    def _record_episode_failure(
        self,
        subject_id: int,
        error: BangumiApiError,
        previous: SyncState | None,
        stats: QuarterStats,
        *,
        retry_count: int = 0,
    ) -> None:
        stats.failed += 1
        self.reporter.error(
            stage="episodes",
            message=error.summary,
            entity_type="subject",
            entity_id=subject_id,
        )
        stats.failures.append(
            {
                "stage": "episodes",
                "subject_id": subject_id,
                "entity_type": "subject",
                "entity_id": subject_id,
                "data_type": "episodes",
                "code": error.code,
                "summary": error.summary,
                "retry_count": retry_count,
            }
        )
        with self.repository.transaction() as connection:
            self.repository.write_sync_state(
                connection,
                SyncState(
                    "subject",
                    subject_id,
                    "episodes",
                    "failed",
                    _utc_timestamp(),
                    failure_count=(previous.failure_count if previous else 0) + 1,
                    error_code=error.code,
                    error_summary=error.summary,
                ),
            )

    def _warn_episode_count_conflict(
        self, subject_id: int, stats: QuarterStats
    ) -> None:
        subject = self.repository.get_stored_subject(subject_id)
        if subject is None:
            return
        values = {
            value
            for value in (
                subject.episode_count,
                subject.total_episode_count,
                self.repository.main_episode_count(subject_id),
            )
            if value is not None
        }
        if len(values) > 1:
            stats.warnings.append(f"episode_count_conflict:{subject_id}")
            stats.episode_conflicts += 1

    def _rebuild_continuing_quarters(
        self, subject_id: int, stats: QuarterStats | None = None
    ) -> None:
        subject = self.repository.get_stored_subject(subject_id)
        if subject is None:
            return
        if subject.media_format != "tv" or subject.air_date is None:
            with self.repository.transaction() as connection:
                self.repository.replace_continuing_quarters(connection, subject_id, ())
            return
        permanent = quarter_for_date(subject.air_date)
        evidence: dict[tuple[int, int], tuple[str, str]] = {}
        if subject.end_date is not None:
            if subject.end_date < subject.air_date:
                if stats is not None:
                    stats.continuation_end_before_air += 1
                    stats.warnings.append(f"continuation_end_before_air:{subject_id}")
            else:
                end_quarter = quarter_for_date(subject.end_date)
                for year, month in _quarters_after_until(
                    permanent.year, permanent.month, end_quarter.year, end_quarter.month
                ):
                    evidence[(year, month)] = (
                        "air_end_overlap",
                        f"{subject.air_date.isoformat()}/{subject.end_date.isoformat()}",
                    )
        for air_date in self.repository.main_episode_air_dates(subject_id):
            if air_date < subject.air_date:
                if stats is not None:
                    stats.continuation_invalid_before_air += 1
                    stats.warnings.append(f"continuation_invalid_before_air:{subject_id}")
                continue
            if subject.end_date is not None and air_date > subject.end_date:
                if stats is not None:
                    stats.continuation_after_end_date += 1
                    stats.warnings.append(f"continuation_after_end_date:{subject_id}")
                continue
            episode_quarter = quarter_for_date(air_date)
            evidence[(episode_quarter.year, episode_quarter.month)] = (
                "episode_air_date",
                air_date.isoformat(),
            )
        evidence.pop((permanent.year, permanent.month), None)
        quarters = tuple(
            SubjectQuarter(
                year,
                month,
                "continuing",
                evidence_type,
                evidence_value,
                position,
            )
            for position, ((year, month), (evidence_type, evidence_value)) in enumerate(
                sorted(evidence.items())
            )
        )
        with self.repository.transaction() as connection:
            self.repository.replace_continuing_quarters(connection, subject_id, quarters)
        if stats is not None:
            stats.continuation_quarters_created += len(quarters)

    def _sync_subject_roles(
        self, subject_id: int, force: bool, stats: QuarterStats
    ) -> dict[int, str]:
        state = self.repository.get_sync_state("subject", subject_id, "roles")
        if (
            not force
            and state is not None
            and state.status == "success"
            and not self.repository.role_details_need_refresh(subject_id)
        ):
            return {}
        retries_before = self._json_retries()
        stats.roles_requested += 1
        self.reporter.progress(
            stage="roles",
            message="正在读取角色关系",
            entity_type="subject",
            entity_id=subject_id,
        )
        try:
            roles = self.api.get_related_characters(subject_id)
        except BangumiApiError as error:
            self._record_roles_failure(
                subject_id,
                error,
                state,
                stats,
                retry_count=self._json_retries() - retries_before,
            )
            return {}
        finally:
            stats.retries += self._json_retries() - retries_before

        characters: dict[int, CharacterRecord] = {}
        persons: dict[int, PersonRecord] = {}
        relations: list[SubjectCharacterRecord] = []
        voices: list[CharacterVoiceRecord] = []
        successful_details: list[SyncState] = []
        character_image_urls: dict[int, str] = {}
        for position, role in enumerate(roles):
            if role.relation not in self.settings.main_character_relations:
                continue
            character, detail_state, image_url = self._resolve_character(
                role, force, stats
            )
            if character is None:
                stats.warnings.append(f"main_character_missing_name:{role.character_id}")
                continue
            if role.character_id in characters:
                continue
            characters[role.character_id] = character
            if image_url is not None:
                character_image_urls[role.character_id] = image_url
            relations.append(
                SubjectCharacterRecord(role.character_id, role.relation, position)
            )
            if detail_state is not None:
                successful_details.append(detail_state)
            seen_people: set[int] = set()
            for actor_position, actor in enumerate(role.actors):
                if actor.person_id in seen_people:
                    continue
                person, person_state = self._resolve_person(actor, force, stats)
                if person is None:
                    stats.warnings.append(f"actor_missing_name:{actor.person_id}")
                    continue
                seen_people.add(actor.person_id)
                persons[actor.person_id] = person
                voices.append(
                    CharacterVoiceRecord(
                        role.character_id, actor.person_id, None, actor_position
                    )
                )
                if person_state is not None:
                    successful_details.append(person_state)

        try:
            with self.repository.transaction() as connection:
                for character in characters.values():
                    self.repository.upsert_character(connection, character)
                for person in persons.values():
                    self.repository.upsert_person(connection, person)
                self.repository.replace_roles_snapshot(
                    connection, subject_id, relations, voices
                )
                for detail_state in successful_details:
                    self.repository.write_sync_state(connection, detail_state)
                self.repository.write_sync_state(
                    connection,
                    SyncState(
                        "subject",
                        subject_id,
                        "roles",
                        "success",
                        _utc_timestamp(),
                        _utc_timestamp(),
                    ),
                )
        except (ValueError, TypeError) as error:
            self._record_roles_failure(
                subject_id,
                BangumiApiError("roles_store_error", "roles could not be stored"),
                state,
                stats,
            )
            stats.warnings.append(type(error).__name__)
            return {}
        stats.main_characters_stored += len(relations)
        stats.persons_stored += len(persons)
        stats.voice_relations_stored += len(voices)
        return character_image_urls

    def _resolve_character(
        self, role: RelatedCharacter, force: bool, stats: QuarterStats
    ) -> tuple[CharacterRecord | None, SyncState | None, str | None]:
        detail_state = self.repository.get_sync_state(
            "character", role.character_id, "character_detail"
        )
        detail: CharacterDetail | None = None
        if force or detail_state is None or detail_state.status != "success":
            retries_before = self._json_retries()
            self.reporter.progress(
                stage="character-detail",
                message="正在读取角色详情",
                entity_type="character",
                entity_id=role.character_id,
            )
            try:
                detail = self.api.get_character(role.character_id)
            except BangumiApiError as error:
                self._record_detail_failure(
                    "character",
                    role.character_id,
                    "character_detail",
                    error,
                    detail_state,
                    stats,
                    retry_count=self._json_retries() - retries_before,
                )
            finally:
                stats.retries += self._json_retries() - retries_before
        original_name = detail.original_name if detail else role.original_name
        summary = detail.summary if detail and detail.summary is not None else role.summary
        chinese_name = _chinese_name(
            detail.infobox if detail else (), self.settings.chinese_name_infobox_keys
        )
        if not original_name and not chinese_name:
            return None, None, None
        state = None
        if detail is not None:
            state = SyncState(
                "character",
                role.character_id,
                "character_detail",
                "success",
                _utc_timestamp(),
                _utc_timestamp(),
            )
        return (
            CharacterRecord(role.character_id, original_name, chinese_name, summary),
            state,
            detail.images.largest_available if detail else role.images.largest_available,
        )

    def _sync_media_for_subject(
        self,
        subject_id: int,
        cover_url: str | None,
        force_images: bool,
        stats: QuarterStats,
    ) -> None:
        targets: list[MediaTarget] = []
        if (
            cover_url is not None
            or self.repository.get_media_record("subject", subject_id, "cover")
            is not None
        ):
            targets.append(MediaTarget("subject", subject_id, "cover", cover_url))
        for target in targets:
            result = self.media_cache.sync_target(target, force_images=force_images)
            stats.retries += result.retries
            self.reporter.progress(
                stage="cover",
                message=f"媒体 {result.status}",
                entity_type=target.owner_type,
                entity_id=target.owner_id,
            )
            if result.status == "downloaded":
                stats.media_downloaded += 1
                stats.covers_downloaded += 1
            elif result.status == "skipped":
                stats.media_skipped += 1
            else:
                self.reporter.error(
                    stage=target.media_kind,
                    message=result.error_summary or "媒体处理失败",
                    entity_type=target.owner_type,
                    entity_id=target.owner_id,
                )
                stats.media_failed += 1
                stats.failed += 1
                stats.failures.append(
                    {
                        "stage": target.media_kind,
                        "entity_type": target.owner_type,
                        "entity_id": target.owner_id,
                        "subject_id": (
                            target.owner_id if target.owner_type == "subject" else None
                        ),
                        "data_type": "cover_image",
                        "code": result.error_code,
                        "summary": result.error_summary,
                        "retry_count": result.retries,
                    }
                )
            if result.error_code == "media_cleanup_failed":
                self.reporter.warning(
                    stage=target.media_kind,
                    message="媒体替换后清理失败",
                    entity_type=target.owner_type,
                    entity_id=target.owner_id,
                )
                stats.failed += 1
                stats.media_failed += 1
                stats.failures.append(
                    {
                        "stage": target.media_kind,
                        "entity_type": target.owner_type,
                        "entity_id": target.owner_id,
                        "subject_id": (
                            target.owner_id if target.owner_type == "subject" else None
                        ),
                        "data_type": "cover_image",
                        "code": result.error_code,
                        "summary": result.error_summary,
                        "retry_count": result.retries,
                    }
                )
        cleanup = self.media_cache.cleanup_orphaned()
        stats.cleanup_deleted += cleanup.deleted
        if cleanup.failures:
            stats.failed += len(cleanup.failures)
            stats.media_failed += len(cleanup.failures)
            stats.cleanup_failed += len(cleanup.failures)
            stats.failures.extend(
                {
                    "stage": media_kind,
                    "code": code,
                    "summary": "orphaned media cleanup failed",
                }
                for media_kind, code in cleanup.failures
            )

    def _resolve_person(
        self, actor: ApiPersonSummary, force: bool, stats: QuarterStats
    ) -> tuple[PersonRecord | None, SyncState | None]:
        detail_state = self.repository.get_sync_state(
            "person", actor.person_id, "person_detail"
        )
        detail: PersonDetail | None = None
        if force or detail_state is None or detail_state.status != "success":
            retries_before = self._json_retries()
            self.reporter.progress(
                stage="person-detail",
                message="正在读取声优详情",
                entity_type="person",
                entity_id=actor.person_id,
            )
            try:
                detail = self.api.get_person(actor.person_id)
            except BangumiApiError as error:
                self._record_detail_failure(
                    "person",
                    actor.person_id,
                    "person_detail",
                    error,
                    detail_state,
                    stats,
                    retry_count=self._json_retries() - retries_before,
                )
            finally:
                stats.retries += self._json_retries() - retries_before
        original_name = detail.original_name if detail else actor.original_name
        chinese_name = _chinese_name(
            detail.infobox if detail else (), self.settings.chinese_name_infobox_keys
        )
        if not original_name and not chinese_name:
            return None, None
        state = None
        if detail is not None:
            state = SyncState(
                "person",
                actor.person_id,
                "person_detail",
                "success",
                _utc_timestamp(),
                _utc_timestamp(),
            )
        return PersonRecord(actor.person_id, original_name, chinese_name), state

    def _record_roles_failure(
        self,
        subject_id: int,
        error: BangumiApiError,
        previous: SyncState | None,
        stats: QuarterStats,
        *,
        retry_count: int = 0,
    ) -> None:
        self._record_data_failure(
            "subject",
            subject_id,
            "roles",
            error,
            previous,
            "roles",
            stats,
            retry_count=retry_count,
        )

    def _record_detail_failure(
        self,
        entity_type: str,
        entity_id: int,
        data_type: str,
        error: BangumiApiError,
        previous: SyncState | None,
        stats: QuarterStats,
        *,
        retry_count: int = 0,
    ) -> None:
        self._record_data_failure(
            entity_type,
            entity_id,
            data_type,
            error,
            previous,
            data_type,
            stats,
            retry_count=retry_count,
        )

    def _record_data_failure(
        self,
        entity_type: str,
        entity_id: int,
        data_type: str,
        error: BangumiApiError,
        previous: SyncState | None,
        stage: str,
        stats: QuarterStats,
        *,
        retry_count: int = 0,
    ) -> None:
        stats.failed += 1
        self.reporter.error(
            stage=stage,
            message=error.summary,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        stats.failures.append(
            {
                "stage": stage,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "data_type": data_type,
                "subject_id": entity_id if entity_type == "subject" else None,
                "code": error.code,
                "summary": error.summary,
                "retry_count": retry_count,
            }
        )
        with self.repository.transaction() as connection:
            self.repository.write_sync_state(
                connection,
                SyncState(
                    entity_type,
                    entity_id,
                    data_type,
                    "failed",
                    _utc_timestamp(),
                    failure_count=(previous.failure_count if previous else 0) + 1,
                    error_code=error.code,
                    error_summary=error.summary,
                ),
            )

    def _record_failure(
        self,
        subject_id: int,
        error: BangumiApiError,
        stats: QuarterStats,
        *,
        retry_count: int = 0,
    ) -> None:
        stats.failed += 1
        self.reporter.error(
            stage="subject-detail",
            message=error.summary,
            entity_type="subject",
            entity_id=subject_id,
        )
        stats.failures.append(
            {
                "stage": "detail",
                "subject_id": subject_id,
                "entity_type": "subject",
                "entity_id": subject_id,
                "data_type": "subject_detail",
                "code": error.code,
                "summary": error.summary,
                "retry_count": retry_count,
            }
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

    def _json_retries(self) -> int:
        """Read the client's actual JSON retry counter when it is available."""
        metrics = getattr(self.api, "metrics", None)
        value = getattr(metrics, "json_retries", 0)
        return value if isinstance(value, int) else 0

    def _write_sync_report(
        self,
        scope: SyncScope,
        force: bool,
        force_images: bool,
        started_at: str,
        stats: list[QuarterStats],
    ) -> Path:
        quarter_payloads = [_report_quarter(item) for item in stats]
        payload = {
            "command": "sync",
            "app_version": _version(),
            "started_at": started_at,
            "finished_at": _utc_timestamp(),
            "scope": {
                "release_quarters": list(self.settings.scope.release_quarters),
                "formats": list(self.settings.scope.formats),
                "country_filter": "structured_contains_japan",
                "continuations": False,
                "roles": False,
                "years": list(scope.years),
                "quarter_month": scope.quarter_month,
                "force": force,
                "force_images": force_images,
            },
            "quarters": quarter_payloads,
            "totals": _report_totals(quarter_payloads),
        }
        return _write_json(self.reports_directory, f"sync-{scope.label}", payload)

    def _write_tag_audit_report(self) -> Path:
        rows = self._tag_audit_rows()
        payload = {"generated_at": _utc_timestamp(), "tags": rows}
        return _write_json(self.reports_directory, "tag-audit", payload)

    def _write_country_audit_report(self) -> Path:
        payload = {
            "generated_at": _utc_timestamp(),
            "scope": "2026-04",
            "filter": "structured_contains_japan",
            "subjects": self._country_audit_rows,
        }
        return _write_json(self.reports_directory, "country-audit", payload)

    def _record_country_audit(
        self, detail: SubjectDetail, title: str, country: CountryDecision
    ) -> None:
        self._country_audit_rows.append(
            {
                "subject_id": detail.subject_id,
                "title": title,
                "decision": country.decision,
                "evidence_source": country.evidence_source,
                "structured_tokens": [
                    {"key": item.key, "tokens": list(item.tokens)}
                    for item in country.evidence
                ],
                "matched_positive_tags": list(country.matched_positive_tags),
                "matched_negative_tags": list(country.matched_negative_tags),
                "default_reason": country.default_reason,
                "reason": country.reason,
            }
        )

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
                    SELECT tags.subject_id, titles.title
                    FROM subject_raw_tags AS tags
                    LEFT JOIN subject_titles AS titles
                        ON titles.subject_id = tags.subject_id
                        AND titles.title_kind = 'preferred'
                    WHERE tags.tag_name = ?
                    ORDER BY tags.subject_id LIMIT 5
                    """,
                    (raw_tag,),
                ).fetchall()
                rows.append(
                    {
                        "raw_tag": raw_tag,
                        "subject_count": tag["subject_count"],
                        "count_total": tag["count_total"],
                        "examples": [
                            {
                                "subject_id": row["subject_id"],
                                "title": row["title"],
                            }
                            for row in samples
                        ],
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


def _report_quarter(stats: QuarterStats) -> dict[str, object]:
    """Expose stable public report names while retaining useful legacy counters."""
    payload = {
        key: value
        for key, value in vars(stats).items()
        if key not in {"failures"}
    }
    payload.update(
        {
            "subjects_created": stats.created,
            "subjects_updated": stats.updated,
            "failures": [_report_failure(stats, failure) for failure in stats.failures],
        }
    )
    return payload


def _report_failure(
    stats: QuarterStats, failure: dict[str, object]
) -> dict[str, object]:
    stage = failure.get("stage") if isinstance(failure.get("stage"), str) else "unknown"
    entity_id = failure.get("entity_id", failure.get("subject_id"))
    subject_id = failure.get("subject_id")
    data_type = failure.get("data_type")
    if not isinstance(data_type, str):
        data_type = {
            "detail": "subject_detail",
            "episodes": "episodes",
            "roles": "roles",
            "cover": "cover_image",
            "character_image": "character_image",
        }.get(stage, stage)
    entity_type = failure.get("entity_type")
    if not isinstance(entity_type, str):
        entity_type = "subject" if subject_id is not None else "unknown"
    return {
        "quarter": {"year": stats.year, "month": stats.month},
        "subject_id": subject_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "data_type": data_type,
        "error_code": failure.get("code"),
        "summary": failure.get("summary"),
        "retry_count": failure.get("retry_count", 0),
    }


def _report_totals(quarters: list[dict[str, object]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for quarter in quarters:
        for key, value in quarter.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals


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


def _end_date_from_infobox(
    items: Iterable[ApiInfoboxItem], keys: frozenset[str]
) -> date | None:
    """Read only a configured, explicit Infobox end-date key in ISO form."""
    for item in items:
        if item.key not in keys or not isinstance(item.value, str):
            continue
        try:
            return date.fromisoformat(item.value)
        except ValueError:
            continue
    return None


def _quarters_after_until(
    start_year: int, start_month: int, end_year: int, end_month: int
) -> tuple[tuple[int, int], ...]:
    values: list[tuple[int, int]] = []
    year, month = start_year, start_month
    while (year, month) < (end_year, end_month):
        month += 3
        if month > 10:
            year += 1
            month = 1
        if (year, month) <= (end_year, end_month):
            values.append((year, month))
    return tuple(values)


def _chinese_name(
    items: Iterable[ApiInfoboxItem], keys: frozenset[str]
) -> str | None:
    """Return only the first configured structured Chinese name, never a translation."""
    for item in items:
        if item.key in keys and isinstance(item.value, str) and item.value.strip():
            return item.value.strip()
    return None


def _episode_record(episode: ApiEpisode) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=episode.episode_id,
        episode_number=episode.episode_number,
        sort_number=episode.sort_number,
        name=episode.name,
        name_cn=episode.name_cn,
        air_date=episode.air_date,
        duration_seconds=episode.duration_seconds,
        raw_duration=episode.raw_duration,
        position=episode.position,
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
