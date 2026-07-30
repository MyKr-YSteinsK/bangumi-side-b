"""Risk-focused tests for deterministic domain rules."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from bgm_side_b.config import load_rules
from bgm_side_b.rules import (
    InfoboxItem,
    Quarter,
    decide_country,
    derive_sources,
    display_tags,
    expand_years,
    format_utc,
    is_quarter_month,
    is_supported_format,
    normalise_aliases,
    preferred_title,
    quarter_for_date,
)

CONFIG_DIRECTORY = Path(__file__).resolve().parents[1] / "config"
SETTINGS, TAG_RULES, SOURCE_RULES = load_rules(CONFIG_DIRECTORY)


@pytest.mark.parametrize("month", [1, 4, 7, 10])
def test_quarter_months_are_limited_to_calendar_starts(month: int) -> None:
    assert is_quarter_month(month)


@pytest.mark.parametrize("month", [0, 2, 3, 5, 12])
def test_non_quarter_months_are_rejected(month: int) -> None:
    assert not is_quarter_month(month)


def test_quarter_dates_and_year_ranges_are_deterministic() -> None:
    assert quarter_for_date(date(2022, 6, 30)) == Quarter(2022, 4)
    assert Quarter(2022, 10).start_date == date(2022, 10, 1)
    assert Quarter(2022, 10).end_date == date(2022, 12, 31)
    assert expand_years(2022, 2024) == (2022, 2023, 2024)
    with pytest.raises(ValueError):
        expand_years(2024, 2022)


def test_only_tv_and_theatrical_movies_are_supported() -> None:
    assert is_supported_format("TV")
    assert is_supported_format("剧场版")
    assert not is_supported_format("WEB")
    assert not is_supported_format("OVA")
    assert not is_supported_format("other")


def test_titles_aliases_and_tags_use_exact_normalisation() -> None:
    assert preferred_title("  中文名  ", "Original") == "中文名"
    assert normalise_aliases([" 中文名", "Ａ", "A", "B"], "中文名") == ("A", "B")
    tags = display_tags(["搞笑", "喜剧", "BL向", "喜剧动画片"], TAG_RULES)
    assert tags == ("喜剧", "BL")


def test_structured_source_evidence_beats_tag_fallback() -> None:
    result = derive_sources(
        [InfoboxItem("原作", "漫画")],
        ["轻小说改编"],
        SOURCE_RULES,
    )

    assert result.sources == ("manga",)
    assert result.evidence[0].evidence_type == "infobox"


def test_source_priority_conflicts_and_unknown_are_explicit() -> None:
    prioritized = derive_sources(
        [], ["游戏改编", "视觉小说改编", "小说改编", "轻小说改编"], SOURCE_RULES
    )
    assert prioritized.sources == ("light_novel", "visual_novel")

    conflict = derive_sources(
        [InfoboxItem("原作", "原创"), InfoboxItem("原案", "漫画")], [], SOURCE_RULES
    )
    assert conflict.sources == ("unknown",)
    assert conflict.warnings == ("original_adaptation_conflict",)
    assert derive_sources([], ["未映射标签"], SOURCE_RULES).sources == ("unknown",)


def test_utc_format_rejects_naive_timestamps() -> None:
    assert format_utc(datetime(2022, 1, 1, tzinfo=UTC)) == "2022-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        format_utc(datetime(2022, 1, 1))


@pytest.mark.parametrize(
    ("value", "decision"),
    [
        ("日本", "included_structured_japan"),
        ("Japan", "included_structured_japan"),
        ("日本 / 中国", "included_structured_japan"),
        ("日本，美国", "included_structured_japan"),
        ("中国", "excluded_structured_non_japan"),
        ("日本风", "included_tv_default"),
        ("日本語", "included_tv_default"),
        ("日本|中国", "included_tv_default"),
        ("Ｊａｐａｎ", "included_structured_japan"),
    ],
)
def test_country_filter_uses_only_exact_normalized_tokens(
    value: str, decision: str
) -> None:
    result = _country_decision([InfoboxItem("制片国家/地区", value)])
    assert result.decision == decision


def test_country_filter_handles_missing_and_consistent_or_conflicting_keys() -> None:
    assert (
        _country_decision([]).decision
        == "included_tv_default"
    )
    assert (
        _country_decision(
            [
                InfoboxItem("制片国家/地区", "日本、中国"),
                InfoboxItem("国家/地区", "中国 / 日本"),
            ]
        ).decision
        == "included_structured_japan"
    )
    assert (
        _country_decision(
            [
                InfoboxItem("制片国家/地区", "日本"),
                InfoboxItem("国家/地区", "中国"),
            ]
        ).decision
        == "included_tv_default"
    )


def test_country_filter_prioritises_decisive_structured_evidence() -> None:
    assert (
        _country_decision(
            [InfoboxItem("制片国家/地区", "日本")], tags=("中国",)
        ).decision
        == "included_structured_japan"
    )
    assert (
        _country_decision(
            [InfoboxItem("制片国家/地区", "中国")], tags=("日本动画",)
        ).decision
        == "excluded_structured_non_japan"
    )


def test_minimal_public_country_fixtures_cover_verified_keys() -> None:
    fixture = json.loads(
        (
            CONFIG_DIRECTORY.parent
            / "tests"
            / "fixtures"
            / "api"
            / "country"
            / "minimal-country-subjects.json"
        ).read_text(encoding="utf-8")
    )
    expected = {
        "japan": "included_structured_japan",
        "not_japan": "excluded_structured_non_japan",
        "co_production": "included_structured_japan",
        "missing_country": "included_tv_default",
    }
    for name, decision in expected.items():
        values = fixture["verified_public_subjects"][name]["infobox"]
        assert (
            _country_decision(
                [InfoboxItem(item["key"], item["value"]) for item in values]
            ).decision
            == decision
        )


@pytest.mark.parametrize(
    ("tags", "decision"),
    [
        (("日本",), "included_tag_japan"),
        (("日本动画",), "included_tag_japan"),
        (("国产",), "excluded_negative_tag"),
        (("欧美",), "excluded_negative_tag"),
        (("日本", "中国"), "excluded_tag_conflict"),
    ],
)
def test_country_filter_uses_only_exact_configured_region_tags(
    tags: tuple[str, ...], decision: str
) -> None:
    assert _country_decision([], tags=tags).decision == decision


def test_country_filter_falls_back_from_invalid_structured_evidence_to_tags() -> None:
    assert (
        _country_decision(
            [
                InfoboxItem("制片国家/地区", "日本"),
                InfoboxItem("国家/地区", "中国"),
            ],
            tags=("日本动画",),
        ).decision
        == "included_tag_japan"
    )
    assert (
        _country_decision(
            [InfoboxItem("制片国家/地区", "日本风")], tags=("中国",)
        ).decision
        == "excluded_negative_tag"
    )


def test_country_filter_defaults_for_complete_seasonal_tv() -> None:
    decisions = [_country_decision([]).decision for _ in range(85)]
    assert decisions == ["included_tv_default"] * 85
    assert _country_decision([], subject_type=2, platform="剧场版").decision == (
        "excluded_no_region_evidence"
    )
    assert _country_decision([], air_date=date(2026, 7, 1)).decision == (
        "excluded_no_region_evidence"
    )
    fuzzy = _country_decision([], tags=("日本风", "日语"))
    assert fuzzy.decision == "included_tv_default"
    assert fuzzy.matched_positive_tags == ()


def _country_decision(
    infobox: list[InfoboxItem],
    *,
    tags: tuple[str, ...] = (),
    subject_type: int = 2,
    platform: str = "TV",
    air_date: date = date(2026, 4, 2),
) -> object:
    return decide_country(
        infobox,
        SETTINGS.country_filter,
        raw_tags=tags,
        subject_type=subject_type,
        platform=platform,
        air_date=air_date,
        target_quarter=Quarter(2026, 4),
    )
