"""Atomic TV/MOVIE archive synchronization into the current fact store."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from bgm_side_b.admission import (
    AdmissionDecision,
    AdmissionStatus,
    QuarterOverride,
    ReviewFinding,
    admit_subject,
    quarter_for_date,
)
from bgm_side_b.api import BangumiApiClient, BangumiApiError, SubjectDetail
from bgm_side_b.archive_config import ArchiveSourceRules, ArchiveSyncSettings
from bgm_side_b.discovery import (
    BrowseDiscoveryAdapter,
    DiscoveryFailure,
    SearchDiscoveryAdapter,
)
from bgm_side_b.domain import (
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
    report_path: Path
    cover: CoverResult


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

    def run(self, scope: SyncScope) -> SyncRun:
        """Synchronize the requested scope without writing any legacy fact tables."""
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
                    results.append(_skipped_result(quarter, current))
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
        if subject_id in self.settings.excluded_subject_ids:
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
            excluded_subject_ids=self.settings.excluded_subject_ids,
            override=override,
        )
        if decision.status is not AdmissionStatus.ACCEPTED:
            raise SyncError(
                "manual import requires confirmed Anime, TV/MOVIE, and Japanese facts"
            )
        prepared = self._prepare_subject(detail, decision, None)
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
        report_path = self._write_single_import_report(subject_id, snapshot, cover)
        return SingleSubjectImport(snapshot, report_path, cover)

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
        batch = self.browse.discover(quarter)
        if batch.failures:
            errors = tuple(_discovery_error(item) for item in batch.failures)
            return self._write_incomplete(quarter, prior, len(batch.candidates), errors)

        prepared: list[_PreparedSubject] = []
        reviews: list[ReviewIssue] = []
        external_reviews: list[dict[str, object]] = []
        errors: list[dict[str, str]] = []
        blacklisted = 0
        rejected_non_japanese = 0
        accepted_tv = 0
        accepted_movie = 0
        try:
            for candidate in batch.candidates:
                if candidate.subject_id in self.settings.excluded_subject_ids:
                    blacklisted += 1
                    continue
                try:
                    detail = candidate.detail or self.api.get_subject(
                        candidate.subject_id
                    )
                    decision = admit_subject(
                        candidate,
                        detail,
                        quarter,
                        excluded_subject_ids=self.settings.excluded_subject_ids,
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
                if decision.status is AdmissionStatus.BLACKLISTED:
                    blacklisted += 1
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
                    existing = self.repository.get_subject_facts(candidate.subject_id)
                    prepared_subject = self._prepare_subject(detail, decision, existing)
                except ValueError:
                    errors.append(
                        {
                            "code": "normalization",
                            "summary": "subject facts are invalid",
                        }
                    )
                    continue
                prepared.append(prepared_subject)
                reviews.extend(prepared_subject.snapshot.review_issues)
                if decision.status is AdmissionStatus.ACCEPTED:
                    if decision.media_format.value == "TV":
                        accepted_tv += 1
                    else:
                        accepted_movie += 1
        except KeyboardInterrupt:
            self._write_incomplete(quarter, prior, len(batch.candidates), ())
            raise
        if errors:
            return self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                tuple(errors),
                blacklisted=blacklisted,
            )

        premiere_subject_ids = {
            item.snapshot.subject.subject_id
            for item in prepared
            if item.snapshot.premiere is not None
            and item.snapshot.premiere.quarter == quarter
        }
        reconciliation = self._reconcile_continuing(
            quarter, excluded_subject_ids=frozenset(premiere_subject_ids)
        )
        if reconciliation.errors:
            return self._write_incomplete(
                quarter,
                prior,
                len(batch.candidates),
                reconciliation.errors,
                blacklisted=blacklisted,
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
            [*cleanup_warnings, *cover_warnings, *reconciliation.warnings]
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
        )

    def _prepare_subject(
        self,
        detail: SubjectDetail,
        decision: object,
        existing: SubjectSnapshot | None,
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
        reviews = tuple(
            _review_issue(finding, detail.subject_id) for finding in decision.reviews
        )
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
        reconciliation = self._reconcile_continuing(
            target, excluded_subject_ids=frozenset(premiere_subject_ids)
        )
        if reconciliation.errors:
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
        issues_by_subject: dict[int, list[ReviewIssue]] = {
            subject_id: [
                issue
                for issue in replacements.get(subject_id, snapshot).review_issues
                if not (
                    issue.issue_code
                    in {
                        CONTINUING_EVIDENCE_UNRESOLVED,
                        CONTINUING_EVIDENCE_CONFLICT,
                    }
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
        excluded = self.settings.excluded_subject_ids
        if not excluded:
            return
        affected = self.repository.affected_quarters(excluded)
        prior_states = {
            quarter: self.repository.get_sync_state(quarter) for quarter in affected
        }
        for subject_id in excluded:
            try:
                self.covers.remove_subject_cover(subject_id)
            except OSError as error:
                raise SyncError("blacklisted cover cleanup failed") from error
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

    def _write_incomplete(
        self,
        quarter: Quarter,
        prior: QuarterSyncState | None,
        discovered: int,
        errors: tuple[dict[str, str], ...],
        *,
        blacklisted: int = 0,
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
        )

    def _sync_covers(
        self, prepared: Iterable[_PreparedSubject]
    ) -> tuple[int, int, int, list[dict[str, str]]]:
        subjects = tuple(prepared)
        results: dict[int, CoverResult] = {}
        with ThreadPoolExecutor(max_workers=MAX_COVER_CONCURRENCY) as executor:
            futures = {
                executor.submit(
                    self._sync_cover, item
                ): item.snapshot.subject.subject_id
                for item in subjects
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()
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
            "review_count": sum(
                len(item["reviews"]) + len(item["external_reviews"])
                for item in serialized
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


def _quarter_label(quarter: Quarter) -> str:
    return f"{quarter.year:04d}-{quarter.month:02d}"


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _discovery_error(failure: DiscoveryFailure) -> dict[str, str]:
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
    value = detail.total_episodes if detail.total_episodes is not None else detail.eps
    return value if value is None or value >= 0 else None


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


def _skipped_result(quarter: Quarter, state: QuarterSyncState) -> QuarterSyncResult:
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
