"""Deterministic archive admission and quarter adjudication rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from bgm_side_b.api import SubjectDetail
from bgm_side_b.discovery import TV_BOUNDARY_LOOKBACK_DAYS, DiscoveredSubject
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAssignmentSource,
)
from bgm_side_b.repository import QuarterOwnership
from bgm_side_b.rules import classify_japanese, normalize_text

ANIME_SUBJECT_TYPE: Final = 2
TV_QUARTER_BOUNDARY: Final = "TV_QUARTER_BOUNDARY"
TV_QUARTER_DATE_UNRESOLVED: Final = "TV_QUARTER_DATE_UNRESOLVED"
MOVIE_DATE_UNRESOLVED: Final = "MOVIE_DATE_UNRESOLVED"
DISCOVERY_DATE_MISMATCH: Final = "DISCOVERY_DATE_MISMATCH"
DISCOVERY_MEDIA_CONFLICT: Final = "DISCOVERY_MEDIA_CONFLICT"
JAPANESE_CLASSIFICATION_UNRESOLVED: Final = "JAPANESE_CLASSIFICATION_UNRESOLVED"
SEARCH_ONLY_MEDIA_UNRESOLVED: Final = "SEARCH_ONLY_MEDIA_UNRESOLVED"


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
    quarter: QuarterOwnership | None = None
    reviews: tuple[ReviewFinding, ...] = ()
    reason: str | None = None


def admit_subject(
    candidate: DiscoveredSubject,
    detail: SubjectDetail,
    target_quarter: Quarter,
    *,
    excluded_subject_ids: frozenset[int] = frozenset(),
    override: QuarterOverride | None = None,
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

    media_format, media_review = _resolve_media(candidate, detail)
    if media_review is not None:
        return AdmissionDecision(
            AdmissionStatus.REVIEW,
            detail.subject_id,
            reviews=(media_review,),
            reason=media_review.issue_code,
        )
    assert media_format is not None

    japanese = classify_japanese(_country_values(detail))
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
            JAPANESE_CLASSIFICATION_UNRESOLVED,
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
            reviews=(review,),
            reason=review.issue_code,
        )

    if override is not None:
        ownership = (
            None
            if override.quarter is None
            else QuarterOwnership(
                override.quarter,
                QuarterAssignmentSource.MANUAL,
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

    quarter_review = _resolve_quarter(candidate, detail, target_quarter, media_format)
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


def _resolve_media(
    candidate: DiscoveredSubject, detail: SubjectDetail
) -> tuple[MediaFormat | None, ReviewFinding | None]:
    detail_format = _platform_media_format(detail.platform)
    if candidate.has_media_conflict:
        return None, _media_review(candidate, detail, "conflicting_browse_categories")
    candidate_format = candidate.candidate_media_format
    if (
        candidate_format is not None
        and detail_format is not None
        and candidate_format is not detail_format
    ):
        return None, _media_review(candidate, detail, "browse_platform_conflict")
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
) -> QuarterOwnership | ReviewFinding:
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
        return QuarterOwnership(
            natural_quarter, QuarterAssignmentSource.AUTOMATIC, "air_date"
        )

    if natural_quarter == target_quarter:
        return QuarterOwnership(
            target_quarter, QuarterAssignmentSource.AUTOMATIC, "air_date"
        )
    target_start = date(target_quarter.year, target_quarter.month, 1)
    days_before_target = (target_start - detail.air_date).days
    if 1 <= days_before_target <= TV_BOUNDARY_LOOKBACK_DAYS:
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
