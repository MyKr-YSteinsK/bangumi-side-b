"""Loopback preview tests for the Pages project prefix."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

import bgm_side_b.build.serve as serve_module
from bgm_side_b.build.serve import (
    ServeError,
    create_preview_server,
    preview_url,
    serve_site,
)


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


def test_preview_url_uses_bound_port_and_normalized_base_path(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    server = create_preview_server(site, port=0, base_path="/preview/")
    try:
        assert preview_url(server, base_path="/preview/") == (
            f"http://127.0.0.1:{server.server_port}/preview/"
        )
    finally:
        server.server_close()


def test_serve_calls_ready_only_after_bind_and_cleans_up_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    site.mkdir()

    class FakeServer:
        server_port = 8123
        shutdown_called = False
        close_called = False

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def shutdown(self) -> None:
            self.shutdown_called = True

        def server_close(self) -> None:
            self.close_called = True

    server = FakeServer()
    monkeypatch.setattr(
        serve_module, "create_preview_server", lambda *args, **kwargs: server
    )
    ready: list[str] = []

    serve_site(site, port=8000, base_path="/preview/", ready_callback=ready.append)

    assert ready == ["http://127.0.0.1:8123/preview/"]
    assert server.shutdown_called and server.close_called


def test_serve_never_reports_ready_when_bind_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setattr(
        serve_module,
        "create_preview_server",
        lambda *args, **kwargs: (_ for _ in ()).throw(ServeError("occupied")),
    )
    ready: list[str] = []

    with pytest.raises(ServeError, match="occupied"):
        serve_site(site, ready_callback=ready.append)

    assert ready == []


def test_preview_rejects_an_occupied_port(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    first = create_preview_server(site, port=0)
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ServeError, match="already in use"):
            create_preview_server(site, port=first.server_port)
    finally:
        first.shutdown()
        first.server_close()
        thread.join(timeout=2)
