"""Atomic TV/MOVIE archive synchronization into the current fact store."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from bgm_side_b.admission import (
    DISCOVERY_MEDIA_CONFLICT,
    AdmissionDecision,
    AdmissionStatus,
    QuarterOverride,
    ReviewFinding,
    admit_subject,
    is_unresolved_cold_review,
    quarter_end_date,
    quarter_for_date,
    should_auto_blacklist_unresolved_cold,
)
from bgm_side_b.api import BangumiApiClient, BangumiApiError, SubjectDetail
from bgm_side_b.archive_config import (
    ArchiveSourceRules,
    ArchiveSyncSettings,
    add_auto_excluded_subject,
    load_archive_sync_settings,
    restore_archive_config,
    should_auto_blacklist,
)
from bgm_side_b.discovery import (
    BrowseDiscoveryAdapter,
    DiscoveryFailure,
    SearchDiscoveryAdapter,
    merge_discovery_batches,
)
from bgm_side_b.domain import (
    JapaneseClassification,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
    SourceEvidence,
)
from bgm_side_b.media import MAX_COVER_CONCURRENCY, CoverResult, CoverStore
from bgm_side_b.overrides import load_quarter_overrides
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.repository import (
    QuarterAppearance,
    QuarterSyncState,
    ReviewIssue,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)
from bgm_side_b.rules import normalize_aliases, order_tag_candidates, resolve_source

FACTS_COMPLETE = "complete"
FACTS_INCOMPLETE = "incomplete"
COVERS_COMPLETE = "complete"
COVERS_INCOMPLETE = "incomplete"
CONTINUING_EVIDENCE_UNRESOLVED = "CONTINUING_EVIDENCE_UNRESOLVED"
CONTINUING_EVIDENCE_CONFLICT = "CONTINUING_EVIDENCE_CONFLICT"


class SyncError(RuntimeError):
    """The requested archive synchronization cannot safely continue."""


@dataclass(frozen=True)
class SyncScope:
    """One forced-refresh quarter or one newest-to-oldest range lifecycle."""

    start: Quarter
    end: Quarter
    refresh_existing: bool = False

    @property
    def is_single_quarter(self) -> bool:
        return self.start == self.end

    @property
    def quarters(self) -> tuple[Quarter, ...]:
        quarters: list[Quarter] = []
        current = self.end
        while (current.year, current.month) >= (self.start.year, self.start.month):
            quarters.append(current)
            if current == self.start:
                break
            current = _previous_quarter(current)
        return tuple(quarters)

    @property
    def label(self) -> str:
        if self.is_single_quarter:
            return _quarter_label(self.start)
        return f"{_quarter_label(self.start)}..{_quarter_label(self.end)}"


def parse_sync_scope(
    positional: list[str],
    *,
    range_start: list[str] | None = None,
    range_end: list[str] | None = None,
    refresh_existing: bool = False,
) -> SyncScope:
    """Parse ``sync YEAR QUARTER`` or the explicit bounded range form."""
    if range_start is not None or range_end is not None:
        if positional or range_start is None or range_end is None:
            raise ValueError("range sync requires both --from and --to without scope")
        start = _quarter_from_values(range_start, "--from")
        end = _quarter_from_values(range_end, "--to")
        if (start.year, start.month) > (end.year, end.month):
            raise ValueError("--from must not be later than --to")
        return SyncScope(start, end, refresh_existing)
    quarter = _quarter_from_values(positional, "sync")
    return SyncScope(quarter, quarter, refresh_existing)


@dataclass(frozen=True)
class QuarterSyncResult:
    """Safe aggregate status for one independently atomic quarter attempt."""

    quarter: Quarter
    facts_status: str
    covers_status: str
    discovered: int
    accepted_tv: int
    accepted_movie: int
    rejected_non_japanese: int
    blacklisted: int
    reviews: tuple[ReviewIssue, ...]
    external_reviews: tuple[dict[str, object], ...]
    warnings: tuple[dict[str, str], ...]
    errors: tuple[dict[str, str], ...]
    cover_downloaded: int = 0
    cover_reused: int = 0
    cover_missing: int = 0
    continuing_end_date: int = 0
    continuing_episode: int = 0
    continuing_unresolved: int = 0
    natural_premiere_tv: int = 0
    early_premieres: tuple[dict[str, object], ...] = ()
    boundary_reviews: int = 0
    skipped: bool = False
    auto_blacklisted: tuple[dict[str, object], ...] = ()
    manual_blacklisted: int = 0
    existing_auto_blacklisted: int = 0
    canonical_detail_requests: int = 0
    persisted_review_count: int = 0
    source_counts: tuple[tuple[str, int], ...] = ()
    episode_known: int = 0
    episode_unknown: int = 0
    legacy_zero_written: int = 0


@dataclass(frozen=True)
class SyncRun:
    """The final report path and all independently attempted quarter results."""

    scope: SyncScope
    quarters: tuple[QuarterSyncResult, ...]
    report_path: Path
    exit_code: int


@dataclass(frozen=True)
class SingleSubjectImport:
    """Result of the low-frequency manual discovery recovery path."""

    snapshot: SubjectSnapshot
    report_path: Path | None
    cover: CoverResult
    report_warning: str | None = None


@dataclass(frozen=True)
class _PreparedSubject:
    snapshot: SubjectSnapshot
    cover_url: str | None
    cover_variant: str | None


@dataclass(frozen=True)
class _ContinuingReconciliation:
    """Complete externally gathered evidence for one target-quarter replacement."""

    quarter: Quarter
    examined: tuple[SubjectSnapshot, ...]
    appearances: tuple[tuple[int, QuarterAppearance], ...]
    reviews: tuple[tuple[SubjectSnapshot, ReviewIssue], ...]
    warnings: tuple[dict[str, str], ...]
    errors: tuple[dict[str, str], ...]
    confirmed_by_end_date: int
    confirmed_by_episode: int

    @property
    def unresolved(self) -> int:
        return len(self.reviews)


class ArchiveSynchronizer:
    """Keep facts atomic per quarter while covers recover independently afterwards."""

    def __init__(
        self,
        repository: SubjectRepository,
        api: BangumiApiClient,
        settings: ArchiveSyncSettings,
        source_rules: ArchiveSourceRules,
        *,
        overrides_path: Path,
        workspace_directory: Path,
        reports_directory: Path,
        reporter: ProgressReporter | None = None,
        browse: BrowseDiscoveryAdapter | None = None,
        search: SearchDiscoveryAdapter | None = None,
        settings_path: Path | None = None,
        evaluation_date: date | None = None,
    ) -> None:
        self.repository = repository
        self.api = api
        self.settings = settings
        self.source_rules = source_rules
        self.overrides_path = overrides_path
        self.workspace_directory = workspace_directory
        self.reports_directory = reports_directory
        self.reporter = reporter or NullProgressReporter()
        self.browse = browse or BrowseDiscoveryAdapter(api)
        self.search = search or SearchDiscoveryAdapter(api)
        self.covers = CoverStore(workspace_directory / "covers", api)
        self.settings_path = settings_path or workspace_directory / "bangumi.toml"
        self.evaluation_date = evaluation_date or date.today()
        self._manual_excluded_subject_ids = set(settings.excluded_subject_ids)
        self._auto_excluded_subject_ids = set(settings.auto_excluded_subject_ids)
        self._active_excluded_subject_ids = set(settings.all_excluded_subject_ids)

    def run(self, scope: SyncScope) -> SyncRun:
        """Synchronize the requested scope without writing any legacy fact tables."""
        if self.settings_path.is_file():
            settings = load_archive_sync_settings(self.settings_path)
            self._manual_excluded_subject_ids = set(settings.excluded_subject_ids)
            self._auto_excluded_subject_ids = set(settings.auto_excluded_subject_ids)
            self._active_excluded_subject_ids = set(settings.all_excluded_subject_ids)
        self.repository.database.initialize()
        self._purge_blacklist()
        overrides = load_quarter_overrides(self.overrides_path)
        started = _timestamp()
        results: list[QuarterSyncResult] = []
        self.reporter.start(
            stage="scope",
            message="开始同步 archive facts",
            current=scope.label,
            counters={"季度": len(scope.quarters), "封面并发": MAX_COVER_CONCURRENCY},
        )
        try:
            for quarter in scope.quarters:
                current = self.repository.get_sync_state(quarter)
                if (
                    not scope.is_single_quarter
                    and not scope.refresh_existing
                    and current is not None
                    and current.facts_status == FACTS_COMPLETE
                    and current.covers_status == COVERS_COMPLETE
                ):
                    source_counts, episode_known, episode_unknown = (
                        _persisted_fact_aggregates(self.repository, quarter)
                    )
                    results.append(
                        _skipped_result(
                            quarter,
                            current,
                            source_counts=source_counts,
                            episode_known=episode_known,
                            episode_unknown=episode_unknown,
                        )
                    )
                    continue
                result = self._sync_quarter(quarter, overrides, current)
                if result.facts_status == FACTS_COMPLETE:
                    result = replace(
                        result,
                        warnings=(
                            *result.warnings,
                            *self._backfill_next_quarter(quarter),
                        ),
                    )
                results.append(result)
        except KeyboardInterrupt:
            report_path = self._write_report(scope, started, results, interrupted=True)
            return SyncRun(scope, tuple(results), report_path, 130)
        report_path = self._write_report(scope, started, results, interrupted=False)
        exit_code = (
            0 if all(item.facts_status == FACTS_COMPLETE for item in results) else 1
        )
        self.reporter.complete(
            stage="summary",
            message="archive facts 同步完成",
            counters={
                "季度": len(results),
                "错误": sum(len(item.errors) for item in results),
            },
        )
        return SyncRun(scope, tuple(results), report_path, exit_code)

    def import_single_subject(
        self, subject_id: int, override: QuarterOverride
    ) -> SingleSubjectImport:
        """Fetch one missing manual-assignment subject through the official endpoint."""
        if subject_id in self._active_excluded_subject_ids:
            raise SyncError("blacklisted subjects cannot be imported")
        self.repository.database.initialize()
        detail = self.api.get_subject(subject_id)
        target = override.quarter or (
            quarter_for_date(detail.air_date)
            if detail.air_date is not None
            else Quarter(1970, 1)
        )
        from bgm_side_b.discovery import DiscoveredSubject

        candidate = DiscoveredSubject(
            subject_id, frozenset(), frozenset(), frozenset(), ("manual:assign",)
        )
        decision = admit_subject(
            candidate,
            detail,
            target,
            excluded_subject_ids=frozenset(self._active_excluded_subject_ids),
            override=override,
        )
        if decision.status is not AdmissionStatus.ACCEPTED:
            raise SyncError(
                "manual import requires confirmed Anime, TV/MOVIE, and Japanese facts"
            )
        prepared = self._prepare_subject(detail, decision, None, target)
        with self.repository.transaction() as connection:
            self.repository.replace_subject_snapshot(connection, prepared.snapshot)
        cover = self._sync_cover(prepared)
        snapshot = (
            replace(prepared.snapshot, cover=cover.cover)
            if cover.cover
            else prepared.snapshot
        )
        if cover.cover is not None:
            with self.repository.transaction() as connection:
                self.repository.replace_subject_snapshot(connection, snapshot)
        try:
            report_path = self._write_single_import_report(subject_id, snapshot, cover)
        except OSError as error:
            report_path = None
            report_warning = f"manual import report unavailable: {error}"
        else:
            report_warning = None
        return SingleSubjectImport(snapshot, report_path, cover, report_warning)

    def _sync_quarter(
        self,
        quarter: Quarter,
        overrides: Mapping[int, QuarterOverride],
        prior: QuarterSyncState | None,
    ) -> QuarterSyncResult:
        self.reporter.stage(
            stage="discovery",
            message="发现 TV/MOVIE 候选",
            current=_quarter_label(quarter),
        )
        try:
            browse_batch = self.browse.discover(quarter)
            search_batch = self.search.discover(quarter)
        except KeyboardInterrupt:
            self._write_incomplete(quarter, prior, 0, ())
            raise
        batch = merge_discovery_batches(browse_batch, search_batch)
        fatal_failures = tuple(
            item for item in batch.failures if item.source.value != "search"
        )
        discovery_warnings = tuple(
            _discovery_warning(item)
            for item in batch.failures
            if item.source.value == "search"
        )
        if fatal_failures:
            errors = tuple(_discovery_error(item) for item in fatal_failures)
            return self._write_incomplete(quarter, prior, len(batch.candidates), errors)
        existing_by_subject = self.repository.get_subject_facts_many(
            candidate.subject_id for candidate in batch.candidates
        )

        prepared: list[_PreparedSubject] = []
        reviews: list[ReviewIssue] = []
        external_reviews: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        blacklisted = 0
        manual_blacklisted = 0
        existing_auto_blacklisted = 0
        auto_blacklisted: list[dict[str, object]] = []
        rejected_non_japanese = 0
        accepted_tv = 0
        accepted_movie = 0
        tv_candidates = 0
        admitted_japanese_tv = 0
        premiere_conflict_warnings: list[dict[str, str]] = []
        canonical_detail_requests = 0
        try:
            for candidate in batch.candidates:
                if candidate.subject_id in self._manual_excluded_subject_ids:
                    blacklisted += 1
                    manual_blacklisted += 1
                    continue
                if candidate.subject_id in self._auto_excluded_subject_ids:
                    blacklisted += 1
                    existing_auto_blacklisted += 1
                    continue
                candidate_is_tv = MediaFormat.TV in candidate.media_formats
                try:
                    canonical_detail_requests += 1
                    detail = self.api.get_subject(candidate.subject_id)
                    decision = admit_subject(
                        candidate,
                        detail,
                        quarter,
                        excluded_subject_ids=frozenset(
                            self._active_excluded_subject_ids
                        ),
                        override=overrides.get(candidate.subject_id),
                    )
                except BangumiApiError as error:
                    errors.append({"code": error.code, "summary": error.summary})
                    continue
                except (TypeError, ValueError):
                    errors.append(
                        {
                            "code": "normalization",
                            "summary": "subject facts are invalid",
                        }
                    )
                    continue
                if candidate_is_tv or decision.media_format is MediaFormat.TV:
                    tv_candidates += 1
                if (
                    decision.status is AdmissionStatus.ACCEPTED
                    and decision.media_format is MediaFormat.TV
                ):
                    admitted_japanese_tv += 1
                if decision.status is AdmissionStatus.BLACKLISTED:
                    blacklisted += 1
                    if candidate.subject_id in self._manual_excluded_subject_ids:
                        manual_blacklisted += 1
                    elif candidate.subject_id in self._auto_excluded_subject_ids:
                        existing_auto_blacklisted += 1
                    continue
                if _eligible_for_auto_blacklist(decision) and should_auto_blacklist(
                    detail.air_date, detail.rating_total, self.evaluation_date
                ):
                    try:
                        added = self._record_auto_blacklist(
                            detail,
                            existing_by_subject.get(candidate.subject_id),
                        )
                    except Exception as error:
                        errors.append(
                            {
                                "code": "auto_blacklist_persist",
                                "summary": _auto_blacklist_error_summary(error),
                            }
                        )
                        continue
                    blacklisted += 1
                    if added:
                        auto_blacklisted.append(
                            _auto_blacklist_event(detail, self.evaluation_date)
                        )
                    continue
                unresolved_cold = _persisted_unresolved_cold_review(decision)
                if unresolved_cold is not None:
                    issue_code, target_quarter = unresolved_cold
                    if should_auto_blacklist_unresolved_cold(
                        issue_code,
                        target_quarter,
                        detail.rating_total,
                        self.evaluation_date,
                    ):
                        try:
                            added = self._record_auto_blacklist(
                                detail,
                                existing_by_subject.get(candidate.subject_id),
                            )
                        except Exception as error:
                            errors.append(
                                {
                                    "code": "auto_blacklist_persist",
                                    "summary": _auto_blacklist_error_summary(error),
                                }
                            )
                            continue
                        blacklisted += 1
                        if added:
                            auto_blacklisted.append(
                                _auto_blacklist_event(
                                    detail,
                                    self.evaluation_date,
                                    reason="unresolved_cold_candidate",
                                    issue_code=issue_code,
                                    target_quarter=target_quarter,
                                )
                            )
                        continue
                external_cold = _external_unresolved_cold_review(decision, quarter)
                if external_cold is not None:
                    issue_code, target_quarter = external_cold
                    if should_auto_blacklist_unresolved_cold(
                        issue_code,
                        target_quarter,
                        detail.rating_total,
                        self.evaluation_date,
                    ):
                        try:
                            added = self._record_auto_blacklist(
                                detail,
                                existing_by_subject.get(candidate.subject_id),
                            )
                        except Exception as error:
                            errors.append(
                                {
                                    "code": "auto_blacklist_persist",
                                    "summary": _auto_blacklist_error_summary(error),
                                }
                            )
                            continue
                        blacklisted += 1
                        if added:
                            auto_blacklisted.append(
                                _auto_blacklist_event(
                                    detail,
                                    self.evaluation_date,
                                    reason="unresolved_cold_candidate",
                                    issue_code=issue_code,
                                    target_quarter=target_quarter,
                                )
                            )
                        continue
                if decision.status is AdmissionStatus.REJECTED:
                    if decision.reason == "non_japanese":
                        rejected_non_japanese += 1
                    continue
                if decision.media_format is None or decision.japanese is None:
                    external_reviews.append(
                        _external_review(candidate.subject_id, decision, quarter)
                    )
                    continue
                try:
                    existing = existing_by_subject.get(candidate.subject_id)
                    if (
                        existing is not None
                        and existing.premiere is not None
                        and existing.premiere.quarter != quarter
                    ):
                        premiere_conflict_warnings.append(
                            {
                                "code": "premiere_retained",
                                "summary": (
                                    f"retained existing premiere for subject "
                                    f"{candidate.subject_id} instead of later "
                                    f"{_quarter_label(quarter)} discovery"
                                ),
                            }
                        )
                        continue
                    prepared_subject = self._prepare_subject(
                        detail, decision, existing, quarter
                    )
                except ValueError:
                    errors.append(
                        {
                            "code": "normalization",
                            "summary": "subject facts are invalid",
                        }
                    )
                    continue
                prepared.append(prepared_subject)
                reviews.extend(
                    issue
                    for issue in prepared_subject.snapshot.review_issues
                    if issue.candidate_quarter in {None, quarter}
                )
                if decision.status is AdmissionStatus.ACCEPTED:
                    if decision.media_format.value == "TV":
                        accepted_tv += 1
                    else:
                        accepted_movie += 1
        except KeyboardInterrupt:
            self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                (),
                blacklisted=blacklisted,
                manual_blacklisted=manual_blacklisted,
                existing_auto_blacklisted=existing_auto_blacklisted,
                auto_blacklisted=tuple(auto_blacklisted),
                canonical_detail_requests=canonical_detail_requests,
            )
            raise
        if errors:
            return self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                tuple(errors),
                blacklisted=blacklisted,
                manual_blacklisted=manual_blacklisted,
                existing_auto_blacklisted=existing_auto_blacklisted,
                auto_blacklisted=tuple(auto_blacklisted),
                canonical_detail_requests=canonical_detail_requests,
            )
        if tv_candidates > 0 and admitted_japanese_tv == 0:
            self.reporter.error(
                stage="candidate-summary",
                message="候选存在但日本 TV 收录为 0；保留旧事实并标记未完成。",
                quarter=_quarter_label(quarter),
                counters={"TV 候选": tv_candidates, "日本 TV": 0},
            )
            return self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                (
                    {
                        "code": "empty_included_result",
                        "summary": (
                            "TV candidates exist but no Japanese TV subject "
                            "was admitted"
                        ),
                    },
                ),
                blacklisted=blacklisted,
                manual_blacklisted=manual_blacklisted,
                existing_auto_blacklisted=existing_auto_blacklisted,
                auto_blacklisted=tuple(auto_blacklisted),
                canonical_detail_requests=canonical_detail_requests,
            )
        if tv_candidates >= 20 and admitted_japanese_tv / tv_candidates < 0.20:
            premiere_conflict_warnings.append(
                {
                    "code": "low_japan_tv_inclusion_rate",
                    "summary": (
                        "Japanese TV inclusion rate is below 20% "
                        f"({admitted_japanese_tv}/{tv_candidates})"
                    ),
                }
            )
            self.reporter.warning(
                stage="candidate-summary",
                message="日本 TV 收录率低于 20%。",
                quarter=_quarter_label(quarter),
                counters={
                    "TV 候选": tv_candidates,
                    "日本 TV": admitted_japanese_tv,
                },
            )

        premiere_subject_ids = {
            item.snapshot.subject.subject_id
            for item in prepared
            if item.snapshot.premiere is not None
            and item.snapshot.premiere.quarter == quarter
        }
        try:
            reconciliation = self._reconcile_continuing(
                quarter, excluded_subject_ids=frozenset(premiere_subject_ids)
            )
        except KeyboardInterrupt:
            self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                (),
                blacklisted=blacklisted,
                manual_blacklisted=manual_blacklisted,
                existing_auto_blacklisted=existing_auto_blacklisted,
                auto_blacklisted=tuple(auto_blacklisted),
                canonical_detail_requests=canonical_detail_requests,
            )
            raise
        if reconciliation.errors:
            return self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                reconciliation.errors,
                blacklisted=blacklisted,
                manual_blacklisted=manual_blacklisted,
                existing_auto_blacklisted=existing_auto_blacklisted,
                auto_blacklisted=tuple(auto_blacklisted),
                canonical_detail_requests=canonical_detail_requests,
            )

        stale_ids = {
            item.subject.subject_id
            for item in self.repository.list_subjects_appearing_in_quarter(
                quarter, appearance_kind=QuarterAppearanceKind.PREMIERE
            )
        } - {item.snapshot.subject.subject_id for item in prepared}
        completed_at = _timestamp()
        continuing_appearances = tuple(
            (subject_id, appearance)
            for subject_id, appearance in reconciliation.appearances
            if subject_id not in premiere_subject_ids
        )
        subject_count = len(
            premiere_subject_ids
            | {subject_id for subject_id, _ in continuing_appearances}
        )
        natural_premiere_tv = sum(
            item.snapshot.subject.media_format.value == "TV"
            and item.snapshot.premiere is not None
            and item.snapshot.premiere.quarter == quarter
            and item.snapshot.premiere.evidence_type == "air_date"
            for item in prepared
        )
        early_premieres = tuple(
            {
                "subject_id": item.snapshot.subject.subject_id,
                "air_date": item.snapshot.subject.air_date.isoformat(),
                "premiere_quarter": _quarter_label(quarter),
                "evidence": item.snapshot.premiere.evidence_value,
            }
            for item in prepared
            if item.snapshot.subject.media_format.value == "TV"
            and item.snapshot.premiere is not None
            and item.snapshot.premiere.quarter == quarter
            and item.snapshot.premiere.evidence_type == "community_quarter_tag"
            and item.snapshot.subject.air_date is not None
        )
        boundary_reviews = sum(
            issue.issue_code == "TV_QUARTER_BOUNDARY" for issue in reviews
        )
        with self.repository.transaction() as connection:
            for item in prepared:
                self.repository.replace_subject_snapshot(connection, item.snapshot)
            self.repository.delete_subjects(connection, frozenset(stale_ids))
            self.repository.replace_automatic_continuing_for_quarter(
                connection, quarter, continuing_appearances
            )
            self._replace_continuing_reviews(
                connection,
                reconciliation,
                {
                    item.snapshot.subject.subject_id: item.snapshot
                    for item in prepared
                },
            )
            self.repository.write_sync_state(
                connection,
                QuarterSyncState(
                    quarter,
                    FACTS_COMPLETE,
                    COVERS_INCOMPLETE,
                    subject_count,
                    subject_count,
                    completed_at,
                    completed_at,
                ),
            )
        cleanup_warnings = self._remove_stale_covers(stale_ids)
        cover_downloaded, cover_reused, cover_missing, cover_warnings = (
            self._sync_covers(
                item
                for item in prepared
                if item.snapshot.premiere is not None
                and item.snapshot.premiere.quarter == quarter
            )
        )
        warnings = tuple(
            [
                *discovery_warnings,
                *premiere_conflict_warnings,
                *cleanup_warnings,
                *cover_warnings,
                *reconciliation.warnings,
            ]
        )
        covers_status = (
            COVERS_COMPLETE
            if cover_missing == 0 and not cleanup_warnings
            else COVERS_INCOMPLETE
        )
        with self.repository.transaction() as connection:
            self.repository.write_sync_state(
                connection,
                QuarterSyncState(
                    quarter,
                    FACTS_COMPLETE,
                    covers_status,
                    subject_count,
                    cover_missing,
                    _timestamp(),
                    completed_at,
                ),
            )
        persisted_review_count = len(self.repository.list_review_issues(quarter))
        source_counts, episode_known, episode_unknown = _persisted_fact_aggregates(
            self.repository, quarter
        )
        return QuarterSyncResult(
            quarter,
            FACTS_COMPLETE,
            covers_status,
            len(batch.candidates),
            accepted_tv,
            accepted_movie,
            rejected_non_japanese,
            blacklisted,
            tuple(reviews) + tuple(issue for _, issue in reconciliation.reviews),
            tuple(external_reviews),
            warnings,
            (),
            cover_downloaded,
            cover_reused,
            cover_missing,
            reconciliation.confirmed_by_end_date,
            reconciliation.confirmed_by_episode,
            reconciliation.unresolved,
            natural_premiere_tv,
            early_premieres,
            boundary_reviews,
            auto_blacklisted=tuple(auto_blacklisted),
            manual_blacklisted=manual_blacklisted,
            existing_auto_blacklisted=existing_auto_blacklisted,
            canonical_detail_requests=canonical_detail_requests,
            persisted_review_count=persisted_review_count,
            source_counts=source_counts,
            episode_known=episode_known,
            episode_unknown=episode_unknown,
        )

    def _prepare_subject(
        self,
        detail: SubjectDetail,
        decision: object,
        existing: SubjectSnapshot | None,
        review_scope: Quarter,
    ) -> _PreparedSubject:
        assert isinstance(decision, AdmissionDecision)
        assert decision.media_format is not None
        assert decision.japanese is not None
        name = detail.name
        if name is None or not name.strip():
            raise ValueError("subject name is required")
        aliases = normalize_aliases(
            (value for value in (detail.name_cn,) if value), excluded=(name,)
        )
        current_reviews = tuple(
            _review_issue(finding, detail.subject_id) for finding in decision.reviews
        )
        preserved_reviews = tuple(
            issue
            for issue in (() if existing is None else existing.review_issues)
            if issue.candidate_quarter not in {None, review_scope}
        )
        reviews = preserved_reviews + current_reviews
        snapshot = SubjectSnapshot(
            SubjectRecord(
                detail.subject_id,
                name,
                detail.name_cn,
                detail.summary,
                decision.media_format,
                detail.air_date,
                _end_date(detail.infobox),
                _episode_count(detail),
                detail.rating_score,
                detail.rating_total,
                decision.japanese,
            ),
            aliases,
            tuple(
                item
                for item in (
                    _infobox_item(value.key, value.value) for value in detail.infobox
                )
                if item is not None
            ),
            order_tag_candidates(
                _tag_candidate(item.name, item.count) for item in detail.tags
            ),
            _source_decision(detail, self.source_rules),
            decision.premiere,
            existing.continuing if existing is not None else (),
            existing.cover if existing is not None else None,
            reviews,
        )
        return _PreparedSubject(
            snapshot, detail.images.largest_available, detail.images.largest_variant
        )

    def _reconcile_continuing(
        self,
        quarter: Quarter,
        *,
        excluded_subject_ids: frozenset[int] = frozenset(),
    ) -> _ContinuingReconciliation:
        """Gather all continuing evidence before mutating target-quarter rows."""
        target_start = date(quarter.year, quarter.month, 1)
        appearances: list[tuple[int, QuarterAppearance]] = []
        examined: list[SubjectSnapshot] = []
        reviews: list[tuple[SubjectSnapshot, ReviewIssue]] = []
        warnings: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        confirmed_by_end_date = 0
        confirmed_by_episode = 0

        for snapshot in self.repository.list_tv_subjects_appearing_in_previous_quarter(
            quarter
        ):
            premiere = snapshot.premiere
            subject = snapshot.subject
            if subject.subject_id in excluded_subject_ids or (
                premiere is None
                or premiere.quarter >= quarter
                or subject.air_date is None
                or subject.air_date >= target_start
            ):
                continue
            examined.append(snapshot)
            if subject.end_date is not None and subject.end_date >= target_start:
                appearances.append(
                    (
                        subject.subject_id,
                        _automatic_continuing(
                            quarter,
                            "structured_end_date",
                            subject.end_date.isoformat(),
                        ),
                    )
                )
                confirmed_by_end_date += 1
                continue

            try:
                airdates = self.api.get_main_episode_airdates(subject.subject_id)
            except BangumiApiError as error:
                errors.append(
                    {
                        "code": f"continuing_{error.code}",
                        "summary": (
                            f"main episode evidence unavailable for "
                            f"subject {subject.subject_id}: {error.summary}"
                        ),
                    }
                )
                continue

            matching = tuple(
                item for item in airdates if quarter_for_date(item) == quarter
            )
            if matching:
                if subject.end_date is not None and subject.end_date < target_start:
                    reviews.append(
                        (
                            snapshot,
                            _continuing_review(
                                CONTINUING_EVIDENCE_CONFLICT,
                                quarter,
                                subject.end_date.isoformat(),
                                {
                                    "end_date": subject.end_date.isoformat(),
                                    "main_episode_airdate": matching[0].isoformat(),
                                },
                            ),
                        )
                    )
                    continue
                appearances.append(
                    (
                        subject.subject_id,
                        _automatic_continuing(
                            quarter,
                            "main_episode_airdate",
                            matching[0].isoformat(),
                        ),
                    )
                )
                confirmed_by_episode += 1
                continue

            if not airdates and _has_target_season_tag(snapshot, quarter):
                reviews.append(
                    (
                        snapshot,
                        _continuing_review(
                            CONTINUING_EVIDENCE_UNRESOLVED,
                            quarter,
                            _target_season_tag(quarter),
                            {"season_tag": _target_season_tag(quarter)},
                        ),
                    )
                )
            else:
                warnings.append(
                    {
                        "code": "continuing_not_confirmed",
                        "summary": (
                            f"no main episode airdate confirms subject "
                            f"{subject.subject_id} in {_quarter_label(quarter)}"
                        ),
                    }
                )

        return _ContinuingReconciliation(
            quarter,
            tuple(examined),
            tuple(appearances),
            tuple(reviews),
            tuple(warnings),
            tuple(errors),
            confirmed_by_end_date,
            confirmed_by_episode,
        )

    def _backfill_next_quarter(self, quarter: Quarter) -> tuple[dict[str, str], ...]:
        """Converge the already-managed next quarter after a prior sync succeeds."""
        target = _next_quarter(quarter)
        state = self.repository.get_sync_state(target)
        if state is None:
            return ()
        premiere_subject_ids = {
            snapshot.subject.subject_id
            for snapshot in self.repository.list_subjects_appearing_in_quarter(
                target, appearance_kind=QuarterAppearanceKind.PREMIERE
            )
        }
        try:
            reconciliation = self._reconcile_continuing(
                target, excluded_subject_ids=frozenset(premiere_subject_ids)
            )
        except KeyboardInterrupt:
            self._write_incomplete(target, state, 0, ())
            raise
        if reconciliation.errors:
            self._write_incomplete(target, state, 0, reconciliation.errors)
            return tuple(
                {
                    "code": "continuing_backfill_failed",
                    "summary": item["summary"],
                }
                for item in reconciliation.errors
            )
        appearances = tuple(
            (subject_id, appearance)
            for subject_id, appearance in reconciliation.appearances
            if subject_id not in premiere_subject_ids
        )
        with self.repository.transaction() as connection:
            self.repository.replace_automatic_continuing_for_quarter(
                connection, target, appearances
            )
            self._replace_continuing_reviews(connection, reconciliation, {})
            self.repository.write_sync_state(
                connection,
                replace(
                    state,
                    subject_count=_appearance_count(connection, target),
                    last_attempt_at=_timestamp(),
                ),
            )
        return reconciliation.warnings

    def _replace_continuing_reviews(
        self,
        connection: sqlite3.Connection,
        reconciliation: _ContinuingReconciliation,
        replacements: Mapping[int, SubjectSnapshot],
    ) -> None:
        """Replace only this target's continuing review findings."""
        continuing_codes = {
            CONTINUING_EVIDENCE_UNRESOLVED,
            CONTINUING_EVIDENCE_CONFLICT,
        }
        self.repository.delete_review_issues_for_quarter(
            connection, reconciliation.quarter, continuing_codes
        )
        issues_by_subject: dict[int, list[ReviewIssue]] = {
            subject_id: [
                issue
                for issue in replacements.get(subject_id, snapshot).review_issues
                if not (
                    issue.issue_code in continuing_codes
                    and issue.candidate_quarter == reconciliation.quarter
                )
            ]
            for subject_id, snapshot in (
                (item.subject.subject_id, item) for item in reconciliation.examined
            )
        }
        for snapshot, issue in reconciliation.reviews:
            subject_id = snapshot.subject.subject_id
            issues_by_subject[subject_id].append(issue)
        for subject_id, issues in issues_by_subject.items():
            self.repository.replace_review_issues(connection, subject_id, tuple(issues))

    def _purge_blacklist(self) -> None:
        excluded = frozenset(self._active_excluded_subject_ids)
        if not excluded:
            return
        affected = self.repository.affected_quarters(excluded)
        prior_states = {
            quarter: self.repository.get_sync_state(quarter) for quarter in affected
        }
        try:
            covers = self.covers.quarantine_subject_covers(excluded)
        except OSError as error:
            raise SyncError("blacklisted cover cleanup failed") from error
        try:
            with self.repository.transaction() as connection:
                self.repository.delete_subjects(connection, excluded)
                for quarter in affected:
                    prior = prior_states[quarter]
                    if prior is not None:
                        self.repository.write_sync_state(
                            connection,
                            replace(
                                prior,
                                facts_status=FACTS_INCOMPLETE,
                                last_attempt_at=_timestamp(),
                            ),
                        )
        except BaseException:
            try:
                covers.restore()
            except OSError as recovery_error:
                raise SyncError(
                    "blacklist transaction failed and cover recovery is incomplete"
                ) from recovery_error
            raise
        try:
            covers.finalize()
        except OSError as error:
            raise SyncError("blacklisted cover cleanup finalization failed") from error

    def _record_auto_blacklist(
        self, detail: SubjectDetail, existing: SubjectSnapshot | None
    ) -> bool:
        """Persist one new automatic exclusion with existing-data rollback."""
        subject_id = detail.subject_id
        if subject_id in self._active_excluded_subject_ids:
            return False
        if not self.settings_path.is_file():
            raise SyncError("automatic blacklist configuration is missing")
        original_config = self.settings_path.read_bytes()
        affected = (
            self.repository.affected_quarters(frozenset({subject_id}))
            if existing is not None
            else ()
        )
        prior_states = {
            quarter: self.repository.get_sync_state(quarter) for quarter in affected
        }
        covers = None
        if existing is not None:
            try:
                covers = self.covers.quarantine_subject_covers({subject_id})
            except OSError as error:
                raise SyncError(
                    "automatic blacklist cover quarantine failed"
                ) from error
        config_changed = False
        try:
            with self.repository.transaction() as connection:
                if existing is not None:
                    self.repository.delete_subjects(connection, frozenset({subject_id}))
                    for quarter in affected:
                        prior = prior_states[quarter]
                        if prior is not None:
                            self.repository.write_sync_state(
                                connection,
                                replace(
                                    prior,
                                    facts_status=FACTS_INCOMPLETE,
                                    last_attempt_at=_timestamp(),
                                ),
                            )
                add_auto_excluded_subject(
                    self.settings_path,
                    subject_id,
                    name_cn=detail.name_cn,
                    name_original=detail.name,
                )
                config_changed = True
                self._auto_excluded_subject_ids.add(subject_id)
                self._active_excluded_subject_ids.add(subject_id)
        except BaseException:
            self._auto_excluded_subject_ids.discard(subject_id)
            self._active_excluded_subject_ids.discard(subject_id)
            if config_changed or self.settings_path.read_bytes() != original_config:
                try:
                    restore_archive_config(self.settings_path, original_config)
                except OSError as error:
                    if covers is not None:
                        try:
                            covers.restore()
                        except OSError as recovery_error:
                            raise SyncError(
                                "automatic blacklist rollback is incomplete"
                            ) from recovery_error
                    raise SyncError(
                        "automatic blacklist configuration rollback failed"
                    ) from error
            if covers is not None:
                try:
                    covers.restore()
                except OSError as error:
                    raise SyncError(
                        "automatic blacklist cover recovery is incomplete"
                    ) from error
            raise
        if covers is not None:
            try:
                covers.finalize()
            except OSError as error:
                raise SyncError(
                    "automatic blacklist cover cleanup finalization failed"
                ) from error
        return True

    def _write_incomplete(
        self,
        quarter: Quarter,
        prior: QuarterSyncState | None,
        discovered: int,
        errors: tuple[dict[str, str], ...],
        *,
        blacklisted: int = 0,
        manual_blacklisted: int = 0,
        existing_auto_blacklisted: int = 0,
        auto_blacklisted: tuple[dict[str, object], ...] = (),
        canonical_detail_requests: int = 0,
    ) -> QuarterSyncResult:
        with self.repository.transaction() as connection:
            self.repository.write_sync_state(
                connection,
                QuarterSyncState(
                    quarter,
                    FACTS_INCOMPLETE,
                    prior.covers_status if prior is not None else COVERS_INCOMPLETE,
                    prior.subject_count if prior is not None else 0,
                    prior.missing_cover_count if prior is not None else 0,
                    _timestamp(),
                    prior.last_success_at if prior is not None else None,
                ),
            )
        persisted_review_count = len(self.repository.list_review_issues(quarter))
        return QuarterSyncResult(
            quarter,
            FACTS_INCOMPLETE,
            prior.covers_status if prior is not None else COVERS_INCOMPLETE,
            discovered,
            0,
            0,
            0,
            blacklisted,
            (),
            (),
            (),
            errors,
            auto_blacklisted=auto_blacklisted,
            manual_blacklisted=manual_blacklisted,
            existing_auto_blacklisted=existing_auto_blacklisted,
            canonical_detail_requests=canonical_detail_requests,
            persisted_review_count=persisted_review_count,
        )

    def _sync_covers(
        self, prepared: Iterable[_PreparedSubject]
    ) -> tuple[int, int, int, list[dict[str, str]]]:
        subjects = tuple(prepared)
        results: dict[int, CoverResult] = {}
        executor = ThreadPoolExecutor(max_workers=MAX_COVER_CONCURRENCY)
        futures = {}
        subject_iter = iter(subjects)
        try:
            while len(futures) < MAX_COVER_CONCURRENCY:
                item = next(subject_iter, None)
                if item is None:
                    break
                futures[executor.submit(self._sync_cover, item)] = (
                    item.snapshot.subject.subject_id
                )
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                completed = []
                for future in done:
                    subject_id = futures.pop(future)
                    completed.append((subject_id, future.result()))
                for subject_id, result in completed:
                    results[subject_id] = result
                for _ in completed:
                    item = next(subject_iter, None)
                    if item is not None:
                        futures[executor.submit(self._sync_cover, item)] = (
                            item.snapshot.subject.subject_id
                        )
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        successful = [
            item
            for item in subjects
            if results[item.snapshot.subject.subject_id].cover is not None
        ]
        if successful:
            with self.repository.transaction() as connection:
                for item in successful:
                    result = results[item.snapshot.subject.subject_id]
                    self.repository.replace_subject_snapshot(
                        connection, replace(item.snapshot, cover=result.cover)
                    )
        downloaded = sum(result.status == "downloaded" for result in results.values())
        reused = sum(result.status == "reused" for result in results.values())
        missing = sum(
            result.status in {"failed", "missing"} for result in results.values()
        )
        warnings = [
            {
                "code": result.error_code or "cover_missing",
                "summary": result.error_summary or "cover unavailable",
            }
            for result in results.values()
            if result.status in {"failed", "missing"}
        ]
        return downloaded, reused, missing, warnings

    def _sync_cover(self, prepared: _PreparedSubject) -> CoverResult:
        return self.covers.sync_subject(
            prepared.snapshot, prepared.cover_url, prepared.cover_variant
        )

    def _remove_stale_covers(self, subject_ids: Iterable[int]) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        for subject_id in subject_ids:
            try:
                self.covers.remove_subject_cover(subject_id)
            except OSError:
                warnings.append(
                    {"code": "cover_cleanup", "summary": "stale cover cleanup failed"}
                )
        return warnings

    def _write_report(
        self,
        scope: SyncScope,
        started_at: str,
        results: Iterable[QuarterSyncResult],
        *,
        interrupted: bool,
    ) -> Path:
        completed_at = _timestamp()
        serialized = [_result_payload(item) for item in results]
        source_totals: Counter[str] = Counter()
        new_auto_by_reason: Counter[str] = Counter()
        episode_known = 0
        episode_unknown = 0
        legacy_zero_written = 0
        for item in serialized:
            source_totals.update(item["source_counts"])
            new_auto_by_reason.update(item["new_auto_by_reason"])
            episode = item["episode_count"]
            episode_known += episode["known"]
            episode_unknown += episode["unknown"]
            legacy_zero_written += episode["legacy_zero_written"]
        payload = {
            "scope": scope.label,
            "started_at": started_at,
            "completed_at": completed_at,
            "interrupted": interrupted,
            "discovery_sources": ["browse", "search"],
            "quarters": serialized,
            "accepted_tv": sum(item["accepted_tv"] for item in serialized),
            "accepted_movie": sum(item["accepted_movie"] for item in serialized),
            "rejected_non_japanese": sum(
                item["rejected_non_japanese"] for item in serialized
            ),
            "blacklisted": sum(item["blacklisted"] for item in serialized),
            "manual_blacklisted": sum(
                item["manual_blacklisted"]
                for item in serialized
            ),
            "existing_auto_blacklisted": sum(
                item["existing_auto_blacklisted"] for item in serialized
            ),
            "auto_blacklisted_count": sum(
                len(item["auto_blacklisted"]) for item in serialized
            ),
            "new_auto_by_reason": dict(sorted(new_auto_by_reason.items())),
            "canonical_detail_requests": sum(
                item["canonical_detail_requests"] for item in serialized
            ),
            "source_counts": dict(sorted(source_totals.items())),
            "episode_count": {
                "known": episode_known,
                "unknown": episode_unknown,
                "legacy_zero_written": legacy_zero_written,
            },
            "review_count": sum(
                item["persisted_review_count"] for item in serialized
            ),
            "persisted_review_count": sum(
                item["persisted_review_count"] for item in serialized
            ),
            "external_review_count": sum(
                len(item["external_reviews"]) for item in serialized
            ),
            "warning_count": sum(len(item["warnings"]) for item in serialized),
            "error_count": sum(len(item["errors"]) for item in serialized),
        }
        return _write_json_report(
            self.reports_directory, f"sync-{scope.label}", payload
        )

    def _write_single_import_report(
        self, subject_id: int, snapshot: SubjectSnapshot, cover: CoverResult
    ) -> Path:
        return _write_json_report(
            self.reports_directory,
            f"manual-import-{subject_id}",
            {
                "subject_id": subject_id,
                "media_format": snapshot.subject.media_format.value,
                "japanese": snapshot.subject.japanese.classification.value,
                "cover_status": cover.status,
                "cover_error": cover.error_code,
            },
        )


