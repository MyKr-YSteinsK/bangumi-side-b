"""Behavior checks for the unified-site PWA and verified cache core."""

from __future__ import annotations

import functools
import http.server
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

from tests.test_site_builder import _build_fixture


@pytest.fixture
def pwa_site(tmp_path: Path) -> Path:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    served = tmp_path / "served" / "bangumi-side-b"
    shutil.copytree(tmp_path / "dist" / "site", served)
    return served


@pytest.fixture
def pwa_server(pwa_site: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(pwa_site.parent),
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def chromium() -> Iterator[Browser]:
    with sync_playwright() as runner:
        browser = runner.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def test_service_worker_controls_pages_prefix_and_serves_shell_offline(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    scope = page.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.scope)"
    )
    assert scope == f"{pwa_server}/"

    shell_count = page.evaluate(
        "fetch('../data/pwa-shell.json').then((response) => response.json())"
        ".then((manifest) => manifest.resources.length)"
    )
    content_count = page.evaluate(
        "caches.open('bsb-content-v1').then((cache) => cache.keys())"
        ".then((keys) => keys.length)"
    )
    assert content_count == shell_count
    assert page.locator('link[rel="manifest"]').get_attribute("href") == (
        "../manifest.webmanifest"
    )

    context.set_offline(True)
    page.reload()
    assert page.get_by_role("heading", name="设置").is_visible()
    page.goto(f"{pwa_server}/archive/index.html")
    assert page.get_by_role("heading", name="播出档案").is_visible()
    context.close()


def test_shell_hash_failure_never_activates_or_writes_active_metadata(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    css = pwa_site / "assets" / "app.css"
    css.write_bytes(css.read_bytes() + b"\ncorrupt-after-build")
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_timeout(1500)
    assert page.evaluate("navigator.serviceWorker.controller") is None
    active_shell = page.evaluate(
        "caches.open('bsb-meta-v1').then((cache) => "
        "cache.match(new URL('../__bsb_meta__/shell.json', location.href)))"
        ".then((response) => Boolean(response))"
    )
    assert active_shell is False
    context.close()


def test_active_quarter_uses_content_identity_for_offline_navigation_and_cover(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    page.evaluate(
        """
        async (manifest) => {
          const content = await caches.open("bsb-content-v1");
          for (const resource of manifest.resources) {
            const response = await fetch(new URL(`../${resource.url}`, location.href));
            await content.put(
              new Request(new URL(
                `../__bsb_content__/${resource.content_hash}`,
                location.href,
              )),
              response,
            );
          }
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json",
              location.href,
            )),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "COMPLETE",
              active: manifest,
              staging: null,
            })),
          );
        }
        """,
        manifest,
    )
    cover = next(
        item for item in manifest["resources"] if item["url"] == "covers/101.webp"
    )
    context.set_offline(True)
    page.goto(f"{pwa_server}/2026-07/index.html")
    assert page.locator('[data-subject-id="101"] img').is_visible()
    response_size = page.evaluate(
        """
        ({ url, hash }) => fetch(`${url}?v=${hash}`)
          .then((response) => response.arrayBuffer())
          .then((buffer) => buffer.byteLength)
        """,
        {"url": f"{pwa_server}/{cover['url']}", "hash": cover["content_hash"]},
    )
    assert response_size == cover["size_bytes"]
    context.close()
