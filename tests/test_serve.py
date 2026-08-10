"""Loopback preview tests for the Pages project prefix."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from bgm_side_b.build.serve import ServeError, create_preview_server


def test_preview_serves_only_dist_site_under_project_prefix(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("root", encoding="utf-8")
    (site / "data.json").write_text("data", encoding="utf-8")
    server = create_preview_server(site, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/bangumi-side-b/"
        with urlopen(base, timeout=2) as response:
            assert response.read() == b"root"
        with urlopen(base + "data.json", timeout=2) as response:
            assert response.read() == b"data"
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_preview_reports_missing_site_and_invalid_base_path(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="dist/site does not exist"):
        create_preview_server(tmp_path / "missing", port=0)
    site = tmp_path / "site"
    site.mkdir()
    with pytest.raises(ServeError, match="base path"):
        create_preview_server(site, port=0, base_path="relative")
