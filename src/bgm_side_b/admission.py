"""Deterministic archive admission and quarter adjudication rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from bgm_side_b.api import ApiTag, SubjectDetail
from bgm_side_b.archive_config import should_auto_blacklist
from bgm_side_b.discovery import TV_BOUNDARY_LOOKBACK_DAYS, DiscoveredSubject
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
)
from bgm_side_b.repository import QuarterAppearance
from bgm_side_b.rules import classify_japanese_with_public_regions, normalize_text

ANIME_SUBJECT_TYPE: Final = 2
TV_QUARTER_BOUNDARY: Final = "TV_QUARTER_BOUNDARY"
TV_QUARTER_DATE_UNRESOLVED: Final = "TV_QUARTER_DATE_UNRESOLVED"
MOVIE_DATE_UNRESOLVED: Final = "MOVIE_DATE_UNRESOLVED"
DISCOVERY_DATE_MISMATCH: Final = "DISCOVERY_DATE_MISMATCH"
DISCOVERY_MEDIA_CONFLICT: Final = "DISCOVERY_MEDIA_CONFLICT"
OUT_OF_SCOPE_QUARTER: Final = "out_of_scope_quarter"
OUTCOME_DOMINATED_LOW_RATING: Final = "outcome_dominated_low_rating"
JAPANESE_CLASSIFICATION_UNRESOLVED: Final = "JAPANESE_CLASSIFICATION_UNRESOLVED"
SEARCH_ONLY_MEDIA_UNRESOLVED: Final = "SEARCH_ONLY_MEDIA_UNRESOLVED"
_UNSUPPORTED_MEDIA_PLATFORMS: Final = frozenset({"WEB", "OVA", "OAD"})

# Only evidence-missing REVIEWs may participate in the immediate cold cleanup
# rule. Conflict and classification REVIEWs remain human-only.
UNRESOLVED_COLD_REVIEW_ISSUES: Final = frozenset(
    {
        TV_QUARTER_DATE_UNRESOLVED,
        MOVIE_DATE_UNRESOLVED,
        SEARCH_ONLY_MEDIA_UNRESOLVED,
    }
)
CONFLICT_REVIEW_ISSUES: Final = frozenset(
    {
        DISCOVERY_DATE_MISMATCH,
        DISCOVERY_MEDIA_CONFLICT,
        "JAPANESE_REGION_CONFLICT",
        "JAPANESE_EVIDENCE_CONFLICT",
    }
)


def is_unresolved_cold_review(issue_code: str) -> bool:
    """Return whether an issue is explicitly allowlisted for cold cleanup."""
    return issue_code in UNRESOLVED_COLD_REVIEW_ISSUES


def is_conflict_review(issue_code: str) -> bool:
    """Return whether an issue records conflicting structured evidence."""
    return issue_code in CONFLICT_REVIEW_ISSUES


def should_auto_blacklist_unresolved_cold(issue_code: str) -> bool:
    """Apply the immediate information-insufficiency rule."""
    return is_unresolved_cold_review(issue_code)


class AdmissionStatus(StrEnum):
    """The only terminal outcomes of deterministic candidate admission."""

    BLACKLISTED = "blacklisted"
    REJECTED = "rejected"
    REVIEW = "review"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class QuarterOverride:
    """A human decision that may only override archive-quarter ownership."""

    quarter: Quarter | None
    reason: str | None = None


@dataclass(frozen=True)
class TVBoundaryEvidence:
    """Bounded deterministic evidence for a TV next-quarter exception."""

    planned_episode_count: int | None = None
    main_episode_airdates: tuple[date, ...] | None = None
    episode_count_conflict: bool = False


@dataclass(frozen=True)
class ReviewFinding:
    """One actionable deterministic ambiguity before it is persisted."""

    issue_code: str
    candidate_quarter: Quarter | None
    observed_value: str | None
    details: dict[str, object]


@dataclass(frozen=True)
class AdmissionDecision:
    """A candidate's terminal scope decision without any SQLite side effect."""

    status: AdmissionStatus
    subject_id: int
    media_format: MediaFormat | None = None
    japanese: JapaneseDecision | None = None
    premiere: QuarterAppearance | None = None
    reviews: tuple[ReviewFinding, ...] = ()
    reason: str | None = None
    outcome_dominated: bool = False


