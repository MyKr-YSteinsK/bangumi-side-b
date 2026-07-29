"""Offline orchestration for deterministic local and Pages static builds."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.build.assets import (
    MediaPublisher,
    PublishedMedia,
    assert_pages_media_policy,
    generate_pwa_icons,
    publish_static_assets,
)
from bgm_side_b.build.output import AtomicOutput
from bgm_side_b.build.paths import PathResolver
from bgm_side_b.build.profiles import BuildProfile, local_profile, pages_profile
from bgm_side_b.build.projection import BuildProjection
from bgm_side_b.build.queries import BuildQueries
from bgm_side_b.build.report import ProfileBuildReport, write_build_report
from bgm_side_b.build.templates import RenderMedia, TemplateRenderer
from bgm_side_b.config import ProjectSettings, SourceRules, TagRules
from bgm_side_b.database import Database
from bgm_side_b.release.candidate import write_pages_build_marker
from bgm_side_b.sync import SyncScope


class BuildError(RuntimeError):
    """Raised when a static build cannot safely produce a complete site."""


@dataclass(frozen=True)
class BuildRun:
    """Successful profile outputs and the report written outside ``dist``."""

    report_path: Path
    profiles: tuple[ProfileBuildReport, ...]


class ArchiveBuilder:
    """Generate both profiles from SQLite without sync, network, or runtime data."""

    def __init__(
        self,
        project_root: Path,
        database: Database,
        settings: ProjectSettings,
        tag_rules: TagRules,
        source_rules: SourceRules,
        *,
        workspace_directory: Path | None = None,
        distribution_directory: Path | None = None,
        reports_directory: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.database = database
        self.settings = settings
        self.tag_rules = tag_rules
        self.source_rules = source_rules
        self.workspace_directory = workspace_directory or project_root / "workspace"
        self.distribution_directory = distribution_directory or project_root / "dist"
        self.reports_directory = (
            reports_directory or self.workspace_directory / "reports"
        )

    def build(
        self,
        scope: SyncScope | None,
        *,
        target: str = "all",
    ) -> BuildRun:
        """Build every currently available quarter to avoid partial-site link rot.

        The explicit scope remains useful for validation/reporting and CLI parity,
        but each invocation regenerates the complete current SQLite archive. This
        makes navigation, the landing page, detail links, and blacklist cleanup
        deterministic without preserving stale generated pages from older runs.
        """
        if target not in {"all", "local", "pages"}:
            raise ValueError("build target must be local, pages, or all")
        self._verify_database()
        started = datetime.now(UTC)
        queries = BuildQueries(self.database)
        quarters = queries.list_quarters(self.settings.excluded_subject_ids)
        models = tuple(
            BuildProjection(
                self.tag_rules,
                self.source_rules,
                self.workspace_directory,
                excluded_subject_ids=self.settings.excluded_subject_ids,
            ).project_quarter(
                queries.load_quarter(
                    year,
                    month,
                    excluded_subject_ids=self.settings.excluded_subject_ids,
                )
            )
            for year, month in quarters
        )
        profiles = _profiles(target)
        reports: list[ProfileBuildReport] = []
        for profile in profiles:
            reports.append(self._build_profile(profile, models))
        finished = datetime.now(UTC)
        label = "all" if scope is None else scope.label
        report = write_build_report(
            self.reports_directory, label, started, finished, tuple(reports)
        )
        pages_report = next((item for item in reports if item.profile == "pages"), None)
        if pages_report is not None:
            write_pages_build_marker(
                self.workspace_directory,
                self.distribution_directory / "pages",
                project_root=self.project_root,
                deployment_path=pages_profile().deployment_path,
                quarter_count=pages_report.quarters,
                subject_count=pages_report.subjects,
            )
        return BuildRun(report, tuple(reports))

    def _build_profile(
        self, profile: BuildProfile, models: tuple[object, ...]
    ) -> ProfileBuildReport:
        output = AtomicOutput(self.distribution_directory)
        metrics: dict[str, object] = {}

        def writer(stage: Path) -> None:
            metrics.update(self._write_profile(stage, profile, models))

        def validator(stage: Path) -> None:
            _validate_output(stage, profile)

        result = output.generate(profile, writer, validator)
        return ProfileBuildReport(
            profile.name,
            int(metrics["quarters"]),
            int(metrics["subjects"]),
            int(metrics["details"]),
            int(metrics["covers"]),
            int(metrics["character_images"]),
            int(metrics["missing_covers"]),
            tuple(metrics["warnings"]),
            _tree_bytes(result.output_directory),
            _tree_files(result.output_directory),
            False,
            (),
            css_bytes=int(metrics["css_bytes"]),
            js_bytes=int(metrics["js_bytes"]),
            quarter_html_bytes=int(metrics["quarter_html_bytes"]),
            drawer_json_bytes=int(metrics["drawer_json_bytes"]),
            covers_bytes=int(metrics["covers_bytes"]),
            detail_bytes=int(metrics["detail_bytes"]),
        )

    def _write_profile(
        self, stage: Path, profile: BuildProfile, models: tuple[object, ...]
    ) -> dict[str, object]:
        renderer = TemplateRenderer(self.project_root / "templates")
        assets = publish_static_assets(self.project_root / "static", stage)
        stylesheet = assets.get("css/site.css")
        script = assets.get("js/site.js")
        favicon = assets.get("icons/favicon.svg")
        if stylesheet is None or script is None or favicon is None:
            raise BuildError("required static source assets are missing")
        if profile.pwa_enabled:
            pwa_controller = assets.get("js/pwa-controller.js")
            pwa_ui = assets.get("js/pwa-ui.js")
            if pwa_controller is None or pwa_ui is None:
                raise BuildError("required PWA source assets are missing")
            generate_pwa_icons(stage)
            _write_text(
                stage / "manifest.webmanifest",
                json.dumps(_pwa_manifest(), ensure_ascii=False, separators=(",", ":"))
                + "\n",
            )
            _write_service_worker(stage, assets)
        resolver = PathResolver(profile)
        publisher = MediaPublisher(self.workspace_directory, stage)
        warnings: list[str] = []
        covers = 0
        character_images = 0
        missing_covers = 0
        detail_by_subject: dict[int, object] = {}
        subject_count = 0
        for model in models:
            subject_count += model.metadata.subject_count
            for detail in model.details:
                detail_by_subject.setdefault(detail.drawer.card.subject_id, detail)

        for model in models:
            document = resolver.quarter(model.year, model.month)
            cover_media: dict[int, RenderMedia] = {}
            detail_hrefs: dict[int, str] = {}
            for section in model.sections:
                for card in section.subjects:
                    published = publisher.publish_cover(
                        card.subject_id, card.cover, profile
                    )
                    if published is None:
                        missing_covers += 1
                        warnings.append(
                            f"subject {card.subject_id} has no published cover"
                        )
                    else:
                        covers += 1
                        cover_media[card.subject_id] = _render_media(
                            resolver, document, published
                        )
                    detail_hrefs[card.subject_id] = (
                        resolver.href(document, resolver.subject(card.subject_id))
                        + f"?from={model.year:04d}-{model.month:02d}"
                    )
            navigation = {
                (nav.year, nav.month): resolver.href(
                    document, resolver.quarter(nav.year, nav.month)
                )
                for nav in model.navigation
            }
            _write_text(
                stage / document,
                renderer.render_quarter_shell(
                    model,
                    stylesheet_href=resolver.asset(document, stylesheet),
                    script_href=resolver.asset(document, script),
                    profile_label=_profile_label(profile),
                    navigation_hrefs=navigation,
                    cover_media=cover_media,
                    detail_hrefs=detail_hrefs,
                    **_shell_links(profile, resolver, document, assets),
                ),
            )
            warnings.extend(model.metadata.warnings)

        for subject_id, detail in detail_by_subject.items():
            document = resolver.subject(subject_id)
            card = detail.drawer.card
            published_cover = publisher.publish_cover(subject_id, card.cover, profile)
            cover = (
                _render_media(resolver, document, published_cover)
                if published_cover is not None
                else None
            )
            character_media: dict[int, RenderMedia] = {}
            if profile.include_character_images:
                for character in detail.characters:
                    published = publisher.publish_character(
                        character.character_id, character.image, profile
                    )
                    if published is None:
                        if character.image.is_available:
                            warnings.append(
                                "character "
                                f"{character.character_id} has no published image"
                            )
                        continue
                    character_images += 1
                    character_media[character.character_id] = _render_media(
                        resolver, document, published
                    )
            permanent = _detail_return_quarter(detail)
            entered = (detail.drawer.entered_year, detail.drawer.entered_month)
            navigation = {entered: resolver.href(document, resolver.quarter(*entered))}
            _write_text(
                stage / document,
                renderer.render_detail_page(
                    detail,
                    stylesheet_href=resolver.asset(document, stylesheet),
                    script_href=resolver.asset(document, script),
                    profile_label=_profile_label(profile),
                    navigation_hrefs=navigation,
                    return_href=resolver.href(document, resolver.quarter(*permanent)),
                    cover_media=cover,
                    character_media=character_media,
                    include_character_images=profile.include_character_images,
                    **_shell_links(profile, resolver, document, assets),
                ),
            )
        if profile.pwa_enabled:
            for document, template_name in (
                ("settings/index.html", "settings.html"),
                ("updates/index.html", "updates.html"),
                ("offline.html", "offline.html"),
            ):
                links = _shell_links(profile, resolver, document, assets)
                _write_text(
                    stage / document,
                    renderer.render_reference_page(
                        template_name,
                        stylesheet_href=resolver.asset(document, stylesheet),
                        script_href=resolver.asset(document, script),
                        favicon_href=links["favicon_href"],
                        manifest_href=links["manifest_href"],
                        apple_touch_icon_href=links["apple_touch_icon_href"],
                        pwa_controller_href=links["pwa_controller_href"],
                        pwa_ui_href=links["pwa_ui_href"],
                        home_href=links["home_href"],
                        settings_href=links["settings_href"],
                        updates_href=links["updates_href"],
                        app_version=__version__,
                    ),
                )
        _write_text(stage / "index.html", _index_html(models, profile))
        if profile.name == "pages":
            assert_pages_media_policy(stage)
        quarter_html = tuple(
            stage / resolver.quarter(model.year, model.month) for model in models
        )
        detail_html = tuple(
            stage / resolver.subject(subject_id) for subject_id in detail_by_subject
        )
        drawer_bytes = sum(
            len(match.encode("utf-8"))
            for page in quarter_html
            for match in re.findall(
                (
                    r'<script type="application/json" '
                    r'id="quarter-subject-data">(.*?)</script>'
                ),
                page.read_text(encoding="utf-8"),
                flags=re.DOTALL,
            )
        )
        covers_directory = stage / "media" / "covers"
        return {
            "quarters": len(models),
            "subjects": subject_count,
            "details": len(detail_by_subject),
            "covers": covers,
            "character_images": character_images,
            "missing_covers": missing_covers,
            "warnings": warnings,
            "css_bytes": (stage / stylesheet).stat().st_size,
            "js_bytes": (stage / script).stat().st_size,
            "quarter_html_bytes": sum(page.stat().st_size for page in quarter_html),
            "drawer_json_bytes": drawer_bytes,
            "covers_bytes": (
                _tree_bytes(covers_directory) if covers_directory.exists() else 0
            ),
            "detail_bytes": sum(page.stat().st_size for page in detail_html),
        }

    def _verify_database(self) -> None:
        if not self.database.path.is_file():
            raise BuildError("database is missing")
        connection = self.database.connect()
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise BuildError("database integrity check failed")
        except sqlite3.Error as error:
            raise BuildError("database cannot be verified") from error
        finally:
            connection.close()


def _profiles(target: str) -> tuple[BuildProfile, ...]:
    profiles = {"local": local_profile(), "pages": pages_profile()}
    return tuple(profiles.values()) if target == "all" else (profiles[target],)


def _render_media(
    resolver: PathResolver, document: str, media: PublishedMedia
) -> RenderMedia:
    return RenderMedia(
        resolver.href(document, media.relative_path), media.width, media.height
    )


def _detail_return_quarter(detail: object) -> tuple[int, int]:
    drawer = detail.drawer
    if drawer.permanent_year is not None and drawer.permanent_month is not None:
        return drawer.permanent_year, drawer.permanent_month
    return drawer.entered_year, drawer.entered_month


def _profile_label(profile: BuildProfile) -> str:
    return "本地完整资料" if profile.name == "local" else "Pages 轻量资料"


def _shell_links(
    profile: BuildProfile,
    resolver: PathResolver,
    document: str,
    assets: dict[str, str],
) -> dict[str, object]:
    """Return document-relative shared-shell links for either output profile."""
    output: dict[str, object] = {
        "favicon_href": resolver.asset(document, assets["icons/favicon.svg"]),
        "pwa_enabled": profile.pwa_enabled,
        "manifest_href": resolver.href(document, "manifest.webmanifest"),
        "apple_touch_icon_href": resolver.href(document, "icons/icon-192.png"),
        "settings_href": resolver.href(document, "settings/index.html"),
        "updates_href": resolver.href(document, "updates/index.html"),
        "pwa_release_label": "等待发布版本信息",
        "pwa_total_bytes": 0,
        "home_href": resolver.href(document, "index.html"),
    }
    if profile.pwa_enabled:
        output["pwa_controller_href"] = resolver.asset(
            document, assets["js/pwa-controller.js"]
        )
        output["pwa_ui_href"] = resolver.asset(document, assets["js/pwa-ui.js"])
    return output


def _pwa_manifest() -> dict[str, object]:
    """The portable Pages manifest has relative paths for repository subpaths."""
    return {
        "name": "Bangumi Side B by MyKr",
        "short_name": "BGM B",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f5f1e8",
        "theme_color": "#f5f1e8",
        "lang": "zh-CN",
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "icons/icon-512-maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }


def _write_service_worker(stage: Path, assets: dict[str, str]) -> None:
    """Publish a stable worker URL while injecting only generated shell paths."""
    source = (stage / assets["sw.js"]).read_text(encoding="utf-8")
    shell_files = [
        "./index.html",
        "./offline.html",
        "./settings/index.html",
        "./updates/index.html",
        "./manifest.webmanifest",
        "./icons/icon-192.png",
        "./icons/icon-512.png",
        "./icons/icon-512-maskable.png",
        "./" + assets["css/site.css"],
        "./" + assets["js/site.js"],
        "./" + assets["js/pwa-controller.js"],
        "./" + assets["js/pwa-ui.js"],
        "./" + assets["icons/favicon.svg"],
    ]
    marker = "/* __BSB_SHELL_FILES__ */"
    if marker not in source:
        raise BuildError("service worker shell marker is missing")
    _write_text(
        stage / "sw.js",
        source.replace(marker, json.dumps(shell_files, separators=(",", ":"))),
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _index_html(models: tuple[object, ...], profile: BuildProfile) -> str:
    if not models:
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            "<title>Bangumi Side B｜MyKr</title><body><main>"
            "<h1>尚无可展示资料</h1><p>本地资料库中没有可构建季度。</p>"
            "</main></body></html>\n"
        )
    latest = max((model.year, model.month) for model in models)
    destination = f"quarters/{latest[0]:04d}-{latest[1]:02d}/index.html"
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={destination}">'
        "<title>Bangumi Side B｜MyKr</title></head><body><main>"
        f'<p>正在进入最新季度：<a href="{destination}">'
        f"{latest[0]}-{latest[1]:02d}</a></p>"
        f"<p>{_profile_label(profile)} · {__version__}</p>"
        "</main></body></html>\n"
    )


def _validate_output(stage: Path, profile: BuildProfile) -> None:
    windows_path = re.compile(r"[A-Za-z]:[\\/](?!/)")
    allowed_external = re.compile(r"https://bgm\.tv/subject/\d+")
    text_files = tuple(
        path
        for path in stage.rglob("*")
        if path.is_file() and path.suffix in {".html", ".css", ".js"}
    )
    for path in text_files:
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(stage).as_posix()
        if windows_path.search(content):
            raise BuildError(f"generated output contains a local path: {relative}")
        if any(value in content for value in ("api.bgm.tv", "XMLHttpRequest")):
            raise BuildError("generated output contains a runtime data request")
        external_urls = re.findall(r"https?://[^\"'\s)<]+", content)
        if any(not allowed_external.fullmatch(url) for url in external_urls):
            raise BuildError("generated output contains a forbidden external resource")
        if re.fullmatch(r"site\.[0-9a-f]{12}\.js", path.name) and "fetch(" in content:
            raise BuildError("archive interaction JavaScript must stay offline")
    for html in stage.rglob("*.html"):
        content = html.read_text(encoding="utf-8")
        if ".sqlite3" in content:
            raise BuildError("generated HTML contains a database filename")
        if "javascript:" in content.lower():
            raise BuildError("generated HTML contains unsafe links")
        for href in re.findall(r"(?:href|src)=\"([^\"]+)\"", content):
            if href.startswith("#"):
                continue
            if href.startswith(("https://", "http://")):
                if not allowed_external.fullmatch(href):
                    raise BuildError("generated HTML contains an external resource")
                continue
            target = href.split("?", 1)[0].split("#", 1)[0]
            candidate = (html.parent / target).resolve()
            if not candidate.is_relative_to(stage.resolve()) or not candidate.is_file():
                raise BuildError("generated HTML contains a broken internal link")
    if profile.name == "pages":
        assert_pages_media_policy(stage)


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _tree_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file())
