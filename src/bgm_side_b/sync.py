# ruff: noqa: E501
"""Subject-only synchronisation orchestration and safe JSON reports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
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
    media_downloaded: int = 0
    media_skipped: int = 0
    media_failed: int = 0
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
        media_cache: MediaCache | None = None,
    ) -> None:
        self.repository = repository
        self.api = api
        self.settings = settings
        self.tag_rules = tag_rules
        self.source_rules = source_rules
        self.discovery = discovery or QuarterlyDiscovery(api)
        self.reports_directory = reports_directory
        self.media_cache = media_cache or MediaCache(
            repository, api, reports_directory.parent
        )

    def run(
        self, scope: SyncScope, *, force: bool = False, force_images: bool = False
    ) -> SyncRun:
        """Synchronise subject facts for a validated scope and write safe reports."""
        self.repository.database.migrate()
        all_stats: list[QuarterStats] = []
        for year, month in scope.quarters:
            stats = self._sync_quarter(year, month, force, force_images)
            all_stats.append(stats)
        sync_report = self._write_sync_report(scope, force, force_images, all_stats)
        audit_report = self._write_tag_audit_report()
        exit_code = 1 if any(stats.failed for stats in all_stats) else 0
        return SyncRun(tuple(all_stats), sync_report, audit_report, exit_code)

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
        if cleanup.failures:
            stats.failed += len(cleanup.failures)
            stats.media_failed += len(cleanup.failures)
            stats.failures.extend(
                {
                    "stage": media_kind,
                    "code": code,
                    "summary": "orphaned media cleanup failed",
                }
                for media_kind, code in cleanup.failures
            )

    def _sync_quarter(
        self, year: int, month: int, force: bool, force_images: bool
    ) -> QuarterStats:
        stats = QuarterStats(year, month)
        self._cleanup_blacklisted_subjects(year, month, stats)
        result = self.discovery.discover(year, month, self.settings.excluded_subject_ids)
        self._apply_discovery(result, stats)
        discovered_ids: set[int] = set()
        for candidate in result.candidates:
            discovered_ids.add(candidate.subject_id)
            self._sync_candidate(candidate, year, month, force, force_images, stats)
        for subject_id in self.repository.list_tv_subject_ids():
            if subject_id not in discovered_ids:
                self._sync_existing_tv_episodes(subject_id, year, month, force, stats)
        return stats

    def _apply_discovery(self, result: DiscoveryResult, stats: QuarterStats) -> None:
        discovered = result.statistics
        stats.discovered = discovered.discovered
        stats.duplicates = discovered.duplicates
        stats.blacklisted += discovered.blacklisted
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
        force_images: bool,
        stats: QuarterStats,
    ) -> None:
        if not force and self._refresh_stable_subject(candidate):
            stats.skipped += 1
            self._sync_existing_tv_episodes(
                candidate.subject_id, target_year, target_month, force, stats
            )
            character_image_urls = self._sync_subject_roles(
                candidate.subject_id, force, stats
            )
            self._sync_media_for_subject(
                candidate.subject_id, None, character_image_urls, force_images, stats
            )
            return
        try:
            detail = self.api.get_subject(candidate.subject_id)
        except BangumiApiError as error:
            self._record_failure(candidate.subject_id, error, stats)
            return
        if self._store_detail(detail, target_year, target_month, stats):
            self._sync_subject_episodes(detail.subject_id, stats)
            character_image_urls = self._sync_subject_roles(
                detail.subject_id, force, stats
            )
            self._sync_media_for_subject(
                detail.subject_id,
                detail.images.largest_available,
                character_image_urls,
                force_images,
                stats,
            )

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
    ) -> bool:
        media_format = normalize_format(detail.platform)
        if media_format not in {"tv", "movie"} or not is_supported_format(detail.platform):
            stats.unsupported += 1
            return False
        if detail.air_date is None:
            stats.missing_date += 1
            return False
        if quarter_for_date(detail.air_date).year != target_year or quarter_for_date(detail.air_date).month != target_month:
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
        stats.warnings.extend(source_result.warnings)
        return True

    def _sync_existing_tv_episodes(
        self,
        subject_id: int,
        target_year: int,
        target_month: int,
        force: bool,
        stats: QuarterStats,
    ) -> None:
        if self._should_refresh_episodes(subject_id, target_year, target_month, force):
            self._sync_subject_episodes(subject_id, stats)

    def _should_refresh_episodes(
        self, subject_id: int, target_year: int, target_month: int, force: bool
    ) -> bool:
        subject = self.repository.get_stored_subject(subject_id)
        if subject is None:
            return False
        if subject.media_format != "tv":
            return force
        state = self.repository.get_sync_state("subject", subject_id, "episodes")
        if force or state is None or state.status != "success":
            return True
        current_count = self.repository.main_episode_count(subject_id)
        declared_counts = {
            count
            for count in (subject.episode_count, subject.total_episode_count)
            if count is not None
        }
        if any(current_count < count for count in declared_counts):
            return True
        if subject.end_date is not None:
            end_quarter = quarter_for_date(subject.end_date)
            return (end_quarter.year, end_quarter.month) >= (target_year, target_month)
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
        try:
            api_episodes = self.api.get_episodes(subject_id)
        except BangumiApiError as error:
            self._record_episode_failure(subject_id, error, previous, stats)
            return
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
        self._rebuild_continuing_quarters(subject_id)

    def _record_episode_failure(
        self,
        subject_id: int,
        error: BangumiApiError,
        previous: SyncState | None,
        stats: QuarterStats,
    ) -> None:
        stats.failed += 1
        stats.failures.append(
            {
                "stage": "episodes",
                "subject_id": subject_id,
                "code": error.code,
                "summary": error.summary,
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

    def _rebuild_continuing_quarters(self, subject_id: int) -> None:
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
            end_quarter = quarter_for_date(subject.end_date)
            evidence[(end_quarter.year, end_quarter.month)] = (
                "end_date",
                subject.end_date.isoformat(),
            )
        for air_date in self.repository.main_episode_air_dates(subject_id):
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
        try:
            roles = self.api.get_related_characters(subject_id)
        except BangumiApiError as error:
            self._record_roles_failure(subject_id, error, state, stats)
            return {}

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
        return character_image_urls

    def _resolve_character(
        self, role: RelatedCharacter, force: bool, stats: QuarterStats
    ) -> tuple[CharacterRecord | None, SyncState | None, str | None]:
        detail_state = self.repository.get_sync_state(
            "character", role.character_id, "character_detail"
        )
        detail: CharacterDetail | None = None
        if force or detail_state is None or detail_state.status != "success":
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
                )
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
        character_image_urls: dict[int, str],
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
        character_ids = set(self.repository.list_subject_character_ids(subject_id))
        character_ids.update(character_image_urls)
        for character_id in sorted(character_ids):
            source_url = character_image_urls.get(character_id)
            if (
                source_url is not None
                or self.repository.get_media_record(
                    "character", character_id, "character_image"
                )
                is not None
            ):
                targets.append(
                    MediaTarget(
                        "character", character_id, "character_image", source_url
                    )
                )
        for target in targets:
            result = self.media_cache.sync_target(target, force_images=force_images)
            stats.retries += result.retries
            if result.status == "downloaded":
                stats.media_downloaded += 1
            elif result.status == "skipped":
                stats.media_skipped += 1
            else:
                stats.media_failed += 1
                stats.failed += 1
                stats.failures.append(
                    {
                        "stage": target.media_kind,
                        "entity_id": target.owner_id,
                        "code": result.error_code,
                        "summary": result.error_summary,
                    }
                )
            if result.error_code == "media_cleanup_failed":
                stats.failed += 1
                stats.media_failed += 1
                stats.failures.append(
                    {
                        "stage": target.media_kind,
                        "entity_id": target.owner_id,
                        "code": result.error_code,
                        "summary": result.error_summary,
                    }
                )
        cleanup = self.media_cache.cleanup_orphaned()
        if cleanup.failures:
            stats.failed += len(cleanup.failures)
            stats.media_failed += len(cleanup.failures)
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
                )
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
    ) -> None:
        self._record_data_failure(
            "subject", subject_id, "roles", error, previous, "roles", stats
        )

    def _record_detail_failure(
        self,
        entity_type: str,
        entity_id: int,
        data_type: str,
        error: BangumiApiError,
        previous: SyncState | None,
        stats: QuarterStats,
    ) -> None:
        self._record_data_failure(
            entity_type, entity_id, data_type, error, previous, data_type, stats
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
    ) -> None:
        stats.failed += 1
        stats.failures.append(
            {
                "stage": stage,
                "entity_id": entity_id,
                "code": error.code,
                "summary": error.summary,
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
        self,
        scope: SyncScope,
        force: bool,
        force_images: bool,
        stats: list[QuarterStats],
    ) -> Path:
        payload = {
            "command": "sync",
            "version": _version(),
            "generated_at": _utc_timestamp(),
            "scope": {
                "years": list(scope.years),
                "quarter_month": scope.quarter_month,
                "force": force,
                "force_images": force_images,
            },
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
