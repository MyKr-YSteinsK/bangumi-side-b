"""Detail-page rendering tests for exact local/Pages media boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from test_templates import ROOT, _quarter

from bgm_side_b.build.models import (
    CharacterView,
    EpisodeView,
    MediaView,
    VoiceActorView,
)
from bgm_side_b.build.templates import RenderMedia, TemplateRenderer


def test_detail_page_keeps_facts_and_omits_character_images_for_pages() -> None:
    detail = _detail()
    renderer = TemplateRenderer(ROOT / "templates")
    rendered = renderer.render_detail_page(
        detail,
        stylesheet_href="../../assets/site.css",
        script_href="../../assets/site.js",
        profile_label="Pages 轻量资料",
        navigation_hrefs={(2022, 1): "../../quarters/2022-01/index.html"},
        return_href="../../quarters/2022-01/index.html",
        cover_media=RenderMedia("../../media/covers/101.webp", 400, 600),
        character_media={10: RenderMedia("../../media/characters/10.png", 200, 200)},
        include_character_images=False,
    )
    assert 'data-detail-return' in rendered
    assert "../../media/covers/101.webp" in rendered
    assert "../../media/characters/10.png" not in rendered
    assert "第1话" in rendered
    assert 'data-extra-episode' in rendered
    assert "展开剩余 27 条章节" in rendered
    assert "声优一" in rendered
    assert "STAFF" not in rendered
    assert 'rel="noopener noreferrer"' in rendered


def test_detail_page_includes_verified_local_character_images_only_when_enabled(
) -> None:
    detail = _detail()
    rendered = TemplateRenderer(ROOT / "templates").render_detail_page(
        detail,
        stylesheet_href="../../assets/site.css",
        script_href="../../assets/site.js",
        profile_label="本地完整资料",
        navigation_hrefs={(2022, 1): "../../quarters/2022-01/index.html"},
        return_href="../../quarters/2022-01/index.html",
        cover_media=None,
        character_media={10: RenderMedia("../../media/characters/10.png", 200, 200)},
        include_character_images=True,
    )
    assert "../../media/characters/10.png" in rendered
    assert 'loading="lazy"' in rendered
    assert 'width="200" height="200"' in rendered
    assert 'data-extra-alias' in rendered
    assert 'data-toggle-aliases' in rendered


def _detail():  # type: ignore[no-untyped-def]
    quarter = _quarter()
    original = quarter.details[0]
    card = replace(original.drawer.card, aliases=("A", "B", "C", "D"))
    drawer = replace(original.drawer, card=card, summary="第一段。\n\n第二段。")
    episodes = tuple(
        EpisodeView(
            episode_id=index + 1,
            episode_number=None,
            sort_number=None,
            chinese_title=None,
            original_title=None,
            air_date=date(2022, 1, 1),
            duration_seconds=1440,
            position=index,
        )
        for index in range(51)
    )
    character = CharacterView(
        10,
        "角色",
        "Character",
        None,
        MediaView("character_image", None, None, None, None, None),
        (VoiceActorView(20, "声优一", "Actor One", None, 0),),
        0,
    )
    return replace(original, drawer=drawer, episodes=episodes, characters=(character,))
