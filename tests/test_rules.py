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
        ("日本", "included_japan"),
        ("Japan", "included_japan"),
        ("日本 / 中国", "included_japan"),
        ("日本，美国", "included_japan"),
        ("中国", "excluded_not_japan"),
        ("日本风", "excluded_unparseable_country"),
        ("日本語", "excluded_unparseable_country"),
        ("日本|中国", "excluded_unparseable_country"),
        ("Ｊａｐａｎ", "included_japan"),
    ],
)
def test_country_filter_uses_only_exact_normalized_tokens(
    value: str, decision: str
) -> None:
    result = decide_country(
        [InfoboxItem("制片国家/地区", value)], SETTINGS.country_filter
    )
    assert result.decision == decision


def test_country_filter_handles_missing_and_consistent_or_conflicting_keys() -> None:
    assert (
        decide_country([], SETTINGS.country_filter).decision
        == "excluded_missing_country"
    )
    assert (
        decide_country(
            [
                InfoboxItem("制片国家/地区", "日本、中国"),
                InfoboxItem("国家/地区", "中国 / 日本"),
            ],
            SETTINGS.country_filter,
        ).decision
        == "included_japan"
    )
    assert (
        decide_country(
            [
                InfoboxItem("制片国家/地区", "日本"),
                InfoboxItem("国家/地区", "中国"),
            ],
            SETTINGS.country_filter,
        ).decision
        == "excluded_conflicting_country"
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
        "japan": "included_japan",
        "not_japan": "excluded_not_japan",
        "co_production": "included_japan",
        "missing_country": "excluded_missing_country",
    }
    for name, decision in expected.items():
        values = fixture["verified_public_subjects"][name]["infobox"]
        assert (
            decide_country(
                [InfoboxItem(item["key"], item["value"]) for item in values],
                SETTINGS.country_filter,
            ).decision
            == decision
        )
