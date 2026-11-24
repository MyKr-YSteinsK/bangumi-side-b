"""Validate planned artifacts, materialized bytes, and retained file metadata."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Callable, Collection, Mapping
from pathlib import Path

from bgm_side_b.build.cover_preview import (
    PREVIEW_PATH,
    cover_preview_path,
    validate_preview_image,
)
from bgm_side_b.build.errors import BuildError
from bgm_side_b.build.site_manifests import offline_revision, shell_revision
from bgm_side_b.build.site_paths import (
    APP_ASSETS,
    ARCHIVE_INDEX,
    REQUIRED_BUILD_PATHS,
    SHELL_MANIFEST,
    SHELL_RESOURCE_PATHS,
    WEB_APP_MANIFEST,
    quarter_paths,
)
from bgm_side_b.build.site_projection import ArchiveIndexProjection, QuarterProjection
from bgm_side_b.build.writer import ArtifactPlan

_HREF_RE = re.compile(r'(?:href|src)="([^"]+)"')
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_QUARTER_LABEL = re.compile(r"[0-9]{4}-(?:01|04|07|10)")


def _validate_site_paths(paths: Collection[str]) -> None:
    missing = sorted(REQUIRED_BUILD_PATHS.difference(paths))
    if missing:
        raise BuildError(f"required site artifact is missing: {missing[0]}")
    if any(
        path.startswith(("subjects/", "episodes/", "characters/", "persons/"))
        for path in paths
    ):
        raise BuildError("forbidden detail or entity artifact exists")


def validate_plan(
    plan: ArtifactPlan,
    quarters: tuple[QuarterProjection, ...],
    archive: ArchiveIndexProjection,
    excluded_subject_ids: frozenset[int],
) -> None:
    _validate_site_paths(plan.specs)
    archive_labels = {
        str(item.get("quarter")) for item in archive.quarters if isinstance(item, dict)
    }
    for quarter in quarters:
        if quarter.quarter not in archive_labels:
            raise BuildError(
                f"quarter is not listed in archive index: {quarter.quarter}"
            )
        if any(
            item.subject_id in excluded_subject_ids
            for group in (
                quarter.tv_premiere,
                quarter.tv_continuing,
                quarter.movie_premiere,
            )
            for item in group
        ):
            raise BuildError("blacklisted subject appears in site output")
        if any(item.appearance_kind == "continuing" for item in quarter.movie_premiere):
            raise BuildError(f"movie continuing appearance in {quarter.quarter}")


def validate_full_site(
    mapping: Mapping[str, bytes], excluded_subject_ids: frozenset[int]
) -> None:
    """Validate a fully materialized tree against its actual bytes."""
    _validate_site_paths(mapping)
    archive = _load_json(mapping, ARCHIVE_INDEX)
    _validate_web_app_manifest(_load_json(mapping, WEB_APP_MANIFEST))
    _validate_pwa_shell(mapping, None, None)
    quarter_labels = {
        item["quarter"]
        for item in archive.get("quarters", [])
        if isinstance(item, dict)
    }
    for path, content in mapping.items():
        if PREVIEW_PATH.fullmatch(path):
            validate_preview_image(content)
        if path.startswith("data/quarters/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            if payload.get("quarter") not in quarter_labels:
                raise BuildError("quarter data is not listed in archive index")
            _validate_quarter_items(payload, mapping.__contains__, excluded_subject_ids)
    for path in mapping:
        if path.startswith("data/offline/") and path.endswith(".json"):
            _validate_offline_manifest(path, mapping, None, None)
    for path, content in mapping.items():
        if path.endswith(".html"):
            _validate_html_links(path, content, mapping.__contains__)


def validate_dirty_artifacts(
    mapping: Mapping[str, bytes],
    plan: ArtifactPlan,
    site: Path,
    excluded_subject_ids: frozenset[int],
) -> None:
    """Check newly materialized bytes against the plan and their dependencies."""

    def has_path(relative: str) -> bool:
        return relative in plan.specs or (site / relative).is_file()

    for path, content in mapping.items():
        spec = plan.specs.get(path)
        if spec is None:
            raise BuildError(f"artifact is not in the current plan: {path}")
        if spec.kind in {"cover", "cover-preview"}:
            if spec.content_hash != hashlib.sha256(content).hexdigest():
                raise BuildError(f"cover artifact hash is invalid: {path}")
            if spec.size_bytes is not None and spec.size_bytes != len(content):
                raise BuildError(f"cover artifact size is invalid: {path}")
            if spec.kind == "cover-preview":
                validate_preview_image(content)
        if path.endswith(".html"):
            _validate_html_links(path, content, has_path)
        if path.startswith("data/quarters/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            if payload.get("quarter") != Path(path).stem:
                raise BuildError(f"quarter payload label is invalid: {path}")
            movie = payload.get("movie")
            if isinstance(movie, dict) and movie.get("continuing"):
                raise BuildError(f"movie continuing appearance in {path}")
            _validate_quarter_items(payload, has_path, excluded_subject_ids)
        if path.startswith("data/catalog/") and path.endswith(".json"):
            _validate_year_catalog(path, _load_json(mapping, path), has_path)
        if path == ARCHIVE_INDEX:
            _validate_archive_index(_load_json(mapping, path), has_path)
        if path.startswith("data/offline/") and path.endswith(".json"):
            _validate_offline_manifest(path, mapping, plan, site)
        if path == WEB_APP_MANIFEST:
            _validate_web_app_manifest(_load_json(mapping, path))
        if path == SHELL_MANIFEST:
            _validate_pwa_shell(mapping, plan, site)


def validate_scoped_site(
    site: Path, plan: ArtifactPlan, excluded_subject_ids: frozenset[int]
) -> None:
    """Check the written paths by metadata and validate generated sources."""
    source_bytes = {
        path: spec.source
        for path, spec in plan.specs.items()
        if isinstance(spec.source, bytes)
    }
    validate_dirty_artifacts(source_bytes, plan, site, excluded_subject_ids)
    for relative, spec in plan.specs.items():
        target = site / relative
        if not target.is_file():
            raise BuildError(f"planned artifact is missing: {relative}")
        if spec.size_bytes is not None:
            try:
                if target.stat().st_size != spec.size_bytes:
                    raise BuildError(f"planned artifact size is invalid: {relative}")
            except OSError as error:
                raise BuildError(
                    f"planned artifact cannot be inspected: {relative}"
                ) from error


def _load_json(mapping: Mapping[str, bytes], path: str) -> dict[str, object]:
    try:
        value = json.loads(mapping[path].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"invalid JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise BuildError(f"JSON artifact must be an object: {path}")
    return value


def _validate_html_links(
    path: str, content: bytes, has_path: Callable[[str], bool]
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildError(f"generated HTML is not UTF-8: {path}") from error
    for href in _HREF_RE.findall(text):
        if href.startswith(("https://bgm.tv/subject/", "#", "mailto:")):
            continue
        target = href.split("?", 1)[0].split("#", 1)[0]
        if not target or target.startswith("/"):
            raise BuildError("site HTML contains an unsafe absolute URL")
        normalized = posixpath.normpath((Path(path).parent / Path(target)).as_posix())
        if not has_path("index.html" if normalized == "." else normalized):
            raise BuildError(f"site HTML references missing artifact: {target}")


def quarter_items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    for group in (payload.get("tv"), payload.get("movie")):
        if not isinstance(group, dict):
            continue
        for records in group.values():
            if isinstance(records, list):
                values.extend(item for item in records if isinstance(item, dict))
    return tuple(values)


def _validate_quarter_items(
    payload: Mapping[str, object],
    has_path: Callable[[str], bool],
    excluded_subject_ids: frozenset[int],
) -> None:
    for item in quarter_items(payload):
        if item.get("subject_id") in excluded_subject_ids:
            raise BuildError("blacklisted subject appears in site output")
        cover = item.get("cover_url")
        if isinstance(cover, str):
            relative = cover.split("?", 1)[0]
            if not has_path(relative):
                raise BuildError(f"cover artifact is missing: {relative}")


def _validate_year_catalog(
    path: str, payload: Mapping[str, object], has_path: Callable[[str], bool]
) -> None:
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise BuildError(f"year catalog records are invalid: {path}")
    for item in records:
        if not isinstance(item, dict):
            raise BuildError(f"year catalog record is invalid: {path}")
        quarter = item.get("quarter")
        if isinstance(quarter, str) and not has_path(quarter_paths(quarter)[1]):
            raise BuildError(f"year catalog references missing quarter: {quarter}")


def _validate_archive_index(
    payload: Mapping[str, object], has_path: Callable[[str], bool]
) -> None:
    quarters = payload.get("quarters", [])
    if not isinstance(quarters, list):
        raise BuildError("archive index quarters are invalid")
    for item in quarters:
        if not isinstance(item, dict) or not isinstance(item.get("quarter"), str):
            raise BuildError("archive index entry is invalid")
        if not has_path(quarter_paths(item["quarter"])[1]):
            raise BuildError(
                f"archive index references missing quarter: {item['quarter']}"
            )


def quarter_cover_dependencies(
    label: str, page: bytes, quarter: Mapping[str, object]
) -> set[str]:
    """Derive cover requirements using the rule embedded in the actual page."""
    try:
        text = page.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildError("offline page is not UTF-8") from error
    covers = tuple(
        cover
        for item in quarter_items(quarter)
        if isinstance((cover := item.get("cover_url")), str)
    )
    required = {cover.split("?", 1)[0] for cover in covers}
    rule = re.search(r'data-cover-preview-rule="([0-9a-f]{64})"', text)
    expected_previews = (
        {cover_preview_path(cover, rule=rule[1]) for cover in covers} if rule else set()
    )
    for reference in _HREF_RE.findall(text):
        path = posixpath.normpath(f"{label}/{reference.split('?', 1)[0]}")
        if not path.startswith("covers/previews/"):
            continue
        if PREVIEW_PATH.fullmatch(path) is None or path not in expected_previews:
            raise BuildError("preview dependency does not match the quarter source")
        required.add(path)
    return required


def _validate_offline_manifest(
    path: str,
    mapping: Mapping[str, bytes],
    plan: ArtifactPlan | None,
    site: Path | None,
) -> None:
    payload = _load_json(mapping, path)
    label = payload.get("quarter")
    if not isinstance(label, str) or _QUARTER_LABEL.fullmatch(label) is None:
        raise BuildError("offline manifest quarter is invalid")
    page_path, data_path, manifest_path = quarter_paths(label)
    if path != manifest_path:
        raise BuildError("offline manifest quarter is invalid")
    data_revision = payload.get("data_revision")
    if not isinstance(data_revision, str) or _HEX_64.fullmatch(data_revision) is None:
        raise BuildError("offline manifest data revision is invalid")
    data = mapping.get(data_path)
    if data is None:
        if site is None or not (site / data_path).is_file():
            raise BuildError(f"offline manifest data payload is missing: {label}")
        try:
            data = (site / data_path).read_bytes()
        except OSError as error:
            raise BuildError(
                f"offline manifest data payload is unreadable: {label}"
            ) from error
    if hashlib.sha256(data).hexdigest() != data_revision:
        raise BuildError(f"offline manifest data revision is invalid: {label}")
    page = mapping.get(page_path)
    if page is None and site is not None:
        try:
            page = (site / page_path).read_bytes()
        except OSError as error:
            raise BuildError("offline manifest page is unavailable") from error
    if not page:
        raise BuildError("offline page dependency is missing")
    quarter = _load_json({data_path: data}, data_path)
    required = {page_path, data_path, *APP_ASSETS}
    required.update(quarter_cover_dependencies(label, page, quarter))
    resources = _manifest_entries(payload.get("resources"), "offline")
    if not required.issubset(entry["url"] for entry in resources):
        raise BuildError("offline manifest omits a page or cover dependency")
    _validate_resource_metadata(resources, mapping, plan, site, name="offline")
    if payload.get("revision") != offline_revision(label, data_revision, resources):
        raise BuildError(f"offline manifest revision is invalid: {label}")


def _validate_web_app_manifest(payload: Mapping[str, object]) -> None:
    for key in ("id", "start_url", "scope"):
        if payload.get(key) != "./":
            raise BuildError(f"web app manifest {key} must be relative to site scope")
    if payload.get("display") != "standalone":
        raise BuildError("web app manifest display is invalid")
    icons = payload.get("icons")
    if not isinstance(icons, list) or len(icons) < 3:
        raise BuildError("web app manifest icons are invalid")
    if not any(
        isinstance(icon, dict) and icon.get("purpose") == "maskable" for icon in icons
    ):
        raise BuildError("web app manifest maskable icon is missing")
    for icon in icons:
        if not isinstance(icon, dict):
            raise BuildError("web app manifest icon is invalid")
        source = icon.get("src")
        if not isinstance(source, str) or source.startswith(("/", "http:", "https:")):
            raise BuildError("web app manifest icon URL is unsafe")


def _validate_pwa_shell(
    mapping: Mapping[str, bytes], plan: ArtifactPlan | None, site: Path | None
) -> None:
    payload = _load_json(mapping, SHELL_MANIFEST)
    if payload.get("schema") != 1:
        raise BuildError("PWA shell manifest is invalid")
    resources = _manifest_entries(payload.get("resources"), "PWA shell")
    if tuple(entry["url"] for entry in resources) != SHELL_RESOURCE_PATHS:
        raise BuildError("PWA shell resources are invalid")
    if payload.get("revision") != shell_revision(resources):
        raise BuildError("PWA shell revision is invalid")
    _validate_resource_metadata(resources, mapping, plan, site, name="PWA shell")


def _manifest_entries(value: object, name: str) -> list[dict]:
    if not isinstance(value, list):
        raise BuildError(f"{name} manifest resources are invalid")
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise BuildError(f"{name} resource entry is invalid")
        relative = entry.get("url")
        if (
            not isinstance(relative, str)
            or any(char in relative for char in "\\?#:%\x00")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
        ):
            raise BuildError(f"{name} manifest contains a non-local URL")
        if relative in seen:
            raise BuildError(f"{name} manifest repeats a resource: {relative}")
        seen.add(relative)
    return value


def _validate_resource_metadata(
    resources: list[dict],
    mapping: Mapping[str, bytes],
    plan: ArtifactPlan | None,
    site: Path | None,
    *,
    name: str,
) -> None:
    for entry in resources:
        relative = entry["url"]
        expected_hash = expected_size = None
        if relative in mapping:
            content = mapping[relative]
            expected_hash, expected_size = (
                hashlib.sha256(content).hexdigest(),
                len(content),
            )
        else:
            spec = plan.specs.get(relative) if plan is not None else None
            if spec is not None:
                expected_hash, expected_size = spec.content_hash, spec.size_bytes
            elif site is None or not (site / relative).is_file():
                raise BuildError(f"{name} resource is missing: {relative}")
        digest = entry.get("content_hash")
        size = entry.get("size_bytes")
        if (
            not isinstance(digest, str)
            or _HEX_64.fullmatch(digest) is None
            or (expected_hash is not None and digest != expected_hash)
        ):
            raise BuildError(f"{name} resource hash is invalid: {relative}")
        if (
            type(size) is not int
            or size < 0
            or (expected_size is not None and size != expected_size)
        ):
            raise BuildError(f"{name} resource size is invalid: {relative}")