def admit_subject(
    candidate: DiscoveredSubject,
    detail: SubjectDetail,
    target_quarter: Quarter,
    *,
    excluded_subject_ids: frozenset[int] = frozenset(),
    override: QuarterOverride | None = None,
    boundary_evidence: TVBoundaryEvidence | None = None,
    japanese_override: JapaneseDecision | None = None,
    evaluation_date: date | None = None,
    allow_out_of_scope: bool = False,
) -> AdmissionDecision:
    """Apply blacklist, scope, Japanese-only, and quarter rules in fixed order."""
    if candidate.subject_id != detail.subject_id:
        raise ValueError("candidate and detail subject IDs must match")
    if detail.subject_id in excluded_subject_ids:
        return AdmissionDecision(
            AdmissionStatus.BLACKLISTED, detail.subject_id, reason="blacklist"
        )
    if detail.subject_type != ANIME_SUBJECT_TYPE or any(
        subject_type != ANIME_SUBJECT_TYPE for subject_type in candidate.subject_types
    ):
        return AdmissionDecision(
            AdmissionStatus.REJECTED, detail.subject_id, reason="not_anime"
        )
    if (
        detail.platform is not None
        and normalize_text(detail.platform) in _UNSUPPORTED_MEDIA_PLATFORMS
    ):
        return AdmissionDecision(
            AdmissionStatus.REJECTED,
            detail.subject_id,
            reason="unsupported_media",
        )

    media_format, media_review = _resolve_media(candidate, detail)
    if media_format is None:
        assert media_review is not None
        return AdmissionDecision(
            AdmissionStatus.REVIEW,
            detail.subject_id,
            reviews=(media_review,),
            reason=media_review.issue_code,
        )
    assert media_format is not None

    date_conflict = candidate.has_date_conflict or (
        detail.air_date is not None
        and any(observed != detail.air_date for observed in candidate.candidate_dates)
    )
    if (
        override is None
        and not allow_out_of_scope
        and not date_conflict
        and _quarter_relevance(detail, target_quarter, media_format) is False
    ):
        return AdmissionDecision(
            AdmissionStatus.REJECTED,
            detail.subject_id,
            media_format,
            reason=OUT_OF_SCOPE_QUARTER,
        )

    japanese = japanese_override or classify_japanese_with_public_regions(
        detail.meta_tags, _country_values(detail)
    )
    if japanese.classification is JapaneseClassification.REJECTED_NON_JAPANESE:
        return AdmissionDecision(
            AdmissionStatus.REJECTED,
            detail.subject_id,
            media_format,
            japanese,
            reason="non_japanese",
        )
    if japanese.classification is JapaneseClassification.UNRESOLVED:
        review = ReviewFinding(
            _japanese_review_code(japanese),
            None,
            detail.air_date.isoformat() if detail.air_date else None,
            {
                "evidence_type": japanese.evidence_type,
                "evidence_value": japanese.evidence_value,
                "provenance": list(candidate.provenance),
            },
        )
        return AdmissionDecision(
            AdmissionStatus.REVIEW,
            detail.subject_id,
            media_format,
            japanese,
            reviews=tuple(item for item in (media_review, review) if item is not None),
            reason=review.issue_code,
            outcome_dominated=(
                evaluation_date is not None
                and should_auto_blacklist(
                    detail.air_date, detail.rating_total, evaluation_date
                )
            ),
        )
    if media_review is not None:
        return AdmissionDecision(
            AdmissionStatus.REVIEW,
            detail.subject_id,
            media_format,
            japanese,
            reviews=(media_review,),
            reason=media_review.issue_code,
        )

    if override is not None:
        ownership = (
            None
            if override.quarter is None
            else QuarterAppearance(
                override.quarter,
                QuarterAppearanceKind.PREMIERE,
                QuarterAssignmentSource.MANUAL,
                "manual_override",
                override.reason or "quarter_override",
            )
        )
        return AdmissionDecision(
            AdmissionStatus.ACCEPTED,
            detail.subject_id,
            media_format,
            japanese,
            ownership,
        )

    quarter_review = _resolve_quarter(
        candidate,
        detail,
        target_quarter,
        media_format,
        boundary_evidence=boundary_evidence,
    )
    if isinstance(quarter_review, ReviewFinding):
        return AdmissionDecision(
            AdmissionStatus.REVIEW,
            detail.subject_id,
            media_format,
            japanese,
            reviews=(quarter_review,),
            reason=quarter_review.issue_code,
        )
    return AdmissionDecision(
        AdmissionStatus.ACCEPTED,
        detail.subject_id,
        media_format,
        japanese,
        quarter_review,
    )


