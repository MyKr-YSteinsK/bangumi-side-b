"""Detail pages contain only subject facts, episodes, and covers."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from test_templates import ROOT, _quarter

from bgm_side_b.build.models import EpisodeView
from bgm_side_b.build.templates import RenderMedia, TemplateRenderer


def test_detail_page_omits_roles_for_every_static_profile() -> None:
    detail = _detail()
    rendered = TemplateRenderer(ROOT / "templates").render_detail_page(
        detail,
        stylesheet_href="../../assets/site.css",
        script_href="../../assets/site.js",
        profile_label="Pages",
        navigation_hrefs={(2026, 4): "../../quarters/2026-04/index.html"},
        return_href="../../quarters/2026-04/index.html",
        cover_media=RenderMedia("../../media/covers/101.webp", 400, 600),
    )

    assert "data-detail-return" in rendered
    assert "../../media/covers/101.webp" in rendered
    assert "data-extra-episode" in rendered
    assert "MAIN CAST" not in rendered
    assert "character" not in rendered.lower()
    assert "voice" not in rendered.lower()
    assert 'rel="noopener noreferrer"' in rendered


def _detail():  # type: ignore[no-untyped-def]
    original = _quarter().details[0]
    card = replace(original.drawer.card, aliases=("A", "B", "C", "D"))
    drawer = replace(original.drawer, card=card, summary="First paragraph.\n\nSecond.")
    episodes = tuple(
        EpisodeView(
            episode_id=index + 1,
            episode_number=None,
            sort_number=None,
            chinese_title=None,
            original_title=None,
            air_date=date(2026, 4, 1),
            duration_seconds=1440,
            position=index,
        )
        for index in range(51)
    )
    return replace(original, drawer=drawer, episodes=episodes)
