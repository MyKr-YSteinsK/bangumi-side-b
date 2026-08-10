"""Offline orchestration for the single deterministic ``dist/site`` tree."""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from bgm_side_b.build.fingerprint import (
    BuildState,
    DirtySet,
    assign_fingerprints,
    derive_dirty_set,
    read_build_state,
    shared_fingerprint,
)
from bgm_side_b.build.site_projection import (
    ArchiveFacts,
    ArchiveFactsReader,
    ArchiveIndexProjection,
    ProjectionError,
    QuarterProjection,
    SubjectProjection,
    YearCatalogProjection,
    json_bytes,
    project_archive_index,
    project_offline_manifest,
    project_quarter,
    project_year,
)
from bgm_side_b.build.writer import (
    BuildBlockedError,
    IncrementalSiteWriter,
    PatchResult,
    SiteWriteError,
)
from bgm_side_b.config import TagRules
from bgm_side_b.database import Database
from bgm_side_b.domain import Quarter
from bgm_side_b.progress import NullProgressReporter, ProgressReporter


class BuildError(RuntimeError):
    """Raised when a complete offline site cannot be produced safely."""


class BuildBlocked(BuildError):
    """Raised when a filesystem lock prevents an atomic site patch."""


APP_CSS_FALLBACK = """
:root { font-family: system-ui, sans-serif; color: #17201d; background: #f5f1e8; }
body { margin: 0 auto; max-width: 1100px; padding: 2rem; }
a { color: #682337; }
.subject-card {
  border: 1px solid #c9c6bb;
  background: #fffdf8;
  padding: 1rem;
  margin: .5rem 0;
}
.subject-card[hidden] { display: none; }
[data-media-mode="movie"] { border-color: #c95c32; }
nav { display: flex; gap: 1rem; flex-wrap: wrap; }
button { padding: .4rem .7rem; }
#detail-mount { border-top: 1px solid #c9c6bb; margin-top: 1rem; padding-top: 1rem; }
"""
APP_JS = """(() => {
  const root = document.querySelector('[data-quarter]');
  if (!root) return;
  const quarter = root.dataset.quarter;
  const mount = document.querySelector('#detail-mount');
  const dataUrl = `../data/quarters/${quarter}.json`;
  let payload = null;
  fetch(dataUrl)
    .then((response) => response.json())
    .then((value) => { payload = value; });
  document.querySelectorAll('[data-subject-id]').forEach((card) => {
    card.addEventListener('click', () => {
      if (!payload || !mount) return;
      const id = Number(card.dataset.subjectId);
      const groups = [
        payload.tv.premiere,
        payload.tv.continuing,
        payload.movie.premiere,
      ];
      const item = groups.flat().find((entry) => entry.subject_id === id);
      if (!item) return;
      mount.hidden = false;
      mount.textContent = item.display_summary || item.preferred_title;
    });
  });
})();
"""
_HREF_RE = re.compile(r"(?:href|src)=\"([^\"]+)\"")


