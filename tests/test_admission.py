"""Risk-focused admission and archive-quarter decision coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from bgm_side_b.admission import (
    CONFLICT_REVIEW_ISSUES,
    DISCOVERY_DATE_MISMATCH,
    DISCOVERY_MEDIA_CONFLICT,
    JAPANESE_CLASSIFICATION_UNRESOLVED,
    MOVIE_DATE_UNRESOLVED,
    TV_QUARTER_BOUNDARY,
    TV_QUARTER_DATE_UNRESOLVED,
    UNRESOLVED_COLD_REVIEW_ISSUES,
    AdmissionStatus,
    QuarterOverride,
    admit_subject,
    is_conflict_review,
    is_unresolved_cold_review,
    quarter_end_date,
    should_auto_blacklist_unresolved_cold,
)
from bgm_side_b.api import ApiTag, SubjectDetail
from bgm_side_b.discovery import DiscoveredSubject
from bgm_side_b.domain import JapaneseClassification, MediaFormat, Quarter


def _candidate(
    subject_id: int = 101,
    *,
    formats: frozenset[MediaFormat] = frozenset({MediaFormat.TV}),
    dates: frozenset[date] = frozenset({date(2026, 4, 2)}),
) -> DiscoveredSubject:
    return DiscoveredSubject(subject_id, formats, dates, frozenset({2}), ("browse",))


def _detail(
    subject_id: int = 101,
    *,
    subject_type: int | None = 2,
    platform: str | None = "TV",
    air_date: str | None = "2026-04-02",
    country: str | None = "日本",
    meta_tags: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> SubjectDetail:
    infobox = [] if country is None else [{"key": "国家/地区", "value": country}]
    return SubjectDetail.from_payload(
        {
            "id": subject_id,
            "type": subject_type,
            "name": "Original",
            "name_cn": "中文",
            "date": air_date,
            "platform": platform,
            "eps": 12,
            "rating": {"score": 7.5, "total": 100},
            "infobox": infobox,
            "meta_tags": list(meta_tags),
            "tags": [{"name": value, "count": 1} for value in tags],
            "images": {},
        }
    )


def test_blacklist_is_checked_before_any_candidate_or_detail_normalisation() -> None:
    decision = admit_subject(
        _candidate(101),
        _detail(101, subject_type=None, country=None),
        Quarter(2026, 4),
        excluded_subject_ids=frozenset({101}),
    )

    assert decision.status is AdmissionStatus.BLACKLISTED
    assert decision.reason == "blacklist"


@pytest.mark.parametrize("platform", ("WEB", "OVA", "OAD"))
def test_explicit_unsupported_platform_cannot_inherit_browse_tv(
    platform: str,
) -> None:
    decision = admit_subject(
        _candidate(),
        _detail(platform=platform, country="日本"),
        Quarter(2026, 4),
    )

    assert decision.status is AdmissionStatus.REJECTED
    assert decision.media_format is None
    assert decision.reason == "unsupported_media"


def test_japanese_three_state_admission_never_guesses() -> None:
    accepted = admit_subject(_candidate(), _detail(country="日本"), Quarter(2026, 4))
    rejected = admit_subject(_candidate(), _detail(country="中国"), Quarter(2026, 4))
    unresolved = admit_subject(_candidate(), _detail(country=None), Quarter(2026, 4))

    assert accepted.status is AdmissionStatus.ACCEPTED
    assert accepted.japanese is not None
    assert accepted.japanese.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert rejected.status is AdmissionStatus.REJECTED
    assert unresolved.status is AdmissionStatus.REVIEW
    assert unresolved.reviews[0].issue_code == JAPANESE_CLASSIFICATION_UNRESOLVED


def test_public_regions_are_decisive_and_ordinary_region_tags_have_no_effect() -> None:
    accepted = admit_subject(
        _candidate(), _detail(country=None, meta_tags=("日本",)), Quarter(2026, 4)
    )
    rejected = admit_subject(
        _candidate(), _detail(country=None, meta_tags=("中国",)), Quarter(2026, 4)
    )
    conflict = admit_subject(
        _candidate(),
        _detail(country=None, meta_tags=("日本", "中国")),
        Quarter(2026, 4),
    )
    ordinary_tag = admit_subject(
        _candidate(), _detail(country=None, tags=("日本",)), Quarter(2026, 4)
    )

    assert accepted.status is AdmissionStatus.ACCEPTED
    assert rejected.status is AdmissionStatus.REJECTED
    assert conflict.reviews[0].issue_code == "JAPANESE_REGION_CONFLICT"
    assert ordinary_tag.status is AdmissionStatus.REVIEW


def test_tv_boundary_observation_is_reviewed_at_one_to_seven_days_only() -> None:
    target = Quarter(2026, 4)
    boundary = admit_subject(
        _candidate(dates=frozenset({date(2026, 3, 26)})),
        _detail(air_date="2026-03-26"),
        target,
    )
    mismatch = admit_subject(
        _candidate(dates=frozenset({date(2026, 3, 24)})),
        _detail(air_date="2026-03-24"),
        target,
    )
    january = admit_subject(
        _candidate(dates=frozenset({date(2025, 12, 25)})),
        _detail(air_date="2025-12-25"),
        Quarter(2026, 1),
    )

    assert boundary.reviews[0].issue_code == TV_QUARTER_BOUNDARY
    assert boundary.reviews[0].details["days_before_target"] == 6
    assert mismatch.reviews[0].issue_code == DISCOVERY_DATE_MISMATCH
    assert january.reviews[0].issue_code == TV_QUARTER_BOUNDARY


def test_high_confidence_target_quarter_tag_resolves_early_tv_premiere() -> None:
    detail = replace(
        _detail(air_date="2026-03-28"),
        tags=(
            ApiTag("2026年4月", 448),
            ApiTag("2026年1月", 2),
            ApiTag("TV", 500),
            ApiTag("2026年7月", 8),
        ),
    )
    decision = admit_subject(
        _candidate(dates=frozenset({date(2026, 3, 28)})),
        detail,
        Quarter(2026, 4),
    )

    assert decision.status is AdmissionStatus.ACCEPTED
    assert decision.premiere is not None
    assert decision.premiere.evidence_type == "community_quarter_tag"
    assert decision.premiere.evidence_value == "2026年4月:448"


def test_movie_uses_natural_quarter_and_missing_dates_are_reviewed() -> None:
    movie = admit_subject(
        _candidate(
            formats=frozenset({MediaFormat.MOVIE}),
            dates=frozenset({date(2026, 6, 28)}),
        ),
        _detail(platform="剧场版", air_date="2026-06-28"),
        Quarter(2026, 4),
    )
    missing_movie = admit_subject(
        _candidate(formats=frozenset({MediaFormat.MOVIE}), dates=frozenset()),
        _detail(platform="剧场版", air_date=None),
        Quarter(2026, 4),
    )
    missing_tv = admit_subject(
        _candidate(dates=frozenset()), _detail(air_date=None), Quarter(2026, 4)
    )

    assert movie.status is AdmissionStatus.ACCEPTED
    assert movie.premiere is not None and movie.premiere.quarter == Quarter(2026, 4)
    assert missing_movie.reviews[0].issue_code == MOVIE_DATE_UNRESOLVED
    assert missing_tv.reviews[0].issue_code == TV_QUARTER_DATE_UNRESOLVED


def test_unresolved_cold_allowlist_contains_only_evidence_missing_issues() -> None:
    assert UNRESOLVED_COLD_REVIEW_ISSUES == frozenset(
        {
            TV_QUARTER_BOUNDARY,
            TV_QUARTER_DATE_UNRESOLVED,
            MOVIE_DATE_UNRESOLVED,
            "SEARCH_ONLY_MEDIA_UNRESOLVED",
        }
    )
    assert all(
        is_unresolved_cold_review(code) for code in UNRESOLVED_COLD_REVIEW_ISSUES
    )
    assert not is_unresolved_cold_review(DISCOVERY_DATE_MISMATCH)
    assert not is_unresolved_cold_review(DISCOVERY_MEDIA_CONFLICT)
    assert not is_unresolved_cold_review("JAPANESE_REGION_CONFLICT")
    assert CONFLICT_REVIEW_ISSUES == frozenset(
        {
            DISCOVERY_DATE_MISMATCH,
            DISCOVERY_MEDIA_CONFLICT,
            "JAPANESE_REGION_CONFLICT",
            "JAPANESE_EVIDENCE_CONFLICT",
        }
    )
    assert all(is_conflict_review(code) for code in CONFLICT_REVIEW_ISSUES)


@pytest.mark.parametrize("rating_count", (None, 0, 29, 30, 500))
def test_unresolved_cold_rule_ignores_rating_count_and_requires_maturity(
    rating_count: int | None,
) -> None:
    quarter = Quarter(2026, 4)
    assert quarter_end_date(quarter) == date(2026, 6, 30)
    assert should_auto_blacklist_unresolved_cold(
        MOVIE_DATE_UNRESOLVED,
        quarter,
        rating_count,
        date(2026, 7, 8),
    )
    assert not should_auto_blacklist_unresolved_cold(
        TV_QUARTER_DATE_UNRESOLVED,
        Quarter(2026, 4),
        rating_count,
        date(2026, 7, 7),
    )


def test_unresolved_cold_rule_requires_allowlisted_issue_and_target_quarter() -> None:
    evaluation_date = date(2026, 7, 8)
    assert not should_auto_blacklist_unresolved_cold(
        DISCOVERY_DATE_MISMATCH,
        Quarter(2026, 4),
        0,
        evaluation_date,
    )
    assert not should_auto_blacklist_unresolved_cold(
        MOVIE_DATE_UNRESOLVED, None, 0, evaluation_date
    )


def test_manual_override_wins_only_after_scope_and_japanese_admission() -> None:
    manual = admit_subject(
        _candidate(dates=frozenset({date(2026, 3, 20)})),
        _detail(air_date="2026-03-20"),
        Quarter(2026, 4),
        override=QuarterOverride(Quarter(2026, 4), "early broadcast"),
    )
    unresolved = admit_subject(
        _candidate(),
        _detail(country=None),
        Quarter(2026, 4),
        override=QuarterOverride(Quarter(2026, 4)),
    )

    assert manual.status is AdmissionStatus.ACCEPTED
    assert manual.premiere is not None
    assert manual.premiere.assignment_source.value == "manual"
    assert unresolved.status is AdmissionStatus.REVIEW


def test_discovery_media_and_date_conflicts_require_review() -> None:
    media_conflict = admit_subject(
        _candidate(formats=frozenset({MediaFormat.TV, MediaFormat.MOVIE})),
        _detail(),
        Quarter(2026, 4),
    )
    date_conflict = admit_subject(
        _candidate(dates=frozenset({date(2026, 4, 2), date(2026, 4, 3)})),
        _detail(),
        Quarter(2026, 4),
    )

    assert media_conflict.reviews[0].issue_code == DISCOVERY_MEDIA_CONFLICT
    assert date_conflict.reviews[0].issue_code == DISCOVERY_DATE_MISMATCH
