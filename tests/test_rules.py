"""Deterministic archive rules preserve facts and surface ambiguity."""

from __future__ import annotations

from bgm_side_b.domain import (
    JapaneseClassification,
    SourceEvidence,
    SourceType,
)
from bgm_side_b.rules import (
    TagCandidate,
    classify_japanese,
    classify_japanese_with_public_regions,
    display_summary,
    normalize_aliases,
    order_tag_candidates,
    resolve_source,
)


def test_aliases_and_tag_candidates_have_stable_exact_order() -> None:
    assert normalize_aliases(
        [" Alias ", "Ａｌｉａｓ", "别名", "别名", ""],
        excluded=("Original",),
    ) == ("Alias", "别名")
    assert order_tag_candidates(
        (
            TagCandidate("科幻", 5),
            TagCandidate("奇幻", 10),
            TagCandidate(" 冒险 ", 5),
            TagCandidate("科幻", 3),
        )
    ) == ("奇幻", "冒险", "科幻")


def test_source_resolution_is_single_evidence_driven_and_conflict_safe() -> None:
    manga = SourceEvidence(SourceType.MANGA, "infobox", "漫画")
    assert resolve_source((manga, manga)).source_type is SourceType.MANGA
    assert resolve_source(()).source_type is SourceType.UNKNOWN

    conflict = resolve_source(
        (
            manga,
            SourceEvidence(SourceType.ORIGINAL_ANIME, "infobox", "原创"),
        )
    )
    assert conflict.source_type is SourceType.UNKNOWN
    assert conflict.evidence_type == "conflict"
    assert "漫画改" in str(conflict.evidence_value)
    assert "原创动画" in str(conflict.evidence_value)


def test_japanese_classification_uses_only_exact_structured_country_evidence() -> None:
    accepted = classify_japanese((("国家/地区", "日本 / 美国"),))
    rejected = classify_japanese((("制片国家/地区", "中国"),))
    unresolved = classify_japanese((('简介', "日本团队制作"),))
    conflict = classify_japanese(
        (("国家/地区", "日本"), ("制片国家/地区", "中国"))
    )

    assert accepted.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert rejected.classification is JapaneseClassification.REJECTED_NON_JAPANESE
    assert unresolved.classification is JapaneseClassification.UNRESOLVED
    assert unresolved.evidence_type == "unresolved_missing_infobox_country"
    assert conflict.classification is JapaneseClassification.UNRESOLVED
    assert conflict.evidence_type == "unresolved_conflicting_infobox_country"


def test_public_region_tags_are_primary_and_country_is_strict_fallback() -> None:
    accepted = classify_japanese_with_public_regions(("日本", "TV"), ())
    rejected = classify_japanese_with_public_regions(("中国",), ())
    co_production = classify_japanese_with_public_regions(("日本", "中国"), ())
    fallback = classify_japanese_with_public_regions((), (("国家/地区", "日本"),))
    conflict = classify_japanese_with_public_regions(
        ("日本",), (("国家/地区", "中国"),)
    )

    assert accepted.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert accepted.evidence_type == "bangumi_public_region_tag"
    assert rejected.classification is JapaneseClassification.REJECTED_NON_JAPANESE
    assert co_production.classification is JapaneseClassification.UNRESOLVED
    assert fallback.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert conflict.evidence_type == "unresolved_japanese_evidence_conflict"


def test_summary_marker_and_kana_filter_are_conservative() -> None:
    assert display_summary(
        "第一段。\r\n\r\n\r\n第二段。\n [ 简介原文 ] \nこれは原文です。"
    ) == "第一段。\n\n第二段。"
    assert display_summary("\n [简介原文]\nこれは原文です。") is None
    mixed_summary = "这是中文简介，角色名是アキラ。"
    assert display_summary(mixed_summary) == mixed_summary
    assert display_summary(
        "これは明らかな日本語の本文です。物語の始まりと登場人物をとても詳しく紹介します。"
    ) is None
    assert display_summary(None) is None
