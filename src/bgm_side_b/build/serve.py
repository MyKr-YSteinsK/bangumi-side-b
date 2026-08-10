"""Localhost preview for the already-built ``dist/site`` tree."""

from __future__ import annotations

import posixpath
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class ServeError(RuntimeError):
    """Raised when a static preview cannot be started."""


DEFAULT_BASE_PATH = "/bangumi-side-b/"


class _PrefixedHandler(SimpleHTTPRequestHandler):
    """Strip the Pages project prefix before delegating to stdlib serving."""

    base_path = DEFAULT_BASE_PATH

    def translate_path(self, path: str) -> str:
        parsed = urlsplit(path)
        request_path = parsed.path
        prefix = self.base_path
        if request_path == prefix[:-1]:
            request_path = prefix
        if not request_path.startswith(prefix):
            return str(Path(self.directory) / "__missing__")
        relative = request_path[len(prefix) :]
        relative = posixpath.normpath(relative)
        if relative in {"", "."}:
            relative = "index.html"
        if relative.startswith("../") or relative == "..":
            return str(Path(self.directory) / "__missing__")
        return str(Path(self.directory) / Path(relative))

    def log_message(self, format: str, *args: object) -> None:
        # Preview requests are intentionally quiet; the CLI owns progress output.
        return None


def create_preview_server(
    site_directory: Path,
    *,
    port: int = 8000,
    base_path: str = DEFAULT_BASE_PATH,
) -> ThreadingHTTPServer:
    """Create a loopback-only server without reading SQLite or mutating files."""
    site = site_directory.resolve()
    if not site.is_dir():
        raise ServeError("dist/site does not exist; run bgmb build first")
    normalized = _normalize_base_path(base_path)
    class PrefixedStaticHandler(_PrefixedHandler):
        base_path = normalized

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(site), **kwargs)

    handler = PrefixedStaticHandler
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        raise ServeError("preview port is already in use") from error


def serve_site(
    site_directory: Path,
    *,
    port: int = 8000,
    base_path: str = DEFAULT_BASE_PATH,
) -> None:
    """Serve only the generated site until Ctrl-C."""
    server = create_preview_server(site_directory, port=port, base_path=base_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def _normalize_base_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    if not candidate.startswith("/"):
        raise ServeError("base path must start with '/'")
    candidate = "/" + candidate.strip("/") + "/"
    if ".." in candidate.split("/"):
        raise ServeError("base path must not escape the site")
    return candidate


__all__ = ["DEFAULT_BASE_PATH", "ServeError", "create_preview_server", "serve_site"]
