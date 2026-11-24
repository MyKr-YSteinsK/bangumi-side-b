"""Public artifact locations shared by the builder and candidate checks."""

from __future__ import annotations

APP_ASSETS = ("assets/app.css", "assets/app.js", "assets/pwa.js")
ROOT_PAGE = "index.html"
ARCHIVE_PAGE = "archive/index.html"
SETTINGS_PAGE = "settings/index.html"
SHELL_PAGES = (ROOT_PAGE, ARCHIVE_PAGE, SETTINGS_PAGE)
ARCHIVE_INDEX = "data/archive-index.json"
SHELL_MANIFEST = "data/pwa-shell.json"
WEB_APP_MANIFEST = "manifest.webmanifest"
SERVICE_WORKER = "sw.js"

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
SHELL_STATIC_PATHS = tuple(path for path in PWA_SOURCE_PATHS if path != SERVICE_WORKER)
SHELL_RESOURCE_PATHS = (*SHELL_PAGES, *SHELL_STATIC_PATHS)
CORE_SITE_PATHS = (
    *SHELL_PAGES,
    *APP_ASSETS,
    WEB_APP_MANIFEST,
    SERVICE_WORKER,
    ARCHIVE_INDEX,
    SHELL_MANIFEST,
)
REQUIRED_BUILD_PATHS = frozenset((*CORE_SITE_PATHS, *SHELL_RESOURCE_PATHS))


def quarter_paths(label: str) -> tuple[str, str, str]:
    """Return the public page, data and offline manifest of one quarter."""
    return (
        f"{label}/index.html",
        f"data/quarters/{label}.json",
        f"data/offline/{label}.json",
    )


def year_catalog_path(year: int) -> str:
    return f"data/catalog/{year:04d}.json"


def original_cover_path(subject_id: int) -> str:
    return f"covers/{subject_id}.webp"