def quarter_for_date(value: date) -> Quarter:
    """Return the natural calendar quarter containing a reliable date."""
    return Quarter(value.year, ((value.month - 1) // 3) * 3 + 1)


def _quarter_relevance(
    detail: SubjectDetail, target_quarter: Quarter, media_format: MediaFormat
) -> bool | None:
    """Return known relevance without making a quarter ownership decision."""
    if detail.air_date is None:
        return None
    natural_quarter = quarter_for_date(detail.air_date)
    if natural_quarter == target_quarter:
        return True
    if media_format is MediaFormat.MOVIE:
        return False
    target_start = date(target_quarter.year, target_quarter.month, 1)
    days_before_target = (target_start - detail.air_date).days
    return 1 <= days_before_target <= TV_BOUNDARY_LOOKBACK_DAYS


def _resolve_media(
    candidate: DiscoveredSubject, detail: SubjectDetail
) -> tuple[MediaFormat | None, ReviewFinding | None]:
    detail_format = _platform_media_format(detail.platform)
    if candidate.has_media_conflict:
        if detail_format is None:
            return None, _media_review(
                candidate, detail, "conflicting_browse_categories"
            )
        return detail_format, _media_review(
            candidate, detail, "conflicting_browse_categories"
        )
    candidate_format = candidate.candidate_media_format
    if (
        candidate_format is not None
        and detail_format is not None
        and candidate_format is not detail_format
    ):
        return detail_format, _media_review(
            candidate, detail, "browse_platform_conflict"
        )
    if candidate_format is not None:
        return candidate_format, None
    if detail_format is not None:
        return detail_format, None
    return None, ReviewFinding(
        SEARCH_ONLY_MEDIA_UNRESOLVED,
        None,
        detail.platform,
        {"provenance": list(candidate.provenance)},
    )


def _resolve_quarter(
    candidate: DiscoveredSubject,
    detail: SubjectDetail,
    target_quarter: Quarter,
    media_format: MediaFormat,
    *,
    boundary_evidence: TVBoundaryEvidence | None = None,
) -> QuarterAppearance | ReviewFinding:
    if detail.air_date is None:
        issue_code = (
            TV_QUARTER_DATE_UNRESOLVED
            if media_format is MediaFormat.TV
            else MOVIE_DATE_UNRESOLVED
        )
        return ReviewFinding(
            issue_code,
            target_quarter,
            None,
            {"provenance": list(candidate.provenance)},
        )
    if candidate.has_date_conflict or any(
        observed != detail.air_date for observed in candidate.candidate_dates
    ):
        return ReviewFinding(
            DISCOVERY_DATE_MISMATCH,
            target_quarter,
            detail.air_date.isoformat(),
            {
                "discovery_dates": [
                    observed.isoformat()
                    for observed in sorted(candidate.candidate_dates)
                ],
                "provenance": list(candidate.provenance),
            },
        )

    natural_quarter = quarter_for_date(detail.air_date)
    if media_format is MediaFormat.MOVIE:
        if natural_quarter != target_quarter:
            return _date_mismatch(candidate, detail, target_quarter, natural_quarter)
        return QuarterAppearance(
            natural_quarter,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            detail.air_date.isoformat(),
        )

    if natural_quarter == target_quarter:
        return QuarterAppearance(
            target_quarter,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            detail.air_date.isoformat(),
        )
    target_start = date(target_quarter.year, target_quarter.month, 1)
    days_before_target = (target_start - detail.air_date).days
    if 1 <= days_before_target <= TV_BOUNDARY_LOOKBACK_DAYS:
        if _is_short_tv_run(detail, boundary_evidence):
            return _natural_tv_premiere(detail.air_date, natural_quarter)
        consensus = _early_premiere_consensus(detail.tags, target_quarter)
        run = _continuing_boundary_run(
            detail,
            target_quarter,
            boundary_evidence,
        )
        if consensus is not None and run is not None:
            return QuarterAppearance(
                target_quarter,
                QuarterAppearanceKind.PREMIERE,
                QuarterAssignmentSource.AUTOMATIC,
                "community_quarter_tag_and_main_episode_airdates",
                (
                    f"{consensus};run={run[0].isoformat()}"
                    f"..{run[-1].isoformat()}"
                ),
            )
        return ReviewFinding(
            TV_QUARTER_BOUNDARY,
            target_quarter,
            detail.air_date.isoformat(),
            {
                "natural_quarter": _quarter_label(natural_quarter),
                "days_before_target": days_before_target,
                "provenance": list(candidate.provenance),
            },
        )
    return _date_mismatch(candidate, detail, target_quarter, natural_quarter)


def _natural_tv_premiere(
    air_date: date, natural_quarter: Quarter
) -> QuarterAppearance:
    return QuarterAppearance(
        natural_quarter,
        QuarterAppearanceKind.PREMIERE,
        QuarterAssignmentSource.AUTOMATIC,
        "air_date",
        air_date.isoformat(),
    )


def _is_short_tv_run(
    detail: SubjectDetail, evidence: TVBoundaryEvidence | None
) -> bool:
    if evidence is not None and evidence.episode_count_conflict:
        return False
    structured_count = _structured_episode_count(detail)
    observed_count = None if evidence is None else evidence.planned_episode_count
    if structured_count is not None and observed_count is not None:
        return structured_count == observed_count and structured_count in {1, 2}
    return (structured_count or observed_count) in {1, 2}


def _structured_episode_count(detail: SubjectDetail) -> int | None:
    values = []
    if isinstance(detail.eps, int) and not isinstance(detail.eps, bool):
        if detail.eps > 0:
            values.append(detail.eps)
    for item in detail.infobox:
        if item.key != "话数":
            continue
        value = item.value.strip() if isinstance(item.value, str) else item.value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            values.append(value)
        elif isinstance(value, str) and value.isdecimal() and int(value) > 0:
            values.append(int(value))
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


def _continuing_boundary_run(
    detail: SubjectDetail,
    target_quarter: Quarter,
    evidence: TVBoundaryEvidence | None,
) -> tuple[date, ...] | None:
    """Return a direct multi-week run crossing into the target quarter."""
    if evidence is None or evidence.episode_count_conflict:
        return None
    if evidence.planned_episode_count in {1, 2}:
        return None
    if evidence.main_episode_airdates is None:
        return None
    target_start = date(target_quarter.year, target_quarter.month, 1)
    end_date = _structured_end_date(detail)
    if end_date is not None and end_date < target_start:
        return None
    dates = tuple(sorted(set(evidence.main_episode_airdates)))
    if len(dates) < 3:
        return None
    for index in range(len(dates) - 2):
        window = dates[index : index + 3]
        if window[0] >= target_start:
            continue
        if not any(quarter_for_date(item) == target_quarter for item in window):
            continue
        gaps = tuple(
            (right - left).days for left, right in zip(window, window[1:])
        )
        if all(5 <= gap <= 14 for gap in gaps):
            return window
    return None


def _structured_end_date(detail: SubjectDetail) -> date | None:
    for item in detail.infobox:
        if item.key != "播放结束" or not isinstance(item.value, str):
            continue
        try:
            return date.fromisoformat(item.value)
        except ValueError:
            return None
    return None


def _media_review(
    candidate: DiscoveredSubject, detail: SubjectDetail, reason: str
) -> ReviewFinding:
    return ReviewFinding(
        DISCOVERY_MEDIA_CONFLICT,
        None,
        detail.platform,
        {
            "reason": reason,
            "browse_formats": sorted(item.value for item in candidate.media_formats),
            "provenance": list(candidate.provenance),
        },
    )


def _date_mismatch(
    candidate: DiscoveredSubject,
    detail: SubjectDetail,
    target_quarter: Quarter,
    natural_quarter: Quarter,
) -> ReviewFinding:
    assert detail.air_date is not None
    return ReviewFinding(
        DISCOVERY_DATE_MISMATCH,
        target_quarter,
        detail.air_date.isoformat(),
        {
            "natural_quarter": _quarter_label(natural_quarter),
            "provenance": list(candidate.provenance),
        },
    )


def _country_values(detail: SubjectDetail) -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.key, normalize_text(item.value))
        for item in detail.infobox
        if isinstance(item.value, str) and normalize_text(item.value)
    )