class UnifiedSiteBuilder:
    """Build the one public site from the clean SQLite fact store."""

    def __init__(
        self,
        root: Path,
        database: Database,
        tag_rules: TagRules,
        *,
        workspace_directory: Path | None = None,
        site_directory: Path | None = None,
        reports_directory: Path | None = None,
        excluded_subject_ids: frozenset[int] = frozenset(),
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.root = root.resolve()
        self.database = database
        self.tag_rules = tag_rules
        self.workspace_directory = (
            workspace_directory or self.root / "workspace"
        ).resolve()
        self.site_directory = (site_directory or self.root / "dist" / "site").resolve()
        self.reports_directory = (
            reports_directory or self.workspace_directory / "reports"
        ).resolve()
        self.excluded_subject_ids = excluded_subject_ids
        self.reporter = reporter or NullProgressReporter()

    @property
    def state_path(self) -> Path:
        return self.workspace_directory / "build-state.json"

    def build(self, quarter: Quarter | None = None) -> SiteBuildRun:
        """Build all managed quarters, optionally validating a requested quarter."""
        started = monotonic()
        scope_label = "all" if quarter is None else _quarter_label(quarter)
        self.reporter.start(
            stage="scope",
            message="building unified static site",
            current=scope_label,
        )
        try:
            facts = self._read_facts()
            previous = read_build_state(self.state_path)
            available = tuple(_quarter_label(item) for item in facts.by_quarter)
            if quarter is not None and _quarter_label(quarter) not in available:
                if previous is None or _quarter_label(quarter) not in previous.quarters:
                    raise BuildError(
                        f"quarter is not managed: {_quarter_label(quarter)}"
                    )
            incomplete = tuple(
                _quarter_label(item)
                for item, state in facts.sync_states
                if state.facts_status != "complete" and item in facts.by_quarter
            )
            # A partial facts commit must never replace a previously good site.
            # If one exists, leave the complete site untouched; a later successful
            # sync will rerun the normal incremental path.
            if incomplete and previous is not None:
                return self._write_report(
                    scope_label,
                    DirtySet((), tuple(sorted(incomplete)), (), False, False),
                    PatchResult((), (), tuple(sorted(previous.artifacts)), ()),
                    (),
                    ("facts incomplete; retained last-known-good site",),
                    started,
                    previous,
                )
            quarter_projections = self._project_quarters(facts, incomplete)
            years = self._project_years(quarter_projections)
            archive = project_archive_index(quarter_projections)
            css, js = self._shared_assets()
            shared = shared_fingerprint(
                stylesheet=css,
                script=js,
                tag_rules=self.tag_rules,
                excluded_subject_ids=self.excluded_subject_ids,
            )
            quarter_projections, years, archive, current = assign_fingerprints(
                quarter_projections,
                years,
                archive,
                shared=shared,
            )
            dirty = derive_dirty_set(
                previous,
                current,
                available_quarters=tuple(item.quarter for item in quarter_projections),
                requested_quarters=(
                    None if quarter is None else (_quarter_label(quarter),)
                ),
            )
            desired = self._render_site(
                facts,
                quarter_projections,
                years,
                archive,
                css,
                js,
            )
            writer = IncrementalSiteWriter(
                self.site_directory,
                self.workspace_directory,
            )
            result = writer.apply(
                desired,
                current,
                validate_staged=self._validate_desired,
                validate_final=self._validate_final,
            )
            warnings = tuple(
                warning
                for item in quarter_projections
                for warning in item.warnings
            )
            return self._write_report(
                scope_label,
                dirty,
                result,
                warnings,
                (),
                started,
                current,
            )
        except BuildBlockedError as error:
            raise BuildBlocked(str(error)) from error
        except (ProjectionError, SiteWriteError) as error:
            raise BuildError(str(error)) from error

    def _read_facts(self) -> ArchiveFacts:
        self.reporter.stage(stage="database", message="reading SQLite facts")
        try:
            return ArchiveFactsReader(
                self.database, self.workspace_directory
            ).read(self.excluded_subject_ids)
        except ProjectionError:
            raise

    def _project_quarters(
        self, facts: ArchiveFacts, incomplete: tuple[str, ...]
    ) -> tuple[QuarterProjection, ...]:
        blocked = set(incomplete)
        projections: list[QuarterProjection] = []
        for quarter in sorted(facts.by_quarter):
            label = _quarter_label(quarter)
            if label in blocked:
                continue
            self.reporter.progress(
                stage="projection", current=label, completed=len(projections) + 1
            )
            projections.append(
                project_quarter(
                    facts,
                    quarter,
                    self.tag_rules,
                    self.workspace_directory,
                )
            )
        return tuple(projections)

    @staticmethod
    def _project_years(
        quarters: tuple[QuarterProjection, ...],
    ) -> tuple[YearCatalogProjection, ...]:
        grouped: defaultdict[int, list[QuarterProjection]] = defaultdict(list)
        for quarter in quarters:
            grouped[int(quarter.quarter[:4])].append(quarter)
        return tuple(
            project_year(year, tuple(values))
            for year, values in sorted(grouped.items())
        )

    def _shared_assets(self) -> tuple[bytes, bytes]:
        css_path = self.root / "static" / "css" / "site.css"
        css = css_path.read_bytes() if css_path.is_file() else APP_CSS_FALLBACK.encode()
        js_path = self.root / "static" / "js" / "app.js"
        js = js_path.read_bytes() if js_path.is_file() else APP_JS.encode()
        return css, js

    def _render_site(
        self,
        facts: ArchiveFacts,
        quarters: tuple[QuarterProjection, ...],
        years: tuple[YearCatalogProjection, ...],
        archive: ArchiveIndexProjection,
        css: bytes,
        js: bytes,
    ) -> dict[str, bytes]:
        desired: dict[str, bytes] = {
            "assets/app.css": css,
            "assets/app.js": js,
            "index.html": _root_html(archive),
            "archive/index.html": _archive_html(archive),
            "settings/index.html": _settings_html(),
            "data/archive-index.json": json_bytes(archive.to_dict()),
        }
        for year in years:
            desired[f"data/catalog/{year.year:04d}.json"] = json_bytes(year.to_dict())
        subject_by_id = {subject.subject_id: subject for subject in facts.subjects}
        for quarter in quarters:
            label = quarter.quarter
            desired[f"{label}/index.html"] = _quarter_html(quarter)
            desired[f"data/quarters/{label}.json"] = json_bytes(quarter.to_dict())
            for item in (
                *quarter.tv_premiere,
                *quarter.tv_continuing,
                *quarter.movie_premiere,
            ):
                if item.cover_hash is None:
                    continue
                source = subject_by_id[item.subject_id].cover
                if source is None:
                    continue
                if not source.source_path.is_file():
                    continue
                desired[
                    f"covers/{item.subject_id}.webp"
                ] = source.source_path.read_bytes()
        for quarter in quarters:
            label = quarter.quarter
            manifest = project_offline_manifest(quarter, desired)
            desired[f"data/offline/{label}.json"] = json_bytes(manifest.to_dict())
        return desired

    def _validate_desired(self, desired: Mapping[str, bytes]) -> None:
        _validate_site_mapping(desired, self.excluded_subject_ids)

    def _validate_final(self, site: Path) -> None:
        mapping = {
            path.relative_to(site).as_posix(): path.read_bytes()
            for path in site.rglob("*")
            if path.is_file()
        }
        _validate_site_mapping(mapping, self.excluded_subject_ids)

    def _write_report(
        self,
        scope: str,
        dirty: DirtySet,
        result: PatchResult,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        started: float,
        state: BuildState | None = None,
    ) -> SiteBuildRun:
        self.reports_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.reports_directory / f"build-{scope}-{stamp}.json"
        report = {
            "scope": scope,
            "dirty_quarters": list(dirty.dirty_quarters),
            "skipped_quarters": list(dirty.skipped_quarters),
            "dirty_years": list(dirty.dirty_years),
            "archive_dirty": dirty.archive_dirty,
            "shared_dirty": dirty.shared_dirty,
            "written_files": list(result.written),
            "deleted_files": list(result.deleted),
            "reused_files": list(result.reused),
            "warnings": list(warnings),
            "errors": list(errors),
            "fingerprints": {
                "shared": state.shared if state else None,
                "quarters": dict(state.quarters) if state else {},
                "years": dict(state.years) if state else {},
                "archive": state.archive if state else None,
            },
            "duration_seconds": round(monotonic() - started, 3),
        }
        report_path.write_bytes(json_bytes(report))
        self.reporter.complete(
            stage="summary",
            message="unified static site build complete",
            counters={
                "written": len(result.written),
                "deleted": len(result.deleted),
                "skipped": len(dirty.skipped_quarters),
            },
        )
        return SiteBuildRun(scope, report_path, dirty, result, warnings, errors)


class SiteBuildRun:
    """Summary returned by one offline build invocation."""

    def __init__(
        self,
        scope: str,
        report_path: Path,
        dirty: DirtySet,
        patch: PatchResult,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> None:
        self.scope = scope
        self.report_path = report_path
        self.dirty = dirty
        self.patch = patch
        self.warnings = warnings
        self.errors = errors


def _validate_site_mapping(
    mapping: Mapping[str, bytes], excluded_subject_ids: frozenset[int]
) -> None:
    required = {
        "index.html",
        "archive/index.html",
        "settings/index.html",
        "assets/app.css",
        "assets/app.js",
        "data/archive-index.json",
    }
    missing = sorted(required - set(mapping))
    if missing:
        raise BuildError(f"required site artifact is missing: {missing[0]}")
    if any(
        item.startswith(("subjects/", "episodes/", "characters/", "persons/"))
        for item in mapping
    ):
        raise BuildError("forbidden detail or entity artifact exists")
    archive = _load_json(mapping, "data/archive-index.json")
    quarter_labels = {
        item["quarter"]
        for item in archive.get("quarters", [])
        if isinstance(item, dict)
    }
    for path, content in mapping.items():
        if path.startswith("data/quarters/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            label = payload.get("quarter")
            if label not in quarter_labels:
                raise BuildError("quarter data is not listed in archive index")
            for item in _quarter_items(payload):
                subject_id = item.get("subject_id")
                if subject_id in excluded_subject_ids:
                    raise BuildError("blacklisted subject appears in site output")
                cover = item.get("cover_url")
                if cover:
                    cover_path = str(cover).split("?", 1)[0]
                    if cover_path not in mapping:
                        raise BuildError(f"cover artifact is missing: {cover_path}")
    for path, content in mapping.items():
        if path.startswith("data/offline/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            for resource in payload.get("resources", []):
                if not isinstance(resource, dict):
                    raise BuildError("offline resource entry is invalid")
                url = resource.get("url")
                if not isinstance(url, str) or url.startswith(("http:", "https:", "/")):
                    raise BuildError("offline manifest contains a non-local URL")
                if url not in mapping:
                    raise BuildError(f"offline resource is missing: {url}")
                expected_hash = resource.get("content_hash")
                expected_size = resource.get("size_bytes")
                if expected_hash != hashlib.sha256(mapping[url]).hexdigest():
                    raise BuildError(f"offline resource hash is invalid: {url}")
                if expected_size != len(mapping[url]):
                    raise BuildError(f"offline resource size is invalid: {url}")
    for path, content in mapping.items():
        if path.endswith(".html"):
            for href in _HREF_RE.findall(content.decode("utf-8")):
                if href.startswith(("https://bgm.tv/subject/", "#", "mailto:")):
                    continue
                target = href.split("?", 1)[0].split("#", 1)[0]
                if not target or target.startswith("/"):
                    raise BuildError("site HTML contains an unsafe absolute URL")
                candidate = Path(path).parent / Path(target)
                normalized = posixpath.normpath(candidate.as_posix())
                if normalized == ".":
                    normalized = "index.html"
                if normalized not in mapping:
                    raise BuildError(f"site HTML references missing artifact: {target}")


def _load_json(mapping: Mapping[str, bytes], path: str) -> dict[str, object]:
    try:
        value = json.loads(mapping[path].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"invalid JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise BuildError(f"JSON artifact must be an object: {path}")
    return value


def _quarter_items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    for group in (payload.get("tv"), payload.get("movie")):
        if not isinstance(group, dict):
            continue
        for records in group.values():
            if isinstance(records, list):
                values.extend(item for item in records if isinstance(item, dict))
    return tuple(values)


def _root_html(archive: ArchiveIndexProjection) -> bytes:
    latest = archive.latest_quarter
    if latest:
        body = (
            f'<meta http-equiv="refresh" content="0;url={latest}/index.html">'
            f'<p><a href="{latest}/index.html">Open latest quarter {latest}</a></p>'
        )
    else:
        body = "<p data-empty-archive>Archive is empty.</p>"
    return _page("Bangumi Side B", body, "assets/app.css", "assets/app.js")


def _archive_html(archive: ArchiveIndexProjection) -> bytes:
    links = "".join(
        f'<li data-quarter="{html.escape(str(item["quarter"]))}">'
        f'<a href="../{html.escape(str(item["quarter"]))}/index.html">'
        f'{html.escape(str(item["quarter"]))}</a> '
        f'({item["count"]})</li>'
        for item in archive.quarters
    )
    body = f"<h1>Archive</h1><ul>{links or '<li>Empty archive</li>'}</ul>"
    return _page("Archive", body, "../assets/app.css", "../assets/app.js")


def _settings_html() -> bytes:
    return _page(
        "Settings",
        (
            "<h1>Settings</h1>"
            "<p>Static archive preview. Runtime data is same-origin only.</p>"
        ),
        "../assets/app.css",
        "../assets/app.js",
    )


def _quarter_html(quarter: QuarterProjection) -> bytes:
    sections = (
        ("tv", "premiere", "New TV", quarter.tv_premiere),
        ("tv", "continuing", "Continuing TV", quarter.tv_continuing),
        ("movie", "premiere", "Movies", quarter.movie_premiere),
    )
    rendered: list[str] = []
    for mode, kind, title, records in sections:
        cards = "".join(_subject_card(item) for item in records)
        rendered.append(
            f'<section data-media-mode="{mode}" data-section="{kind}" '
            f'data-quarter="{html.escape(quarter.quarter)}"><h2>{title}</h2>'
            f'<div>{cards or "<p>None</p>"}</div></section>'
        )
    body = (
        f'<header><nav><a href="../index.html">Home</a> '
        f'<a href="../archive/index.html">Archive</a> '
        f'<a href="../settings/index.html">Settings</a></nav>'
        f'<h1 data-quarter="{html.escape(quarter.quarter)}">'
        f'{html.escape(quarter.quarter)}</h1>'
        "</header>"
        '<div data-media-mode-controls><button data-mode="tv">TV</button>'
        '<button data-mode="movie">Movie</button></div>'
        f'<main data-quarter="{html.escape(quarter.quarter)}">'
        f'{"".join(rendered)}'
        '<aside id="detail-mount" hidden data-detail-mount></aside></main>'
    )
    return _page(
        f"Quarter {quarter.quarter}", body, "../assets/app.css", "../assets/app.js"
    )


def _subject_card(item: SubjectProjection) -> str:
    cover = ""
    if item.cover_url:
        cover = f'<img src="../{html.escape(item.cover_url.split("?", 1)[0])}" alt="">'
    tags = " ".join(html.escape(tag) for tag in item.allowed_tags[:2])
    return (
        f'<article class="subject-card" data-subject-id="{item.subject_id}" '
        f'data-appearance-kind="{item.appearance_kind}" '
        f'data-media-format="{item.media_format}"><div>{cover}</div>'
        f'<h3>{html.escape(item.preferred_title)}</h3>'
        f'<p>{html.escape(item.original_title or "")}</p>'
        f'<p data-tags>{tags}</p></article>'
    )


def _page(title: str, body: str, css_href: str, js_href: str) -> bytes:
    content = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f'<link rel="stylesheet" href="{css_href}"></head>'
        f"<body>{body}<script src=\"{js_href}\" defer></script></body></html>"
    )
    return content.encode("utf-8")


def _quarter_label(quarter: Quarter) -> str:
    return f"{quarter.year:04d}-{quarter.month:02d}"


__all__ = [
    "BuildBlocked",
    "BuildError",
    "SiteBuildRun",
    "UnifiedSiteBuilder",
]