def _quarter_from_values(values: list[str], label: str) -> Quarter:
    if len(values) != 2:
        raise ValueError(f"{label} requires YEAR QUARTER_MONTH")
    try:
        return Quarter(int(values[0]), int(values[1]))
    except ValueError as error:
        raise ValueError(f"{label} requires a valid YEAR QUARTER_MONTH") from error


def _previous_quarter(quarter: Quarter) -> Quarter:
    if quarter.month == 1:
        return Quarter(quarter.year - 1, 10)
    return Quarter(quarter.year, quarter.month - 3)


def _next_quarter(quarter: Quarter) -> Quarter:
    if quarter.month == 10:
        return Quarter(quarter.year + 1, 1)
    return Quarter(quarter.year, quarter.month + 3)


def _automatic_continuing(
    quarter: Quarter, evidence_type: str, evidence_value: str
) -> QuarterAppearance:
    return QuarterAppearance(
        quarter,
        QuarterAppearanceKind.CONTINUING,
        QuarterAssignmentSource.AUTOMATIC,
        evidence_type,
        evidence_value,
    )


def _continuing_review(
    issue_code: str,
    quarter: Quarter,
    observed_value: str,
    details: dict[str, str],
) -> ReviewIssue:
    return ReviewIssue(issue_code, quarter, observed_value, details, _timestamp())


