"""Shared Jinja rendering for the local and Pages static shells."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from bgm_side_b.build.frontend import drawer_json
from bgm_side_b.build.models import BuildQuarter, SubjectDetailPage


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
        character_media: Mapping[int, RenderMedia] | None = None,
        include_character_images: bool,
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
            character_media=character_media or {},
            include_character_images=include_character_images,
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
