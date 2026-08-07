"""Shared Jinja rendering for the local and Pages static shells."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from bgm_side_b.build.frontend import drawer_json
from bgm_side_b.build.models import BuildQuarter, SubjectDetailPage
from bgm_side_b.release.history import change_kind_display


class TemplateError(RuntimeError):
    """Raised when a shared template source cannot be loaded safely."""


@dataclass(frozen=True)
class RenderMedia:
    """One profile-published cover as seen from a rendered document."""

    href: str
    width: int
    height: int


@dataclass(frozen=True)
class DetailPageContext:
    """The shared shell's quarter marker while rendering one detail page."""

    year: int
    month: int


class TemplateRenderer:
    """Render every profile from one strict, autoescaped template environment."""

    def __init__(self, templates_directory: Path) -> None:
        if not templates_directory.is_dir():
            raise TemplateError("template directory is missing")
        self.environment = Environment(
            loader=FileSystemLoader(templates_directory),
            autoescape=select_autoescape(("html", "xml")),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["episode_title"] = _episode_title
        self.environment.filters["duration_minutes"] = _duration_minutes
        self.environment.filters["change_kind_display"] = change_kind_display

    def render_quarter_shell(
        self,
        quarter: BuildQuarter,
        *,
        stylesheet_href: str,
        script_href: str,
        profile_label: str,
        navigation_hrefs: Mapping[tuple[int, int], str],
        cover_media: Mapping[int, RenderMedia] | None = None,
        detail_hrefs: Mapping[int, str] | None = None,
        favicon_href: str = "favicon.svg",
        pwa_enabled: bool = False,
        manifest_href: str = "manifest.webmanifest",
        apple_touch_icon_href: str = "icons/icon-192.png",
        pwa_controller_href: str = "pwa-controller.js",
        pwa_ui_href: str = "pwa-ui.js",
        settings_href: str = "settings/index.html",
        updates_href: str = "updates/index.html",
        pwa_release_label: str = "等待发布版本信息",
        pwa_total_bytes: int = 0,
        home_href: str | None = None,
    ) -> str:
        """Render the shared editorial shell used by both output profiles."""
        media = cover_media or {}
        cover_hrefs = {subject_id: item.href for subject_id, item in media.items()}
        details = detail_hrefs or {}
        return self.environment.get_template("quarter.html").render(
            quarter=quarter,
            stylesheet_href=stylesheet_href,
            script_href=script_href,
            profile_label=profile_label,
            navigation_hrefs=navigation_hrefs,
            cover_media=media,
            drawer_data=drawer_json(quarter, cover_hrefs, details),
            detail_hrefs=details,
            filter_sources=_filter_values(quarter, "sources", "source"),
            filter_tags=_filter_values(quarter, "tags", "name"),
            page_season=f"{quarter.month:02d}",
            page_kind="quarter",
            header_code=f"ARCHIVE / {quarter.year:04d}-{quarter.month:02d}",
            home_href=home_href
            or navigation_hrefs.get((quarter.year, quarter.month), "index.html"),
            favicon_href=favicon_href,
            pwa_enabled=pwa_enabled,
            manifest_href=manifest_href,
            apple_touch_icon_href=apple_touch_icon_href,
            pwa_controller_href=pwa_controller_href,
            pwa_ui_href=pwa_ui_href,
            settings_href=settings_href,
            updates_href=updates_href,
            pwa_release_label=pwa_release_label,
            pwa_quarter_count=sum(item.has_subjects for item in quarter.navigation),
            pwa_subject_count=quarter.metadata.subject_count,
            pwa_total_bytes=pwa_total_bytes,
        )

    def render_detail_page(
        self,
        detail: SubjectDetailPage,
        *,
        stylesheet_href: str,
        script_href: str,
        profile_label: str,
        navigation_hrefs: Mapping[tuple[int, int], str],
        return_href: str,
        cover_media: RenderMedia | None = None,
        favicon_href: str = "favicon.svg",
        pwa_enabled: bool = False,
        manifest_href: str = "manifest.webmanifest",
        apple_touch_icon_href: str = "icons/icon-192.png",
        pwa_controller_href: str = "pwa-controller.js",
        pwa_ui_href: str = "pwa-ui.js",
        settings_href: str = "settings/index.html",
        updates_href: str = "updates/index.html",
        pwa_release_label: str = "等待发布版本信息",
        pwa_total_bytes: int = 0,
        home_href: str | None = None,
    ) -> str:
        """Render one complete fact page without querying data at runtime."""
        drawer = detail.drawer
        return self.environment.get_template("detail.html").render(
            detail=detail,
            quarter=DetailPageContext(drawer.entered_year, drawer.entered_month),
            stylesheet_href=stylesheet_href,
            script_href=script_href,
            profile_label=profile_label,
            navigation_hrefs=navigation_hrefs,
            return_href=return_href,
            cover_media=cover_media,
            page_season=f"{drawer.entered_month:02d}",
            page_kind="detail",
            header_code=(
                f"ARCHIVE / {drawer.entered_year:04d}-{drawer.entered_month:02d}"
            ),
            home_href=home_href
            or navigation_hrefs.get(
                (drawer.entered_year, drawer.entered_month), return_href
            ),
            favicon_href=favicon_href,
            pwa_enabled=pwa_enabled,
            manifest_href=manifest_href,
            apple_touch_icon_href=apple_touch_icon_href,
            pwa_controller_href=pwa_controller_href,
            pwa_ui_href=pwa_ui_href,
            settings_href=settings_href,
            updates_href=updates_href,
            pwa_release_label=pwa_release_label,
            pwa_quarter_count=0,
            pwa_subject_count=0,
            pwa_total_bytes=pwa_total_bytes,
        )

    def render_reference_page(
        self,
        template_name: str,
        *,
        stylesheet_href: str,
        script_href: str,
        favicon_href: str,
        manifest_href: str,
        apple_touch_icon_href: str,
        pwa_controller_href: str,
        pwa_ui_href: str,
        home_href: str,
        settings_href: str,
        updates_href: str,
        app_version: str,
        release: Mapping[str, object] | None = None,
        history: tuple[Mapping[str, object], ...] = (),
    ) -> str:
        """Render a neutral Pages shell that never pretends to be a quarter."""
        return self.environment.get_template(template_name).render(
            stylesheet_href=stylesheet_href,
            script_href=script_href,
            profile_label="Pages 轻量资料",
            page_season="neutral",
            page_kind=template_name.removesuffix(".html").replace("_", "-"),
            header_code="ARCHIVE / REFERENCE",
            home_href=home_href,
            favicon_href=favicon_href,
            pwa_enabled=True,
            manifest_href=manifest_href,
            apple_touch_icon_href=apple_touch_icon_href,
            pwa_controller_href=pwa_controller_href,
            pwa_ui_href=pwa_ui_href,
            settings_href=settings_href,
            updates_href=updates_href,
            app_version=app_version,
            release=release,
            history=history,
        )


def _filter_values(
    quarter: BuildQuarter, field: str, attribute: str
) -> tuple[str, ...]:
    values: dict[str, None] = {}
    for section in quarter.sections:
        for card in section.subjects:
            for item in getattr(card, field):
                values.setdefault(getattr(item, attribute), None)
    return tuple(values)


def _episode_title(episode: object) -> str:
    chinese = getattr(episode, "chinese_title", None)
    original = getattr(episode, "original_title", None)
    if chinese or original:
        return chinese or original
    number = getattr(episode, "episode_number", None)
    if number is None:
        number = getattr(episode, "sort_number", None)
    if number is None:
        number = getattr(episode, "position", 0) + 1
    return f"第{number:g}话" if isinstance(number, float) else f"第{number}话"


def _duration_minutes(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return f"{max(1, round(seconds / 60))} 分钟"
