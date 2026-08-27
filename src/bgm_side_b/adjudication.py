"""Manual review rendering and archive-quarter assignment for stored subjects."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from bgm_side_b.admission import (
    DISCOVERY_DATE_MISMATCH,
    JAPANESE_CLASSIFICATION_UNRESOLVED,
    TV_QUARTER_BOUNDARY,
    AdmissionStatus,
    QuarterOverride,
    ReviewFinding,
    admit_subject,
    quarter_for_date,
)
from bgm_side_b.api import ApiInfoboxItem, ApiTag, ImageUrls, SubjectDetail
from bgm_side_b.discovery import DiscoveredSubject
from bgm_side_b.domain import JapaneseClassification, JapaneseDecision, Quarter
from bgm_side_b.japanese_overrides import (
    JapaneseOverride,
    load_japanese_overrides,
    save_japanese_overrides,
)
from bgm_side_b.media import CoverStore
from bgm_side_b.overrides import load_quarter_overrides, save_quarter_overrides
from bgm_side_b.repository import ReviewIssue, SubjectRepository, SubjectSnapshot


class AssignmentError(ValueError):
    """A manual assignment is invalid or cannot safely change local facts."""


_QUARTER_ISSUES = frozenset(
    {
        TV_QUARTER_BOUNDARY,
        DISCOVERY_DATE_MISMATCH,
        "TV_QUARTER_DATE_UNRESOLVED",
        "MOVIE_DATE_UNRESOLVED",
    }
)


class ArchiveAdjudicator:
    """Apply Git-trackable quarter decisions without overriding admission facts."""

    def __init__(
        self,
        repository: SubjectRepository,
        overrides_path: Path,
        excluded_subject_ids: frozenset[int],
        japanese_overrides_path: Path | None = None,
    ) -> None:
        self.repository = repository
        self.overrides_path = overrides_path
        self.excluded_subject_ids = excluded_subject_ids
        self.japanese_overrides_path = (
            japanese_overrides_path
            or overrides_path.with_name("japanese-overrides.toml")
        )

    def assign(self, subject_id: int, override: QuarterOverride) -> SubjectSnapshot:
        """Persist a manual quarter or unassigned decision for an existing subject."""
        snapshot = self._assignable_snapshot(subject_id)
        assignments = load_quarter_overrides(self.overrides_path)
        assignments[subject_id] = override
        updated = replace(
            snapshot,
            premiere=(
                None
                if override.quarter is None
                else _manual_premiere(override)
            ),
            review_issues=_remaining_non_quarter_issues(snapshot.review_issues),
        )
        self._persist(subject_id, assignments, updated)
        return updated

    def clear(self, subject_id: int) -> SubjectSnapshot:
        """Remove a manual decision and deterministically re-run local assignment."""
        snapshot = self._assignable_snapshot(subject_id)
        assignments = load_quarter_overrides(self.overrides_path)
        previous = assignments.pop(subject_id, None)
        target = _automatic_target(snapshot, previous)
        japanese_overrides = load_japanese_overrides(self.japanese_overrides_path)
        decision = admit_subject(
            _stored_candidate(snapshot),
            _stored_detail(snapshot),
            target,
            excluded_subject_ids=self.excluded_subject_ids,
            allow_out_of_scope=True,
            japanese_override=(
                japanese_overrides[subject_id].decision
                if subject_id in japanese_overrides
                else None
            ),
        )
        if decision.status is AdmissionStatus.REJECTED:
            raise AssignmentError("stored subject is no longer admissible")
        reviews = _remaining_non_quarter_issues(snapshot.review_issues)
        reviews += tuple(_review_issue(finding) for finding in decision.reviews)
        updated = replace(snapshot, premiere=decision.premiere, review_issues=reviews)
        self._persist(subject_id, assignments, updated)
        return updated

    def _assignable_snapshot(self, subject_id: int) -> SubjectSnapshot:
        if subject_id in self.excluded_subject_ids:
            raise AssignmentError("blacklisted subjects cannot be assigned")
        snapshot = self.repository.get_subject_facts(subject_id)
        if snapshot is None:
            raise AssignmentError(
                "subject is not stored; sync or single-subject import is required"
            )
        if (
            snapshot.subject.japanese.classification
            is not JapaneseClassification.ACCEPTED_JAPANESE
        ):
            raise AssignmentError(
                "manual assignment cannot override Japanese-only admission"
            )
        return snapshot

    def _persist(
        self,
        subject_id: int,
        assignments: dict[int, QuarterOverride],
        snapshot: SubjectSnapshot,
    ) -> None:
        previous = load_quarter_overrides(self.overrides_path)
        save_quarter_overrides(self.overrides_path, assignments)
        try:
            with self.repository.transaction() as connection:
                self.repository.replace_subject_snapshot(connection, snapshot)
        except BaseException:
            save_quarter_overrides(self.overrides_path, previous)
            raise


def render_review(repository: SubjectRepository, quarter: Quarter | None = None) -> str:
    """Render a compact, non-interactive actionable REVIEW list."""
    rows = repository.list_review_issues(quarter)
    lines = ["Bangumi Side B — REVIEW", "", f"{len(rows)} unresolved subjects"]
    for index, item in enumerate(rows, start=1):
        subject = item.subject
        issue = item.issue
        title = subject.name_cn or subject.name_original
        lines.extend(
            (
                "",
                f"[R{index:03d}]",
                f"BGM ID       {subject.subject_id}",
                f"标题         {title}",
                f"形式         {subject.media_format.value}",
                (
                    "首播         "
                    f"{subject.air_date.isoformat() if subject.air_date else '-'}"
                ),
                f"Issue        {issue.issue_code}",
            )
        )
        if issue.issue_code in _JAPANESE_ISSUES:
            lines.extend(
                (
                    f"处理：bgmb classify {subject.subject_id} --japanese",
                    f"       bgmb classify {subject.subject_id} --non-japanese",
                )
            )
        elif issue.candidate_quarter is not None:
            lines.append(
                "处理："
                f"bgmb assign {subject.subject_id} "
                f"{issue.candidate_quarter.year} {issue.candidate_quarter.month}"
            )
    return "\n".join(lines)


_JAPANESE_ISSUES = frozenset(
    {
        JAPANESE_CLASSIFICATION_UNRESOLVED,
        "JAPANESE_REGION_CONFLICT",
        "JAPANESE_EVIDENCE_CONFLICT",
    }
)


class JapaneseAdjudicator:
    """Apply explicit Japanese-scope decisions without changing quarter rules."""

    def __init__(
        self,
        repository: SubjectRepository,
        overrides_path: Path,
        excluded_subject_ids: frozenset[int],
        workspace_directory: Path,
    ) -> None:
        self.repository = repository
        self.overrides_path = overrides_path
        self.excluded_subject_ids = excluded_subject_ids
        self.workspace_directory = workspace_directory

    def set_classification(
        self, subject_id: int, classification: JapaneseClassification
    ) -> SubjectSnapshot | None:
        """Persist a manual decision and update or remove local facts."""
        snapshot = self._assignable_snapshot(subject_id)
        overrides = load_japanese_overrides(self.overrides_path)
        previous = overrides.get(subject_id)
        if previous is None:
            override = JapaneseOverride.from_decision(
                subject_id, classification, snapshot.subject.japanese
            )
        else:
            override = replace(previous, classification=classification)
        overrides[subject_id] = override
        if classification is JapaneseClassification.REJECTED_NON_JAPANESE:
            self._delete_subject(subject_id, snapshot, overrides)
            return None
        updated = replace(
            snapshot,
            subject=replace(snapshot.subject, japanese=override.decision),
            review_issues=_remaining_non_japanese_issues(snapshot.review_issues),
        )
        self._persist(overrides, updated)
        return updated

    def clear(self, subject_id: int) -> SubjectSnapshot | None:
        """Remove a manual decision and restore its saved automatic result."""
        overrides = load_japanese_overrides(self.overrides_path)
        previous = overrides.pop(subject_id, None)
        if previous is None:
            raise AssignmentError("subject has no manual Japanese decision")
        snapshot = self.repository.get_subject_facts(subject_id)
        if snapshot is None:
            save_japanese_overrides(self.overrides_path, overrides)
            return None
        automatic = previous.automatic_decision
        if automatic.classification is JapaneseClassification.REJECTED_NON_JAPANESE:
            self._delete_subject(subject_id, snapshot, overrides)
            return None
        updated = replace(
            snapshot,
            subject=replace(snapshot.subject, japanese=automatic),
            review_issues=(
                _remaining_non_japanese_issues(snapshot.review_issues)
                + (
                    (_japanese_review_issue(snapshot, automatic),)
                    if automatic.classification is JapaneseClassification.UNRESOLVED
                    else ()
                )
            ),
        )
        self._persist(overrides, updated)
        return updated

    def _assignable_snapshot(self, subject_id: int) -> SubjectSnapshot:
        if subject_id in self.excluded_subject_ids:
            raise AssignmentError("blacklisted subjects cannot be classified")
        snapshot = self.repository.get_subject_facts(subject_id)
        if snapshot is None:
            raise AssignmentError(
                "subject is not stored; sync or single-subject import is required"
            )
        return snapshot

    def _persist(
        self,
        overrides: dict[int, JapaneseOverride],
        snapshot: SubjectSnapshot,
    ) -> None:
        previous = load_japanese_overrides(self.overrides_path)
        save_japanese_overrides(self.overrides_path, overrides)
        try:
            with self.repository.transaction() as connection:
                self.repository.replace_subject_snapshot(connection, snapshot)
        except BaseException:
            save_japanese_overrides(self.overrides_path, previous)
            raise

    def _delete_subject(
        self,
        subject_id: int,
        snapshot: SubjectSnapshot,
        overrides: dict[int, JapaneseOverride],
    ) -> None:
        affected = self.repository.affected_quarters(frozenset({subject_id}))
        prior_states = {
            quarter: self.repository.get_sync_state(quarter) for quarter in affected
        }
        covers = CoverStore(self.workspace_directory / "covers", None)
        try:
            cover_batch = covers.quarantine_subject_covers({subject_id})
        except OSError as error:
            raise AssignmentError("Japanese-scope cover cleanup failed") from error
        previous = load_japanese_overrides(self.overrides_path)
        try:
            save_japanese_overrides(self.overrides_path, overrides)
            with self.repository.transaction() as connection:
                self.repository.delete_subjects(connection, frozenset({subject_id}))
                for quarter in affected:
                    prior = prior_states[quarter]
                    if prior is not None:
                        self.repository.write_sync_state(
                            connection,
                            replace(
                                prior,
                                facts_status="incomplete",
                                last_attempt_at=datetime.now(UTC)
                                .isoformat()
                                .replace("+00:00", "Z"),
                            ),
                        )
        except BaseException:
            try:
                save_japanese_overrides(self.overrides_path, previous)
                cover_batch.restore()
            except OSError as error:
                raise AssignmentError(
                    "Japanese-scope change failed and recovery is incomplete"
                ) from error
            raise
        try:
            cover_batch.finalize()
        except OSError as error:
            raise AssignmentError(
                "Japanese-scope cover cleanup finalization failed"
            ) from error


def _automatic_target(
    snapshot: SubjectSnapshot, previous: QuarterOverride | None
) -> Quarter:
    if previous is not None and previous.quarter is not None:
        return previous.quarter
    if snapshot.premiere is not None:
        return snapshot.premiere.quarter
    if snapshot.subject.air_date is not None:
        return quarter_for_date(snapshot.subject.air_date)
    raise AssignmentError(
        "cannot re-run automatic assignment without a date or quarter"
    )


def _stored_candidate(snapshot: SubjectSnapshot) -> DiscoveredSubject:
    subject = snapshot.subject
    dates = frozenset(() if subject.air_date is None else (subject.air_date,))
    return DiscoveredSubject(
        subject.subject_id,
        frozenset({subject.media_format}),
        dates,
        frozenset({2}),
        ("manual:clear",),
    )


def _stored_detail(snapshot: SubjectSnapshot) -> SubjectDetail:
    subject = snapshot.subject
    return SubjectDetail(
        subject.subject_id,
        2,
        subject.name_original,
        subject.name_cn,
        subject.summary_raw,
        subject.air_date,
        "TV" if subject.media_format.value == "TV" else "剧场版",
        subject.episode_count,
        None,
        subject.rating_score,
        subject.rating_count,
        (),
        tuple(ApiTag(tag, None) for tag in snapshot.tags),
        tuple(ApiInfoboxItem(item.item_key, item.value) for item in snapshot.infobox),
        ImageUrls(),
    )


def _manual_premiere(override: QuarterOverride):
    assert override.quarter is not None
    from bgm_side_b.domain import QuarterAppearanceKind, QuarterAssignmentSource
    from bgm_side_b.repository import QuarterAppearance

    return QuarterAppearance(
        override.quarter,
        QuarterAppearanceKind.PREMIERE,
        QuarterAssignmentSource.MANUAL,
        "manual_override",
        override.reason or "quarter_override",
    )


def _remaining_non_quarter_issues(
    issues: tuple[ReviewIssue, ...]
) -> tuple[ReviewIssue, ...]:
    return tuple(issue for issue in issues if issue.issue_code not in _QUARTER_ISSUES)


def _remaining_non_japanese_issues(
    issues: tuple[ReviewIssue, ...]
) -> tuple[ReviewIssue, ...]:
    return tuple(issue for issue in issues if issue.issue_code not in _JAPANESE_ISSUES)


def _japanese_review_issue(
    snapshot: SubjectSnapshot, decision: JapaneseDecision
) -> ReviewIssue:
    if decision.evidence_type == "unresolved_japanese_region_conflict":
        issue_code = "JAPANESE_REGION_CONFLICT"
    elif decision.evidence_type == "unresolved_japanese_evidence_conflict":
        issue_code = "JAPANESE_EVIDENCE_CONFLICT"
    else:
        issue_code = JAPANESE_CLASSIFICATION_UNRESOLVED
    return ReviewIssue(
        issue_code,
        None,
        snapshot.subject.air_date.isoformat() if snapshot.subject.air_date else None,
        {
            "evidence_type": decision.evidence_type,
            "evidence_value": decision.evidence_value,
            "subject_id": snapshot.subject.subject_id,
        },
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _review_issue(finding: ReviewFinding) -> ReviewIssue:
    return ReviewIssue(
        finding.issue_code,
        finding.candidate_quarter,
        finding.observed_value,
        finding.details,
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
