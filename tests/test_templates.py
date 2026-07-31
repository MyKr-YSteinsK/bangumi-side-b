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
    assert 'id="quarter-subject-data"' in rendered
    assert '"bangumi_href":"https://bgm.tv/subject/101"' in rendered
    assert "长中文标题" in rendered
    assert "Original Title" in rendered
    assert "暂无评分" in rendered
    assert "tag--source" in rendered
    assert 'aria-pressed="false"' in rendered
    assert "完整资料" in rendered


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


def test_updates_page_uses_chinese_public_release_labels() -> None:
    rendered = TemplateRenderer(ROOT / "templates").render_reference_page(
        "updates.html",
        stylesheet_href="../assets/site.css",
        script_href="../assets/site.js",
        favicon_href="../assets/favicon.svg",
        manifest_href="../manifest.webmanifest",
        apple_touch_icon_href="../icons/icon-192.png",
        pwa_controller_href="../assets/pwa-controller.js",
        pwa_ui_href="../assets/pwa-ui.js",
        home_href="../index.html",
        settings_href="../settings/index.html",
        updates_href="./",
        app_version="0.1.1",
        release={
            "release_version": "2026.07.31.1",
            "app_version": "0.1.1",
            "published_at": "2026-07-31T19:42:11Z",
            "change_kind": "系统与资料均有变化",
            "system": ("PWA 快照校验",),
            "data": ("首次发布完整资料快照",),
        },
        history=(
            {
                "release_version": "2026.07.30.1",
                "app_version": "0.1.0",
                "published_at": "2026-07-30T00:00:00Z",
                "change_kind": "资料有变化",
            },
        ),
    )
    for label in ("当前资料版本", "程序版本", "发布时间", "系统变更", "资料变更"):
        assert label in rendered
    assert "Release 0.1.1" not in rendered


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
        (SubjectDetailPage(drawer, ()),),
        BuildMetadata(2, 1, 0, ()),
    )