def _platform_media_format(value: str | None) -> MediaFormat | None:
    platform = normalize_text(value) if value is not None else ""
    if platform == "TV":
        return MediaFormat.TV
    if platform in {"剧场版", "Movie"}:
        return MediaFormat.MOVIE
    return None


def _quarter_label(quarter: Quarter) -> str:
    return f"{quarter.year:04d}-{quarter.month:02d}"


def _japanese_review_code(japanese: JapaneseDecision) -> str:
    if japanese.evidence_type == "unresolved_japanese_region_conflict":
        return "JAPANESE_REGION_CONFLICT"
    if japanese.evidence_type == "unresolved_japanese_evidence_conflict":
        return "JAPANESE_EVIDENCE_CONFLICT"
    return JAPANESE_CLASSIFICATION_UNRESOLVED


def _early_premiere_consensus(
    tags: tuple[ApiTag, ...], target_quarter: Quarter
) -> str | None:
    target_name = f"{target_quarter.year}年{target_quarter.month}月"
    canonical = {
        tag.name: tag.count
        for tag in tags
        if tag.count is not None and _canonical_quarter_tag(tag.name)
    }
    target_count = canonical.get(target_name)
    if target_count is None or target_count < 10:
        return None
    max_count = max((tag.count or 0 for tag in tags), default=0)
    if target_count < 0.25 * max_count:
        return None
    strongest_other = max(
        (count for name, count in canonical.items() if name != target_name), default=0
    )
    if strongest_other and target_count < 4 * strongest_other:
        return None
    return f"{target_name}:{target_count}"


def _canonical_quarter_tag(value: str) -> bool:
    if not value.endswith("月") or "年" not in value:
        return False
    year, month = value[:-1].split("年", maxsplit=1)
    return len(year) == 4 and year.isdecimal() and month in {"1", "4", "7", "10"}
