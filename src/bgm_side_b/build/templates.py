"""Shared Jinja rendering for the local and Pages static shells."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from bgm_side_b.build.models import BuildQuarter


class TemplateError(RuntimeError):
    """Raised when a shared template source cannot be loaded safely."""


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

    def render_quarter_shell(
        self,
        quarter: BuildQuarter,
        *,
        stylesheet_href: str,
        script_href: str,
        profile_label: str,
        navigation_hrefs: Mapping[tuple[int, int], str],
    ) -> str:
        """Render the shared editorial shell used by both output profiles."""
        return self.environment.get_template("quarter.html").render(
            quarter=quarter,
            stylesheet_href=stylesheet_href,
            script_href=script_href,
            profile_label=profile_label,
            navigation_hrefs=navigation_hrefs,
        )
