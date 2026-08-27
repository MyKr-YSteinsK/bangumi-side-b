"""Deterministic archive rules preserve facts and surface ambiguity."""

from __future__ import annotations

from pathlib import Path

from bgm_side_b.archive_config import load_archive_source_rules
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

ROOT = Path(__file__).resolve().parents[1]


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


def test_source_resolution_honors_only_explicit_compatible_precedence() -> None:
    visual_novel = SourceEvidence(SourceType.VISUAL_NOVEL, "infobox", "视觉小说")
    game = SourceEvidence(SourceType.GAME, "tag", "游戏改")
    light_novel = SourceEvidence(SourceType.LIGHT_NOVEL, "infobox", "轻小说")
    novel = SourceEvidence(SourceType.NOVEL, "tag", "小说改")

    resolved_game = resolve_source((game, visual_novel))
    assert resolved_game.source_type is SourceType.VISUAL_NOVEL
    assert resolved_game.evidence_type == "infobox"
    assert resolved_game.evidence_value == "视觉小说"

    resolved_novel = resolve_source((novel, light_novel))
    assert resolved_novel.source_type is SourceType.LIGHT_NOVEL
    assert resolved_novel.evidence_type == "infobox"
    assert resolved_novel.evidence_value == "轻小说"

    unrelated = resolve_source(
        (
            game,
            visual_novel,
            SourceEvidence(SourceType.MANGA, "infobox", "漫画"),
        )
    )
    assert unrelated.source_type is SourceType.UNKNOWN
    assert unrelated.evidence_type == "conflict"


def test_source_config_contains_audited_exact_tags_and_rezero_evidence() -> None:
    rules = load_archive_source_rules(ROOT / "config" / "source-rules.toml")

    assert rules.tag_values["漫画改"] is SourceType.MANGA
    assert rules.tag_values["漫改"] is SourceType.MANGA
    assert rules.tag_values["轻小说改"] is SourceType.LIGHT_NOVEL
    assert rules.tag_values["轻改"] is SourceType.LIGHT_NOVEL
    assert rules.tag_values["小说改"] is SourceType.NOVEL
    assert rules.tag_values["游戏改"] is SourceType.GAME
    rezero = resolve_source(
        (
            SourceEvidence(SourceType.LIGHT_NOVEL, "tag", "轻小说改"),
            SourceEvidence(SourceType.NOVEL, "tag", "小说改"),
        )
    )
    assert rezero.source_type is SourceType.LIGHT_NOVEL
    assert rezero.evidence_type == "tag"


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
    accepted_alias = classify_japanese_with_public_regions(("Japan",), ())
    rejected = classify_japanese_with_public_regions(("中国",), ())
    co_production = classify_japanese_with_public_regions(("日本", "中国"), ())
    fallback = classify_japanese_with_public_regions((), (("国家/地区", "日本"),))
    conflict = classify_japanese_with_public_regions(
        ("日本",), (("国家/地区", "中国"),)
    )

    assert accepted.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert accepted_alias.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert accepted.evidence_type == "bangumi_public_region_tag"
    assert rejected.classification is JapaneseClassification.REJECTED_NON_JAPANESE
    assert co_production.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert fallback.classification is JapaneseClassification.ACCEPTED_JAPANESE
    assert conflict.evidence_type == "unresolved_japanese_evidence_conflict"


def test_country_parser_accepts_verified_keys_and_middle_dot_separator() -> None:
    assert classify_japanese((('制片国家', '日本・美国'),)).classification is (
        JapaneseClassification.ACCEPTED_JAPANESE
    )
    assert classify_japanese((('地区', '日本｜韩国'),)).classification is (
        JapaneseClassification.ACCEPTED_JAPANESE
    )
    broad = classify_japanese_with_public_regions(("欧美",), ())
    assert broad.classification is JapaneseClassification.UNRESOLVED


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
