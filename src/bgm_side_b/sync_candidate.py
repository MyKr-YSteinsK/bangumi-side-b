"""Gather bounded canonical evidence and classify a discovered sync candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bgm_side_b.admission import (
    OUT_OF_SCOPE_QUARTER,
    OUTCOME_DOMINATED_LOW_RATING,
    SEARCH_ONLY_MEDIA_UNRESOLVED,
    AdmissionDecision,
    AdmissionStatus,
    QuarterOverride,
    TVBoundaryEvidence,
    admit_subject,
    is_conflict_review,
    is_unresolved_cold_review,
    should_auto_blacklist_unresolved_cold,
)
from bgm_side_b.api import BangumiApiClient, BangumiApiError, SubjectDetail
from bgm_side_b.archive_config import should_auto_blacklist
from bgm_side_b.discovery import TV_BOUNDARY_LOOKBACK_DAYS, DiscoveredSubject
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
)
from bgm_side_b.rules import normalize_text


@dataclass(frozen=True)
class EpisodeCountResolution:
    value: int | None
    source: str
    warning: str | None = None


@dataclass(frozen=True)
class CandidateEvaluation:
    detail: SubjectDetail
    decision: AdmissionDecision
    episode_count: EpisodeCountResolution | None
    auto_exclusion: dict[str, object] | None


def evaluate_candidate(
    api: BangumiApiClient,
    candidate: DiscoveredSubject,
    quarter: Quarter,
    *,
    excluded_subject_ids: frozenset[int],
    override: QuarterOverride | None,
    japanese_override: JapaneseDecision | None,
    evaluation_date: date,
    low_rating_exempt_subject_ids: frozenset[int],
    warnings: list[dict[str, str]],
) -> CandidateEvaluation:
    """Perform reads only. Reuse a boundary probe if the candidate needs a snapshot."""
    subject_id = candidate.subject_id
    detail = api.get_subject(subject_id)
    episodes = None
    boundary = None
    if _needs_tv_boundary_evidence(candidate, detail, quarter):
        episodes = resolve_episode_count_with_fallback(
            api, detail, subject_id, warnings
        )
        dates = None
        if episodes.warning is None and episodes.value not in {1, 2}:
            dates = _read_boundary_episode_airdates(api, subject_id, warnings)
        boundary = TVBoundaryEvidence(
            episodes.value, dates, episodes.warning is not None
        )
    decision = admit_subject(
        candidate,
        detail,
        quarter,
        excluded_subject_ids=excluded_subject_ids,
        override=override,
        japanese_override=japanese_override,
        boundary_evidence=boundary,
        evaluation_date=evaluation_date,
        low_rating_exempt_subject_ids=low_rating_exempt_subject_ids,
    )
    event = _automatic_exclusion(
        detail, decision, quarter, evaluation_date, low_rating_exempt_subject_ids
    )
    return CandidateEvaluation(detail, decision, episodes, event)


def _automatic_exclusion(
    detail: SubjectDetail,
    decision: AdmissionDecision,
    quarter: Quarter,
    evaluation_date: date,
    low_rating_exempt_subject_ids: frozenset[int],
) -> dict[str, object] | None:
    """Return the report evidence for a proposed exclusion, in rule priority order."""
    if (
        decision.reason == OUT_OF_SCOPE_QUARTER
        or decision.status is AdmissionStatus.BLACKLISTED
    ):
        return None
    if decision.outcome_dominated:
        return auto_blacklist_event(
            detail,
            evaluation_date,
            reason=OUTCOME_DOMINATED_LOW_RATING,
            issue_code=_japanese_issue_code(decision),
            target_quarter=quarter,
            japanese=decision.japanese,
        )
    if _eligible_for_auto_blacklist(decision) and should_auto_blacklist(
        detail.air_date,
        detail.rating_total,
        evaluation_date,
        subject_id=detail.subject_id,
        low_rating_exempt_subject_ids=low_rating_exempt_subject_ids,
    ):
        return auto_blacklist_event(detail, evaluation_date)
    missing = _persisted_unresolved_cold_review(
        decision
    ) or _external_unresolved_cold_review(decision, quarter)
    if missing is None or not should_auto_blacklist_unresolved_cold(missing[0]):
        return None
    issue, target = missing
    return auto_blacklist_event(
        detail,
        evaluation_date,
        reason="insufficient_airing_information",
        issue_code=issue,
        target_quarter=target,
    )


def _eligible_for_auto_blacklist(decision: AdmissionDecision) -> bool:
    return (
        decision.media_format in {MediaFormat.TV, MediaFormat.MOVIE}
        and decision.japanese is not None
        and decision.japanese.classification is JapaneseClassification.ACCEPTED_JAPANESE
        and all(not is_conflict_review(issue.issue_code) for issue in decision.reviews)
    )


def _persisted_unresolved_cold_review(
    decision: AdmissionDecision,
) -> tuple[str, Quarter | None] | None:
    """Return an explicit information-insufficiency issue for cleanup."""
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
        not is_unresolved_cold_review(issue.issue_code) for issue in decision.reviews
    ):
        return None
    target_quarters = {issue.candidate_quarter for issue in decision.reviews}
    target_quarter = next(iter(target_quarters)) if len(target_quarters) == 1 else None
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
        issue.issue_code != SEARCH_ONLY_MEDIA_UNRESOLVED
        or issue.candidate_quarter is not None
        for issue in decision.reviews
    ):
        return None
    return decision.reviews[0].issue_code, target_quarter


def auto_blacklist_event(
    detail: SubjectDetail,
    evaluation_date: date,
    *,
    reason: str = "low_rating_count",
    issue_code: str | None = None,
    target_quarter: Quarter | None = None,
    japanese: JapaneseDecision | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "subject_id": detail.subject_id,
        "title": detail.name_cn or detail.name or str(detail.subject_id),
        "evaluation_date": evaluation_date.isoformat(),
        "reason": reason,
    }
    if reason in {"low_rating_count", OUTCOME_DOMINATED_LOW_RATING}:
        assert detail.air_date is not None
        assert detail.rating_total is not None
        event.update(
            {
                "rating_count": detail.rating_total,
                "air_date": detail.air_date.isoformat(),
                "days_since_air_date": (evaluation_date - detail.air_date).days,
                "threshold": "rating_count < 30",
                "protection_days": "> 7 days",
            }
        )
    if reason != "low_rating_count":
        assert issue_code is not None
        event["issue_code"] = issue_code
        if target_quarter is not None:
            event["target_quarter"] = target_quarter.label
        if japanese is not None:
            event["japanese_evidence"] = {
                "classification": japanese.classification.value,
                "evidence_type": japanese.evidence_type,
                "evidence_value": japanese.evidence_value,
            }
    return event


def _japanese_issue_code(decision: AdmissionDecision) -> str:
    for finding in decision.reviews:
        if finding.issue_code.startswith("JAPANESE_"):
            return finding.issue_code
    return "JAPANESE_CLASSIFICATION_UNRESOLVED"


def resolve_episode_count(
    detail: SubjectDetail, *, registry_count: object = None
) -> EpisodeCountResolution:
    """Resolve the planned main-episode count from bounded trusted evidence.

    ``eps`` is the subject-level planned-count fact.  Bangumi's
    ``total_episodes`` reports the number of episode rows currently present in
    its database, so it is diagnostic only and must not create a planned-count
    conflict or fill an otherwise unknown value.
    """
    eps = _strict_positive_integer(detail.eps)
    infobox = [
        positive
        for item in detail.infobox
        if item.key == "话数"
        and (positive := _strict_positive_integer(item.value)) is not None
    ]
    trusted = ({eps} if eps is not None else set()) | set(infobox)
    if len(trusted) > 1:
        return EpisodeCountResolution(None, "conflict", "episode_count_conflict")
    if eps is not None:
        return EpisodeCountResolution(eps, "subject_structured")
    if infobox:
        return EpisodeCountResolution(infobox[0], "infobox")
    registry = _strict_positive_integer(registry_count)
    if registry is not None:
        return EpisodeCountResolution(registry, "episode_registry")
    return EpisodeCountResolution(None, "unknown")


def resolve_episode_count_with_fallback(
    api: object,
    detail: SubjectDetail,
    subject_id: int,
    warnings: list[dict[str, str]],
) -> EpisodeCountResolution:
    """Resolve episode count once, using only the existing bounded fallback."""
    resolution = resolve_episode_count(detail)
    if resolution.source != "unknown":
        return resolution
    get_episode_count = getattr(api, "get_main_episode_count", None)
    if not callable(get_episode_count):
        return resolution
    try:
        registry_count = get_episode_count(subject_id)
    except BangumiApiError as error:
        warnings.append(
            {
                "code": "episode_count_fallback",
                "summary": (
                    f"episode registry unavailable for subject {subject_id}: "
                    f"{error.summary}"
                ),
            }
        )
    except (TypeError, ValueError):
        warnings.append(
            {
                "code": "episode_count_fallback",
                "summary": (
                    f"episode registry response invalid for subject {subject_id}"
                ),
            }
        )
    else:
        return resolve_episode_count(detail, registry_count=registry_count)
    return resolution


def _needs_tv_boundary_evidence(
    candidate: DiscoveredSubject, detail: SubjectDetail, target_quarter: Quarter
) -> bool:
    """Keep boundary probes limited to unambiguous TV candidates."""
    if detail.air_date is None or candidate.has_date_conflict:
        return False
    if (
        candidate.candidate_date is not None
        and candidate.candidate_date != detail.air_date
    ):
        return False
    if any(media is not MediaFormat.TV for media in candidate.media_formats):
        return False
    if detail.platform is None or normalize_text(detail.platform) != "TV":
        return False
    target_start = target_quarter.start_date
    days_before_target = (target_start - detail.air_date).days
    return 1 <= days_before_target <= TV_BOUNDARY_LOOKBACK_DAYS


def _read_boundary_episode_airdates(
    api: object,
    subject_id: int,
    warnings: list[dict[str, str]],
) -> tuple[date, ...] | None:
    """Read optional main-story dates without making a boundary guess on error."""
    get_airdates = getattr(api, "get_main_episode_airdates", None)
    if not callable(get_airdates):
        return None
    try:
        values = get_airdates(subject_id)
    except BangumiApiError as error:
        warnings.append(
            {
                "code": "boundary_episode_evidence",
                "summary": (
                    f"main episode boundary evidence unavailable for subject "
                    f"{subject_id}: {error.summary}"
                ),
            }
        )
        return None
    except (TypeError, ValueError):
        warnings.append(
            {
                "code": "boundary_episode_evidence",
                "summary": (
                    f"main episode boundary evidence invalid for subject {subject_id}"
                ),
            }
        )
        return None
    if not isinstance(values, (tuple, list)) or not all(
        isinstance(value, date) for value in values
    ):
        warnings.append(
            {
                "code": "boundary_episode_evidence",
                "summary": (
                    f"main episode boundary evidence invalid for subject {subject_id}"
                ),
            }
        )
        return None
    return tuple(values)


def _strict_positive_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdecimal():
            parsed = int(normalized)
            return parsed if parsed > 0 else None
    return None
