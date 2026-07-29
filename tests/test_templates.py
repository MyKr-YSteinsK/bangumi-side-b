"""Checks for the shared, accessible editorial page shell."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bgm_side_b.build.models import (
    BuildMetadata,
    BuildQuarter,
    MediaView,
    QuarterNavigation,
    QuarterSection,
    SourceView,
    SubjectCard,
    SubjectDetailPage,
    SubjectDrawer,
    TagView,
)
from bgm_side_b.build.templates import TemplateRenderer

ROOT = Path(__file__).parents[1]


def test_shared_quarter_shell_has_landmarks_headings_and_no_remote_resources() -> None:
    quarter = _quarter()
    rendered = TemplateRenderer(ROOT / "templates").render_quarter_shell(
        quarter,
        stylesheet_href="../../assets/site.123.css",
        script_href="../../assets/site.456.js",
        profile_label="本地完整资料",
        navigation_hrefs={(2022, 1): "index.html"},
    )
    assert '<a class="skip-link" href="#main-content">' in rendered
    assert "<header" in rendered
    assert '<nav class="quarter-nav"' in rendered
    assert '<main id="main-content"' in rendered
    assert "<footer" in rendered
    assert rendered.index("<h1") < rendered.index("<h2")
    assert "<style" not in rendered
    assert '<link rel="stylesheet" href="../../assets/site.123.css">' in rendered
    assert '<script src="../../assets/site.456.js" defer></script>' in rendered
    assert 'rel="noopener noreferrer"' in rendered
    assert "长中文标题" in rendered
    assert "Original Title" in rendered
    assert "暂无评分" in rendered
    assert "tag--source" in rendered


def test_templates_are_componentized_and_static_sources_keep_accessibility_rules(
) -> None:
    quarter_template = (ROOT / "templates" / "quarter.html").read_text(encoding="utf-8")
    base_template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    for partial in ("quarter-nav.html", "subject-card.html", "drawer.html"):
        assert partial in quarter_template
    card_template = (ROOT / "templates" / "partials" / "subject-card.html").read_text(
        encoding="utf-8"
    )
    assert "tag.html" in card_template
    assert "partials/header.html" in base_template
    assert "partials/footer.html" in base_template

    css = (ROOT / "static" / "css" / "site.css").read_text(encoding="utf-8")
    for token in (
        "--paper:",
        "--paper-raised:",
        "--ink:",
        "--ink-muted:",
        "--rule:",
        "--rule-strong:",
        "--surface-muted:",
        "--accent:",
    ):
        assert token in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "https://" not in css
    assert "linear-gradient" not in css
    script = (ROOT / "static" / "js" / "site.js").read_text(encoding="utf-8")
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script


def _quarter() -> BuildQuarter:
    card = SubjectCard(
        subject_id=101,
        section="new",
        media_format="tv",
        preferred_title="长中文标题：这是一部用于检查编辑排版节奏的作品",
        original_title="Original Title",
        aliases=("Alias",),
        air_date=date(2022, 1, 1),
        declared_episode_count=12,
        total_episode_count=12,
        stored_main_episode_count=12,
        rating_score=None,
        rating_count=None,
        sources=(SourceView("manga"), SourceView("game"), SourceView("original")),
        tags=(TagView("喜剧"), TagView("恋爱")),
        cover=MediaView("cover", None, None, None, None, None),
        search_text="长中文标题 original title alias",
    )
    drawer = SubjectDrawer(card, "完整简介。", None, 2022, 1, 2022, 1)
    return BuildQuarter(
        2022,
        1,
        (QuarterSection("new", "本季度新番", (card,)),),
        (QuarterNavigation(2022, 1, True, True),),
        (SubjectDetailPage(drawer, (), ()),),
        BuildMetadata(2, 1, ()),
    )
