"""Public artifact locations shared by the builder and candidate checks."""

from __future__ import annotations

import re

OFFLINE_SCHEMA = 2
APP_ASSETS = ("assets/app.css", "assets/app.js", "assets/pwa.js")
_APP_ASSET_PATH = re.compile(r"assets/(app|pwa)\.([a-f0-9]{64})\.(css|js)")
ROOT_PAGE = "index.html"
ARCHIVE_PAGE = "archive/index.html"
SETTINGS_PAGE = "settings/index.html"
SHELL_PAGES = (ROOT_PAGE, ARCHIVE_PAGE, SETTINGS_PAGE)
ARCHIVE_INDEX = "data/archive-index.json"
SHELL_MANIFEST = "data/pwa-shell-v2.json"
OFFLINE_DIRECTORY = "data/offline-v2"
WEB_APP_MANIFEST = "manifest.webmanifest"
SERVICE_WORKER = "sw-v2.js"

PWA_SOURCE_PATHS = {
    "assets/app.css": "static/css/site.css",
    "assets/app.js": "static/js/app.js",
    "assets/pwa.js": "static/js/pwa.js",
    WEB_APP_MANIFEST: "static/pwa/manifest.webmanifest",
    "icons/pwa-192.png": "static/icons/pwa-192.png",
    "icons/pwa-512.png": "static/icons/pwa-512.png",
    "icons/pwa-maskable-512.png": "static/icons/pwa-maskable-512.png",
    "icons/favicon.svg": "static/icons/favicon.svg",
    SERVICE_WORKER: "static/pwa/sw.js",
}
SHELL_STATIC_PATHS = tuple(
    path for path in PWA_SOURCE_PATHS if path not in (*APP_ASSETS, SERVICE_WORKER)
)
CORE_SITE_PATHS = (
    *SHELL_PAGES,
    WEB_APP_MANIFEST,
    SERVICE_WORKER,
    ARCHIVE_INDEX,
    SHELL_MANIFEST,
)
REQUIRED_BUILD_PATHS = frozenset((*CORE_SITE_PATHS, *SHELL_STATIC_PATHS))


def app_asset_path(logical_path: str, content_hash: str) -> str:
    stem, extension = logical_path.rsplit(".", 1)
    return f"{stem}.{content_hash}.{extension}"


def app_asset_identity(path: str) -> tuple[str, str] | None:
    """Return the logical asset and exact SHA-256 embedded in its public path."""
    match = _APP_ASSET_PATH.fullmatch(path)
    if match:
        stem, digest, extension = match.groups()
        logical = f"assets/{stem}.{extension}"
        if logical in APP_ASSETS:
            return logical, digest
    return None


def shell_resource_paths(app_assets: tuple[str, ...]) -> tuple[str, ...]:
    return (*SHELL_PAGES, *app_assets, *SHELL_STATIC_PATHS)


def quarter_paths(label: str) -> tuple[str, str, str]:
    """Return the public page, data and offline manifest of one quarter."""
    return (
        f"{label}/index.html",
        f"data/quarters/{label}.json",
        f"{OFFLINE_DIRECTORY}/{label}.json",
    )


def year_catalog_path(year: int) -> str:
    return f"data/catalog/{year:04d}.json"


def original_cover_path(subject_id: int) -> str:
    return f"covers/{subject_id}.webp"
