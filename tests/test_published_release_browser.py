"""Chromium smoke coverage against the exact tree committed to gh-pages."""

from __future__ import annotations

import functools
import http.server
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

from bgm_side_b.release import workflow
from tests.release_fixture import create_release_project, git

_NEXT_SAFE_PORT = 18180


def _server(handler: object) -> http.server.ThreadingHTTPServer:
    global _NEXT_SAFE_PORT
    for _ in range(100):
        port = _NEXT_SAFE_PORT
        _NEXT_SAFE_PORT += 1
        try:
            return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
    raise RuntimeError("could not allocate a safe browser test port")


@pytest.fixture
def published_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    workflow.prepare_release(root)
    run = workflow.publish_prepared_release(root)
    assert run.published

    served = tmp_path / "served" / "bangumi-side-b"
    git(
        tmp_path,
        "-c",
        "core.autocrlf=false",
        "clone",
        "-q",
        "--branch",
        "gh-pages",
        str(remote),
        str(served),
    )
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(served.parent),
    )
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        shutil.rmtree(served.parent, ignore_errors=True)


def _wait_for_queue(page: object, completed: int) -> None:
    page.evaluate(
        """
        async (completed) => {
          for (let attempt = 0; attempt < 400; attempt += 1) {
            const queue = await window.BsbPwa.currentQueue();
            const finished = (queue.succeeded || queue.completed || []).length
              + (queue.failed || []).length;
            if (queue.state === "idle" && finished === completed) return;
            await new Promise((resolve) => setTimeout(resolve, 25));
          }
          throw new Error("published-tree queue did not finish");
        }
        """,
        completed,
    )


def test_published_tree_supports_prefixed_navigation_and_offline_quarter(
    published_site: str,
) -> None:
    with sync_playwright() as runner:
        browser: Browser = runner.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(10000)
        try:
            requests: list[str] = []
            page.on("request", lambda request: requests.append(request.url))
            page.goto(f"{published_site}/")
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            assert any(url.rstrip("/") == published_site for url in requests)
            scope = page.evaluate(
                "navigator.serviceWorker.ready.then("
                "(registration) => registration.scope)"
            )
            assert scope == f"{published_site}/"
            manifest = page.evaluate(
                "url => fetch(url).then((response) => response.json())",
                f"{published_site}/manifest.webmanifest",
            )
            assert manifest["scope"] == manifest["start_url"] == "./"

            page.goto(f"{published_site}/2026-07/index.html#bgm-101")
            page.wait_for_selector('[data-page="quarter"]')
            page.wait_for_selector('[data-detail-panel]:not([hidden])')
            page.goto(f"{published_site}/archive/index.html")
            page.wait_for_selector('[data-page="archive"]')
            page.goto(f"{published_site}/settings/index.html")
            page.wait_for_selector("[data-pwa-settings]")
            page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")

            page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
            _wait_for_queue(page, 1)
            state = page.evaluate(
                "async () => window.BsbPwa.getQuarterState('2026-07')"
            )
            assert state["status"] == "COMPLETE"

            context.set_offline(True)
            page.goto(f"{published_site}/2026-07/index.html#bgm-101")
            page.wait_for_selector('[data-page="quarter"]')
            page.wait_for_selector('[data-detail-panel]:not([hidden])')
            assert page.locator("[data-detail-panel]").is_visible()
        finally:
            context.close()
            browser.close()