def _target_season_tag(quarter: Quarter) -> str:
    return f"{quarter.year}年{quarter.month}月"


def _has_target_season_tag(snapshot: SubjectSnapshot, quarter: Quarter) -> bool:
    return _target_season_tag(quarter) in snapshot.tags


def _appearance_count(connection: sqlite3.Connection, quarter: Quarter) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT subject_id) AS count
        FROM subject_quarters WHERE year = ? AND quarter_month = ?
        """,
        (quarter.year, quarter.month),
    ).fetchone()
    assert row is not None
    return int(row["count"])


def _persisted_fact_aggregates(
    repository: SubjectRepository, quarter: Quarter
) -> tuple[tuple[tuple[str, int], ...], int, int]:
    snapshots = repository.list_subjects_appearing_in_quarter(quarter)
    source_counts = Counter(
        snapshot.source.source_type.value for snapshot in snapshots
    )
    known = sum(snapshot.subject.episode_count is not None for snapshot in snapshots)
    return tuple(sorted(source_counts.items())), known, len(snapshots) - known


def _quarter_label(quarter: Quarter) -> str:
    return f"{quarter.year:04d}-{quarter.month:02d}"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _discovery_error(failure: DiscoveryFailure) -> dict[str, str]:
    return {
        "code": f"discovery_{failure.source.value}_{failure.code}",
        "summary": failure.summary,
    }


def _discovery_warning(failure: DiscoveryFailure) -> dict[str, str]:
    return {
        "code": f"discovery_{failure.source.value}_{failure.code}",
        "summary": failure.summary,
    }


def _review_issue(finding: ReviewFinding, subject_id: int) -> ReviewIssue:
    return ReviewIssue(
        finding.issue_code,
        finding.candidate_quarter,
        finding.observed_value,
        {**finding.details, "subject_id": subject_id},
        _timestamp(),
    )


def _eligible_for_auto_blacklist(decision: AdmissionDecision) -> bool:
    return (
        decision.media_format in {MediaFormat.TV, MediaFormat.MOVIE}
        and decision.japanese is not None
        and decision.japanese.classification is JapaneseClassification.ACCEPTED_JAPANESE
        and all(
            issue.issue_code != DISCOVERY_MEDIA_CONFLICT
            for issue in decision.reviews
        )
    )


def _persisted_unresolved_cold_review(
    decision: AdmissionDecision,
) -> tuple[str, Quarter] | None:
    """Return one explicit cold-review issue when its target is unambiguous."""
    if (
        decision.status is not AdmissionStatus.REVIEW
        or decision.media_format not in {MediaFormat.TV, MediaFormat.MOVIE}
        or decision.japanese is None
        or decision.japanese.classification
        is not JapaneseClassification.ACCEPTED_JAPANESE
        or not decision.reviews
    ):
        return None
    if any(
        not is_unresolved_cold_review(issue.issue_code)
        for issue in decision.reviews
    ):
        return None
    target_quarters = {issue.candidate_quarter for issue in decision.reviews}
    if len(target_quarters) != 1:
        return None
    target_quarter = next(iter(target_quarters))
    if target_quarter is None:
        return None
    return decision.reviews[0].issue_code, target_quarter


def _external_unresolved_cold_review(
    decision: AdmissionDecision, target_quarter: Quarter
) -> tuple[str, Quarter] | None:
    """Return a Search-only cold issue whose sync scope supplies its target."""
    if (
        decision.status is not AdmissionStatus.REVIEW
        or decision.media_format is not None
        or decision.japanese is not None
        or not decision.reviews
    ):
        return None
    if any(
        not is_unresolved_cold_review(issue.issue_code)
        or issue.candidate_quarter is not None
        for issue in decision.reviews
    ):
        return None
    return decision.reviews[0].issue_code, target_quarter


def _auto_blacklist_error_summary(error: Exception) -> str:
    if isinstance(error, SyncError):
        return str(error)
    return "automatic blacklist transaction failed"


def _auto_blacklist_event(
    detail: SubjectDetail,
    evaluation_date: date,
    *,
    reason: str = "low_rating_count",
    issue_code: str | None = None,
    target_quarter: Quarter | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "subject_id": detail.subject_id,
        "title": detail.name_cn or detail.name or str(detail.subject_id),
        "evaluation_date": evaluation_date.isoformat(),
        "rating_count": detail.rating_total,
        "reason": reason,
    }
    if detail.air_date is not None:
        event["air_date"] = detail.air_date.isoformat()
        event["days_since_air_date"] = (evaluation_date - detail.air_date).days
    else:
        event["air_date"] = None
        event["days_since_air_date"] = None
    if reason == "low_rating_count":
        assert detail.air_date is not None
        assert detail.rating_total is not None
        event.update(
            {
                "threshold": "rating_count < 30",
                "protection_days": "> 7 days",
            }
        )
    else:
        assert issue_code is not None
        assert target_quarter is not None
        quarter_end = quarter_end_date(target_quarter)
        event.update(
            {
                "issue_code": issue_code,
                "target_quarter": _quarter_label(target_quarter),
                "quarter_end": quarter_end.isoformat(),
                "days_after_quarter_end": (evaluation_date - quarter_end).days,
                "rating_threshold": 30,
                "rating_missing": detail.rating_total is None,
                "protection_days": "> 7 days after quarter end",
            }
        )
    return event


def _external_review(
    subject_id: int, decision: AdmissionDecision, target_quarter: Quarter
) -> dict[str, object]:
    """Report a REVIEW whose media format is too uncertain for formal storage."""
    finding = decision.reviews[0]
    return {
        "subject_id": subject_id,
        "issue_code": finding.issue_code,
        "candidate_quarter": (
            _quarter_label(finding.candidate_quarter)
            if finding.candidate_quarter is not None
            else None
        ),
        "observed_value": finding.observed_value,
        "command": (
            f"bgmb assign {subject_id} "
            f"{target_quarter.year} {target_quarter.month}"
        ),
    }


def _episode_count(detail: SubjectDetail) -> int | None:
    for value in (detail.total_episodes, detail.eps):
        positive = _strict_positive_integer(value)
        if positive is not None:
            return positive
    for item in detail.infobox:
        if item.key == "话数":
            positive = _strict_positive_integer(item.value)
            if positive is not None:
                return positive
    return None


def _strict_positive_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdecimal():
            parsed = int(normalized)
            return parsed if parsed > 0 else None
    return None


def _end_date(items: Iterable[object]) -> date | None:
    from bgm_side_b.api import ApiInfoboxItem

    for item in items:
        if (
            isinstance(item, ApiInfoboxItem)
            and item.key == "播放结束"
            and isinstance(item.value, str)
        ):
            try:
                return date.fromisoformat(item.value)
            except ValueError:
                return None
    return None


def _infobox_item(key: str, value: object):
    from bgm_side_b.repository import InfoboxItem

    return InfoboxItem(key, value) if key.strip() else None


def _tag_candidate(name: str, count: int | None):
    from bgm_side_b.rules import TagCandidate

    return TagCandidate(name, count or 0)


def _source_decision(detail: SubjectDetail, rules: ArchiveSourceRules):
    evidence = [
        SourceEvidence(rules.infobox_values[item.value], "infobox", item.value)
        for item in detail.infobox
        if item.key in rules.infobox_keys
        and isinstance(item.value, str)
        and item.value in rules.infobox_values
    ]
    if not evidence:
        evidence = [
            SourceEvidence(rules.tag_values[item.name], "tag", item.name)
            for item in detail.tags
            if item.name in rules.tag_values
        ]
    return resolve_source(evidence)


def _skipped_result(
    quarter: Quarter,
    state: QuarterSyncState,
    *,
    source_counts: tuple[tuple[str, int], ...] = (),
    episode_known: int = 0,
    episode_unknown: int = 0,
) -> QuarterSyncResult:
    return QuarterSyncResult(
        quarter,
        state.facts_status,
        state.covers_status,
        0,
        0,
        0,
        0,
        0,
        (),
        (),
        (),
        (),
        skipped=True,
        source_counts=source_counts,
        episode_known=episode_known,
        episode_unknown=episode_unknown,
    )


def _result_payload(result: QuarterSyncResult) -> dict[str, object]:
    return {
        "quarter": _quarter_label(result.quarter),
        "facts_status": result.facts_status,
        "covers_status": result.covers_status,
        "discovered": result.discovered,
        "accepted_tv": result.accepted_tv,
        "accepted_movie": result.accepted_movie,
        "rejected_non_japanese": result.rejected_non_japanese,
        "blacklisted": result.blacklisted,
        "manual_blacklisted": result.manual_blacklisted,
        "existing_auto_blacklisted": result.existing_auto_blacklisted,
        "auto_blacklisted": list(result.auto_blacklisted),
        "new_auto_by_reason": dict(
            sorted(
                Counter(
                    item["reason"]
                    for item in result.auto_blacklisted
                    if isinstance(item.get("reason"), str)
                ).items()
            )
        ),
        "canonical_detail_requests": result.canonical_detail_requests,
        "persisted_review_count": result.persisted_review_count,
        "review_count": result.persisted_review_count,
        "source_counts": dict(result.source_counts),
        "episode_count": {
            "known": result.episode_known,
            "unknown": result.episode_unknown,
            "legacy_zero_written": result.legacy_zero_written,
        },
        "reviews": [
            {
                "subject_id": issue.details.get("subject_id"),
                "issue_code": issue.issue_code,
                "candidate_quarter": (
                    _quarter_label(issue.candidate_quarter)
                    if issue.candidate_quarter is not None
                    else None
                ),
            }
            for issue in result.reviews
        ],
        "external_reviews": list(result.external_reviews),
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "cover_downloaded": result.cover_downloaded,
        "cover_reused": result.cover_reused,
        "cover_missing": result.cover_missing,
        "continuing_end_date": result.continuing_end_date,
        "continuing_episode": result.continuing_episode,
        "continuing_unresolved": result.continuing_unresolved,
        "premiere": {
            "natural_tv": result.natural_premiere_tv,
            "early_auto_resolved": list(result.early_premieres),
            "boundary_reviews": result.boundary_reviews,
        },
        "continuing": {
            "confirmed_by_end_date": result.continuing_end_date,
            "confirmed_by_main_episode": result.continuing_episode,
            "unresolved": result.continuing_unresolved,
        },
        "skipped": result.skipped,
    }


def _write_json_report(
    directory: Path, stem: str, payload: Mapping[str, object]
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = directory / f"{stem}-{timestamp}.json"
    descriptor, name = tempfile.mkstemp(
        dir=directory, prefix=f".{stem}-", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
