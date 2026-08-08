"""Archive-domain contracts reject out-of-scope or ambiguous values."""

from __future__ import annotations

import pytest

from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAssignmentSource,
    SourceType,
)


def test_media_format_contains_only_tv_and_movie() -> None:
    assert tuple(MediaFormat) == (MediaFormat.TV, MediaFormat.MOVIE)
    assert MediaFormat("TV") is MediaFormat.TV
    assert MediaFormat("MOVIE") is MediaFormat.MOVIE
    for legacy in ("WEB", "OVA", "OTHER"):
        with pytest.raises(ValueError):
            MediaFormat(legacy)


@pytest.mark.parametrize("month", (1, 4, 7, 10))
def test_quarter_accepts_only_calendar_quarter_starts(month: int) -> None:
    assert Quarter(2026, month).month == month


@pytest.mark.parametrize("month", (0, 2, 3, 5, 12, 13))
def test_quarter_rejects_non_quarter_months(month: int) -> None:
    with pytest.raises(ValueError, match="quarter month"):
        Quarter(2026, month)


def test_japanese_classification_is_explicit_and_evidence_bound() -> None:
    accepted = JapaneseDecision(
        JapaneseClassification.ACCEPTED_JAPANESE,
        "infobox_country",
        "日本",
    )
    rejected = JapaneseDecision(
        JapaneseClassification.REJECTED_NON_JAPANESE,
        "infobox_country",
        "中国",
    )
    unresolved = JapaneseDecision(JapaneseClassification.UNRESOLVED)

    assert accepted.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert rejected.classification is JapaneseClassification.REJECTED_NON_JAPANESE
    assert unresolved.classification is JapaneseClassification.UNRESOLVED
    with pytest.raises(ValueError, match="require structured evidence"):
        JapaneseDecision(JapaneseClassification.ACCEPTED_JAPANESE)
    with pytest.raises(ValueError, match="must be paired"):
        JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            evidence_type="infobox_country",
        )


def test_source_and_assignment_enums_are_closed_contracts() -> None:
    assert {item.value for item in SourceType} == {
        "漫画改",
        "轻小说改",
        "小说改",
        "游戏改",
        "视觉小说改",
        "原创动画",
        "其他改编",
        "来源未知",
    }
    assert tuple(QuarterAssignmentSource) == (
        QuarterAssignmentSource.AUTOMATIC,
        QuarterAssignmentSource.MANUAL,
    )
    with pytest.raises(ValueError):
        QuarterAssignmentSource("review")
