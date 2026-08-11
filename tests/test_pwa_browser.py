"""Behavior checks for the unified-site PWA and verified cache core."""

from __future__ import annotations

import functools
import gzip
import hashlib
import http.server
import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from bgm_side_b.repository import SubjectRepository
from tests.test_site_builder import _build_fixture


def _wait_for_queue(page: Page, completed: int) -> None:
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
          throw new Error("queue did not finish");
        }
        """,
        completed,
    )


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
def slow_pwa_server(pwa_site: Path) -> Iterator[str]:
    class SlowHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/sw.js"):
                time.sleep(7)
            super().do_GET()

    handler = functools.partial(SlowHandler, directory=str(pwa_site.parent))
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
def retry_pwa_server(pwa_site: Path) -> Iterator[tuple[str, list[int]]]:
    attempts = [0]

    class RetryHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/sw.js"):
                attempts[0] += 1
                if attempts[0] == 1:
                    self.send_error(503, "registration fixture failure")
                    return
            super().do_GET()

    handler = functools.partial(RetryHandler, directory=str(pwa_site.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bangumi-side-b", attempts
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def gzip_pwa_server(pwa_site: Path) -> Iterator[str]:
    class GzipHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/data/quarters/2026-07.json"):
                target = pwa_site / "data" / "quarters" / "2026-07.json"
                body = gzip.compress(target.read_bytes())
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    handler = functools.partial(GzipHandler, directory=str(pwa_site.parent))
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
def update_site(tmp_path: Path) -> tuple[object, object, Path, Path]:
    builder, database = _build_fixture(tmp_path)
    isolated_root = tmp_path / "project"
    shutil.copytree(Path(__file__).parents[1] / "static", isolated_root / "static")
    served = tmp_path / "served" / "bangumi-side-b"
    builder.root = isolated_root.resolve()
    builder.site_directory = served.resolve()
    builder.build()
    return builder, database, isolated_root, served


@pytest.fixture
def update_server(update_site: tuple[object, object, Path, Path]) -> Iterator[str]:
    served = update_site[3]
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(served.parent),
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


def test_service_worker_registration_failure_keeps_online_page_and_blocks_download(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function(
        "window.BsbPwa?.capabilityState() === 'registration-failed'"
    )
    assert page.get_by_role("heading", name="设置").is_visible()
    assert page.get_by_text("registration failed").is_visible()
    rejected = page.evaluate(
        "async () => {"
        "try { await window.BsbPwa.enqueue(['2026-07']); return false; }"
        "catch { return true; }}"
    )
    assert rejected
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert state["status"] == "NONE"
    context.close()


def test_persisted_queue_waits_for_service_worker_after_registration_failure(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate(
        """
        async () => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL("../__bsb_meta__/queue.json", location.href)),
            new Response(JSON.stringify({
              schema: 2,
              generation: "persisted-generation",
              state: "downloading",
              labels: ["2026-07"],
              current: null,
              succeeded: [],
              failed: [],
              errors: [],
            })),
          );
          await meta.put(
            new Request(new URL("../__bsb_meta__/quarters/2026-07.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "NONE",
              active: null,
              staging: null,
              error: null,
            })),
          );
        }
        """
    )
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.reload()
    page.wait_for_function(
        "window.BsbPwa?.capabilityState() === 'registration-failed'"
    )
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.state === 'waiting-service-worker')"
    )
    page.wait_for_timeout(250)
    assert not any("data/offline/2026-07.json" in url for url in requests)
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "NONE"
    context.close()


def test_slow_service_worker_activation_stays_registering_and_then_downloads(
    chromium: Browser,
    slow_pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{slow_pwa_server}/settings/index.html")
    page.wait_for_function(
        "window.BsbPwa?.capabilityState() === 'registering'"
    )
    page.wait_for_timeout(5200)
    assert page.evaluate("window.BsbPwa.capabilityState()") == "registering"
    page.wait_for_function(
        "window.BsbPwa.capabilityState() === 'ready'",
        timeout=15000,
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert page.evaluate("window.BsbPwa.capabilityState()") == "ready"
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"
    context.close()


def test_page_load_and_first_enqueue_share_one_registration_attempt(
    chromium: Browser,
    slow_pwa_server: str,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        """
        const nativeRegister = navigator.serviceWorker.register.bind(navigator.serviceWorker);
        window.__registerCalls = 0;
        Object.defineProperty(navigator.serviceWorker, "register", {
          configurable: true,
          value: (...args) => {
            window.__registerCalls += 1;
            return nativeRegister(...args);
          },
        });
        """
    )
    page = context.new_page()
    page.goto(f"{slow_pwa_server}/settings/index.html")
    page.wait_for_function("window.__registerCalls === 1")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert page.evaluate("window.__registerCalls") == 1
    context.close()


def test_failed_registration_can_retry_and_resume_queue(
    chromium: Browser,
    retry_pwa_server: tuple[str, list[int]],
) -> None:
    pwa_server, attempts = retry_pwa_server
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function(
        "window.BsbPwa?.capabilityState() === 'registration-failed'"
    )
    assert attempts == [1]
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert attempts == [2]
    assert page.evaluate("window.BsbPwa.capabilityState()") == "ready"
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"
    context.close()


def test_paused_persisted_queue_is_not_auto_resumed(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate(
        """
        async () => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL("../__bsb_meta__/queue.json", location.href)),
            new Response(JSON.stringify({
              schema: 2,
              generation: "paused-generation",
              state: "paused",
              labels: ["2026-07"],
              current: null,
              succeeded: [],
              failed: [],
              errors: [],
            })),
          );
        }
        """
    )
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.reload()
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.state === 'paused')"
    )
    page.wait_for_timeout(250)
    assert not any("data/offline/2026-07.json" in url for url in requests)
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
    app_script = next(
        item for item in manifest["resources"] if item["url"] == "assets/app.js"
    )
    quarter_page = next(
        item for item in manifest["resources"] if item["url"] == "2026-07/index.html"
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
    old_app_status = page.evaluate(
        "({ url, hash }) => fetch(`${url}?v=${hash}`)"
        ".then((response) => response.status)",
        {
            "url": f"{pwa_server}/{app_script['url']}",
            "hash": app_script["content_hash"],
        },
    )
    assert old_app_status == 200
    mismatched_status = page.evaluate(
        "({ url, hash }) => fetch(`${url}?v=${hash}`)"
        ".then((response) => response.status)",
        {
            "url": f"{pwa_server}/{cover['url']}",
            "hash": quarter_page["content_hash"],
        },
    )
    assert mismatched_status != 200
    context.close()


def test_quarter_downloader_deduplicates_shared_cover_and_garbage_collects(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-04', '2026-07'])")
    _wait_for_queue(page, 2)
    states = page.evaluate("async () => window.BsbPwa.listQuarterStates()")
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert [state["quarter"] for state in states] == [
        "2026-07",
        "2026-04",
    ], queue
    assert all(state["status"] == "COMPLETE" for state in states), states
    assert all(state["staging"] is None for state in states)

    repeated_requests: list[str] = []
    page.on("request", lambda request: repeated_requests.append(request.url))
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert repeated_requests == [
        f"{pwa_server}/data/offline/2026-07.json"
    ]

    april = next(state for state in states if state["quarter"] == "2026-04")
    cover = next(
        item
        for item in april["active"]["resources"]
        if item["url"] == "covers/101.webp"
    )
    matching = page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.keys())
          .then((keys) => keys.filter((key) => key.url.endsWith(hash)).length)
        """,
        cover["content_hash"],
    )
    assert matching == 1

    page.evaluate("window.BsbPwa.removeQuarter('2026-04')")
    assert page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.keys())
          .then((keys) => keys.some((key) => key.url.endsWith(hash)))
        """,
        cover["content_hash"],
    )
    page.evaluate("window.BsbPwa.removeQuarter('2026-07')")
    assert not page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.keys())
          .then((keys) => keys.some((key) => key.url.endsWith(hash)))
        """,
        cover["content_hash"],
    )
    context.close()


def test_running_queue_merges_new_labels_without_replacing_generation(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    first = page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    second = page.evaluate("async () => window.BsbPwa.enqueue(['2026-04'])")
    assert second["generation"] == first["generation"]
    assert second["labels"] == ["2026-07", "2026-04"]
    _wait_for_queue(page, 2)
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["succeeded"] == ["2026-07", "2026-04"]
    assert queue["failed"] == []
    context.close()


def test_orphan_offline_quarter_stays_visible_without_entering_public_queue(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function(
        "document.querySelectorAll('[data-offline-quarter]').length === 2"
    )
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    page.evaluate(
        """
        async (manifest) => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL(
              "../__bsb_meta__/quarters/2027-01.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2027-01",
              status: "COMPLETE",
              active: { ...manifest, quarter: "2027-01" },
              staging: null,
            })),
          );
          window.dispatchEvent(new CustomEvent("bsb:pwa-state"));
        }
        """,
        manifest,
    )
    page.reload()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2027-01\"]') !== null"
    )
    page.locator("[data-queue-kind]").select_option("all")
    page.wait_for_function(
        "document.querySelector('.queue-preview')?.textContent.includes('2 个季度')"
    )
    page.locator("[data-queue-kind]").select_option("current")
    page.wait_for_function(
        "document.querySelector('.queue-preview')?.textContent.includes('1 个季度')"
    )
    context.close()


def test_two_pages_share_one_queue_runner_and_persist_merge(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    first = context.new_page()
    second = context.new_page()
    first_requests: list[str] = []
    second_requests: list[str] = []
    first.on("request", lambda request: first_requests.append(request.url))
    second.on("request", lambda request: second_requests.append(request.url))

    def delay_manifest(route) -> None:
        time.sleep(0.25)
        route.continue_()

    first.route("**/data/offline/2026-07.json", delay_manifest)
    first.goto(f"{pwa_server}/settings/index.html")
    second.goto(f"{pwa_server}/settings/index.html")
    first.wait_for_function("Boolean(window.BsbPwa)")
    second.wait_for_function("Boolean(window.BsbPwa)")
    generation = first.evaluate(
        "async () => (await window.BsbPwa.enqueue(['2026-07'])).generation"
    )
    merged = second.evaluate(
        "async () => window.BsbPwa.enqueue(['2026-04'])"
    )
    assert merged["generation"] == generation
    assert merged["current"] in (None, "2026-07")
    first.unroute("**/data/offline/2026-07.json")
    _wait_for_queue(second, 2)
    queue = second.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["succeeded"] == ["2026-07", "2026-04"]
    manifest_requests = [
        url for url in [*first_requests, *second_requests] if "data/offline/" in url
    ]
    assert manifest_requests.count(f"{pwa_server}/data/offline/2026-07.json") == 1
    assert manifest_requests.count(f"{pwa_server}/data/offline/2026-04.json") == 1
    context.close()


def test_simultaneous_cross_tab_enqueue_serializes_queue_metadata(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    first = context.new_page()
    second = context.new_page()
    first.goto(f"{pwa_server}/settings/index.html")
    second.goto(f"{pwa_server}/settings/index.html")
    first.wait_for_function("Boolean(window.BsbPwa)")
    second.wait_for_function("Boolean(window.BsbPwa)")
    first.evaluate(
        """
        void navigator.locks.request(
          "bsb-offline-queue-mutation",
          { mode: "exclusive" },
          async () => {
            window.__mutationHeld = true;
            await new Promise((resolve) => { window.__releaseMutation = resolve; });
          },
        );
        """
    )
    first.wait_for_function("window.__mutationHeld === true")
    first.evaluate(
        "void (window.__enqueueA = window.BsbPwa.enqueue(['2026-07']))"
    )
    second.evaluate(
        "void (window.__enqueueB = window.BsbPwa.enqueue(['2026-04']))"
    )
    second.wait_for_timeout(100)
    first.evaluate("window.__releaseMutation()")
    first_queue = first.evaluate("window.__enqueueA")
    second_queue = second.evaluate("window.__enqueueB")
    assert first_queue["generation"] == second_queue["generation"]
    assert second_queue["labels"] == ["2026-07", "2026-04"]
    _wait_for_queue(second, 2)
    queue = second.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["succeeded"] == ["2026-07", "2026-04"]
    assert queue["failed"] == []
    context.close()


def test_cancelled_runner_cannot_modify_new_queue_generation(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()

    def delay_manifest(route) -> None:
        time.sleep(0.5)
        route.continue_()

    page.route("**/data/offline/2026-07.json", delay_manifest)
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    old = page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.evaluate("async () => window.BsbPwa.cancelQueue()")
    new = page.evaluate("async () => window.BsbPwa.enqueue(['2026-04'])")
    _wait_for_queue(page, 1)
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert new["generation"] != old["generation"]
    assert queue["generation"] == new["generation"]
    assert queue["succeeded"] == ["2026-04"]
    assert queue["failed"] == []
    context.close()


def test_requeued_failed_quarter_clears_error_before_success(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    failed_once = [False]

    def fail_first_quarter_request(route) -> None:
        if not failed_once[0]:
            failed_once[0] = True
            route.abort()
            return
        route.continue_()

    page.route("**/data/quarters/2026-07.json", fail_first_quarter_request)
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07', '2026-04'])")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => "
        "queue.current === '2026-04' && queue.failed.includes('2026-07'))"
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 2)
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["succeeded"] == ["2026-07", "2026-04"]
    assert queue["failed"] == []
    assert queue["errors"] == []
    context.close()


def test_failed_quarter_update_keeps_active_and_resume_fetches_only_missing_bytes(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        """
        const nativeSetTimeout = window.setTimeout.bind(window);
        window.setTimeout = (callback, delay, ...args) =>
          nativeSetTimeout(callback, Math.min(delay, 5), ...args);
        """
    )
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    original = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert original["status"] == "COMPLETE"

    quarter_path = pwa_site / "data" / "quarters" / "2026-07.json"
    changed_bytes = quarter_path.read_bytes() + b" "
    quarter_path.write_bytes(b"incorrect quarter bytes")
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["revision"] = "updated-quarter-revision"
    resource = next(
        item
        for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    resource["content_hash"] = hashlib.sha256(changed_bytes).hexdigest()
    resource["size_bytes"] = len(changed_bytes)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    failed = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert failed["status"] == "INCOMPLETE"
    assert failed["active"]["revision"] == original["active"]["revision"]
    assert failed["staging"]["revision"] == "updated-quarter-revision"
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('已离线 · 更新未完成')"
    )
    assert page.locator('[data-offline-quarter="2026-07"]').get_by_role(
        "button", name="继续更新"
    ).is_visible()
    old_hash = next(
        item["content_hash"]
        for item in original["active"]["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    assert page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.match(new URL(
            `../__bsb_content__/${hash}`,
            location.href,
          )))
          .then(Boolean)
        """,
        old_hash,
    )

    quarter_path.write_bytes(changed_bytes)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    complete = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert complete["status"] == "COMPLETE"
    assert complete["active"]["revision"] == "updated-quarter-revision"
    assert complete["staging"] is None
    resource_requests = [
        url
        for url in requests
        if not url.endswith("data/offline/2026-07.json")
    ]
    assert resource_requests == [
        f"{pwa_server}/data/quarters/2026-07.json"
    ]
    context.close()


def test_quarter_manifest_validation_rejects_unsafe_and_duplicate_resources(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    valid = {
        "quarter": "2026-07",
        "revision": "revision",
        "resources": [
            {"url": "index.html", "content_hash": "a" * 64, "size_bytes": 1}
        ],
    }
    assert page.evaluate(
        "([value]) => window.BsbPwa.validateQuarterManifest(value, '2026-07')",
        [valid],
    )["quarter"] == "2026-07"
    for bad_url in ("/absolute", "../escape", "https://example.invalid/x"):
        invalid = {**valid, "resources": [{**valid["resources"][0], "url": bad_url}]}
        assert page.evaluate(
            """
            ([value]) => {
              try {
                window.BsbPwa.validateQuarterManifest(value, "2026-07");
                return false;
              } catch { return true; }
            }
            """,
            [invalid],
        )
    duplicate = {**valid, "resources": valid["resources"] * 2}
    assert page.evaluate(
        """
        ([value]) => {
          try {
            window.BsbPwa.validateQuarterManifest(value, "2026-07");
            return false;
          } catch { return true; }
        }
        """,
        [duplicate],
    )
    context.close()


def test_pause_resume_and_network_recovery_keep_queue_state(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate(
        """
        async () => {
          await window.BsbPwa.enqueue(["2026-07"]);
          await window.BsbPwa.pauseQueue();
        }
        """
    )
    page.wait_for_timeout(100)
    assert page.evaluate(
        "async () => (await window.BsbPwa.currentQueue()).state"
    ) == "paused"
    page.evaluate("async () => window.BsbPwa.resumeQueue()")
    _wait_for_queue(page, 1)
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"

    page.evaluate("async () => window.BsbPwa.removeQuarter('2026-07')")
    context.set_offline(True)
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    assert page.evaluate(
        "async () => (await window.BsbPwa.currentQueue()).state"
    ) == "waiting-network"
    context.set_offline(False)
    _wait_for_queue(page, 1)
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"
    context.close()


def test_cancelled_queue_keeps_partial_staging_and_does_not_resume_on_reopen(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.route("**/covers/101.webp", lambda route: route.abort())
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.evaluate(
        """
        async () => {
          for (let attempt = 0; attempt < 200; attempt += 1) {
            const state = await window.BsbPwa.getQuarterState("2026-07");
            if ((state.staging?.verified_hashes || []).length > 0) return;
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
          throw new Error("partial staging was not retained");
        }
        """
    )
    page.evaluate("async () => window.BsbPwa.cancelQueue()")
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert state["staging"]["verified_hashes"]
    page.close()

    reopened = context.new_page()
    requests: list[str] = []
    reopened.on("request", lambda request: requests.append(request.url))
    reopened.goto(f"{pwa_server}/settings/index.html")
    reopened.wait_for_function("Boolean(window.BsbPwa)")
    reopened.wait_for_timeout(200)
    queue = reopened.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["state"] == "cancelled"
    assert not any("data/offline/2026-07.json" in url for url in requests)
    context.close()


def test_downloading_queue_resumes_after_page_reopen(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.route("**/data/quarters/2026-07.json", lambda route: route.abort())
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    with page.expect_request("**/data/quarters/2026-07.json"):
        page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    assert page.evaluate(
        "async () => (await window.BsbPwa.currentQueue()).state"
    ) == "downloading"
    page.close()

    reopened = context.new_page()
    reopened.goto(f"{pwa_server}/settings/index.html")
    reopened.wait_for_function("Boolean(window.BsbPwa)")
    _wait_for_queue(reopened, 1)
    state = reopened.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert state["status"] == "COMPLETE"
    assert state["staging"] is None
    context.close()


def test_runtime_resource_is_promoted_and_hash_mismatch_is_refetched(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    cover = next(
        item for item in manifest["resources"] if item["url"] == "covers/101.webp"
    )
    quarter_page = next(
        item for item in manifest["resources"] if item["url"] == "2026-07/index.html"
    )
    page.evaluate(
        """
        async ({ cover, quarterPage }) => {
          const runtime = await caches.open("bsb-runtime-v1");
          const coverResponse = await fetch(new URL(`../${cover.url}`, location.href));
          await runtime.put(
            new Request(new URL(
              `../${cover.url}?v=${cover.content_hash}`,
              location.href,
            )),
            coverResponse,
          );
          await runtime.put(
            new Request(new URL(`../${quarterPage.url}`, location.href)),
            new Response(new Uint8Array(quarterPage.size_bytes)),
          );
        }
        """,
        {"cover": cover, "quarterPage": quarter_page},
    )
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert not any("covers/101.webp" in url for url in requests)
    assert requests.count(f"{pwa_server}/2026-07/index.html") == 1
    runtime_entries = page.evaluate(
        "caches.open('bsb-runtime-v1').then((cache) => cache.keys())"
        ".then((keys) => keys.map((key) => key.url))"
    )
    assert not any("covers/101.webp" in url for url in runtime_entries)
    context.close()


def test_missing_old_versioned_blob_never_returns_current_physical_bytes(
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
    old_hash = hashlib.sha256(b"old app revision").hexdigest()
    result = page.evaluate(
        """
        async ({ manifest, oldHash }) => {
          const app = manifest.resources.find((item) => item.url === "assets/app.js");
          if (!app) throw new Error("app.js resource missing");
          const oldResource = { ...app, content_hash: oldHash, size_bytes: 16 };
          const activeManifest = {
            ...manifest,
            resources: manifest.resources.map((item) => (
              item.url === app.url ? oldResource : item
            )),
          };
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL("../__bsb_meta__/quarters/2026-07.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "COMPLETE",
              active: activeManifest,
              staging: null,
              error: null,
            })),
          );
          const content = await caches.open("bsb-content-v1");
          await content.delete(new Request(new URL(
            `../__bsb_content__/${oldHash}`, location.href,
          )));
          const runtime = await caches.open("bsb-runtime-v1");
          const currentBytes = await fetch(new URL(`../${app.url}`, location.href));
          await runtime.put(
            new Request(new URL(`../${app.url}?v=${oldHash}`, location.href)),
            currentBytes,
          );
          const response = await fetch(new URL(
            `../${app.url}?v=${oldHash}`, location.href,
          ));
          return {
            status: response.status,
            body: await response.text(),
            runtime: (await runtime.keys()).map((key) => key.url),
            content: (await content.keys()).map((key) => key.url),
          };
        }
        """,
        {"manifest": manifest, "oldHash": old_hash},
    )
    assert result["status"] == 503
    assert "Versioned resource unavailable" in result["body"]
    assert not any(f"?v={old_hash}" in url for url in result["runtime"])
    assert not any(url.endswith(old_hash) for url in result["content"])
    context.close()


def test_gzip_response_is_verified_and_cached_without_rebuilding_headers(
    chromium: Browser,
    gzip_pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{gzip_pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert state["status"] == "COMPLETE"
    context.close()


def test_settings_reports_storage_and_controls_quarter_downloads(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        """
        window.__persistCalls = 0;
        Object.defineProperty(navigator, "storage", {
          configurable: true,
          value: {
            estimate: async () => ({ usage: 2048, quota: 8192 }),
            persisted: async () => false,
            persist: async () => {
              window.__persistCalls += 1;
              return false;
            },
          },
        });
        """
    )
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function(
        "document.querySelectorAll('[data-offline-quarter]').length === 2"
    )
    for heading in ("应用", "存储", "离线档案", "下载队列"):
        assert page.get_by_role("heading", name=heading).is_visible()
    assert page.get_by_text("2.0 KiB").is_visible()
    assert page.get_by_text("8.0 KiB").is_visible()
    assert page.evaluate("window.__persistCalls") == 0
    page.get_by_role("button", name="申请持久存储").click()
    page.wait_for_function("window.__persistCalls === 1")
    assert page.get_by_text("Persistent storage: not granted").is_visible()
    assert page.get_by_role("button", name="安装应用").count() == 0
    assert page.get_by_text("添加到主屏幕").is_visible()

    selector = page.locator("[data-queue-kind]")
    assert selector.locator("option").all_text_contents() == [
        "当前季度",
        "指定年份",
        "年份范围",
        "全部季度",
    ]
    july = page.locator('[data-offline-quarter="2026-07"]')
    july.get_by_role("button", name="下载").click()
    _wait_for_queue(page, 1)
    page.evaluate("window.dispatchEvent(new CustomEvent('bsb:pwa-state'))")
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('已离线')"
    )
    assert july.get_by_role("button", name="移除").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    july.get_by_role("button", name="移除").click()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('未下载')"
    )
    context.close()


def test_quarter_page_offline_action_tracks_download_and_confirmed_remove(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/2026-07/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    control = page.locator("[data-quarter-offline]")
    control.get_by_role("button", name="下载当前季度供离线使用").click()
    _wait_for_queue(page, 1)
    page.wait_for_function(
        "document.querySelector('[data-quarter-offline-status]')"
        "?.textContent.includes('已离线')"
    )
    assert control.get_by_role("button", name="移除离线缓存").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    control.get_by_role("button", name="移除离线缓存").click()
    page.wait_for_function(
        "document.querySelector('[data-quarter-offline-status]')"
        "?.textContent.includes('未下载')"
    )
    context.close()


def test_downloaded_quarter_is_complete_offline_and_undownloaded_redirects(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"

    context.set_offline(True)
    page.goto(f"{pwa_server}/2026-07/index.html")
    page.wait_for_selector('[data-subject-id="101"]')
    page.wait_for_function(
        """
        () => {
          const image = document.querySelector('[data-subject-id="101"] img');
          return image?.complete && image.naturalWidth > 0;
        }
        """
    )
    assert page.locator('[data-subject-id="101"] img').is_visible()
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    assert page.locator("[data-detail-panel]").is_visible()
    page.locator("[data-search]").fill("Original 101")
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
    page.goto(f"{pwa_server}/settings/index.html")
    assert page.get_by_role("heading", name="设置").is_visible()
    assert page.locator('[data-offline-quarter="2026-07"]').get_by_text(
        "已离线"
    ).is_visible()

    page.goto(f"{pwa_server}/2026-04/index.html")
    page.wait_for_url(f"{pwa_server}/settings/index.html")
    assert page.get_by_role("heading", name="设置").is_visible()
    context.close()


def test_network_error_prefers_guaranteed_active_content_without_faking_unknown_404(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    (pwa_site / "2026-07" / "index.html").unlink()
    fallback = page.evaluate(
        "fetch('../2026-07/index.html').then(async (response) => ({"
        "status: response.status, text: await response.text()}))"
    )
    assert fallback["status"] == 200
    assert "data-page=\"quarter\"" in fallback["text"]
    unknown = page.evaluate(
        "fetch('../not-downloaded.html').then((response) => response.status)"
    )
    assert unknown == 404
    context.close()


def test_app_update_waits_for_user_and_data_only_build_does_not_update_shell(
    chromium: Browser,
    update_server: str,
    update_site: tuple[object, object, Path, Path],
) -> None:
    builder, database, isolated_root, served = update_site
    context = chromium.new_context()
    context.add_init_script(
        """
        window.addEventListener("DOMContentLoaded", () => {
          const count = Number(sessionStorage.getItem("load-count") || "0") + 1;
          sessionStorage.setItem("load-count", String(count));
        });
        """
    )
    page = context.new_page()
    page.goto(f"{update_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    shell_before = (served / "data" / "pwa-shell.json").read_bytes()
    worker_before = (served / "sw.js").read_bytes()

    repository = SubjectRepository(database)
    subject = repository.get_subject_facts(202)
    assert subject is not None
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            replace(subject, subject=replace(subject.subject, rating_score=9.4)),
        )
    builder.build()
    assert (served / "data" / "pwa-shell.json").read_bytes() == shell_before
    assert (served / "sw.js").read_bytes() == worker_before
    page.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.update())"
    )
    page.wait_for_timeout(300)
    assert page.locator("[data-pwa-update-notice]").is_hidden()

    pwa_source = isolated_root / "static" / "js" / "pwa.js"
    pwa_source.write_text(
        pwa_source.read_text("utf-8") + "\n/* browser update fixture */\n",
        encoding="utf-8",
    )
    builder.build()
    assert (served / "data" / "pwa-shell.json").read_bytes() != shell_before
    assert (served / "sw.js").read_bytes() != worker_before
    worker_stat = (served / "sw.js").stat()
    os.utime(
        served / "sw.js",
        (worker_stat.st_atime + 2, worker_stat.st_mtime + 2),
    )
    page.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.update())"
    )
    page.evaluate(
        """
        async () => {
          const registration = await navigator.serviceWorker.ready;
          for (let attempt = 0; attempt < 200; attempt += 1) {
            if (registration.waiting) return;
            await new Promise((resolve) => setTimeout(resolve, 25));
          }
          throw new Error("updated worker did not enter waiting");
        }
        """
    )
    page.wait_for_function(
        "document.querySelector('[data-pwa-update-notice]')?.hidden === false"
    )
    page.evaluate("window.__pageSentinel = 'still-here'")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pageSentinel") == "still-here"
    assert page.evaluate("sessionStorage.getItem('load-count')") == "1"

    page.locator("[data-pwa-refresh]").click()
    page.wait_for_function("sessionStorage.getItem('load-count') === '2'")
    page.wait_for_timeout(300)
    assert page.evaluate("sessionStorage.getItem('load-count')") == "2"
    context.close()


def test_waiting_shell_survives_page_gc_and_activation_cleans_pending_metadata(
    chromium: Browser,
    update_server: str,
    update_site: tuple[object, object, Path, Path],
) -> None:
    builder, _database, isolated_root, served = update_site
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{update_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    shell_before = json.loads(
        (served / "data" / "pwa-shell.json").read_text("utf-8")
    )

    pwa_source = isolated_root / "static" / "js" / "pwa.js"
    pwa_source.write_text(
        pwa_source.read_text("utf-8") + "\n/* pending shell fixture */\n",
        encoding="utf-8",
    )
    builder.build()
    worker_stat = (served / "sw.js").stat()
    os.utime(served / "sw.js", (worker_stat.st_atime + 2, worker_stat.st_mtime + 2))
    page.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.update())"
    )
    pending = page.evaluate(
        """
        async () => {
          const registration = await navigator.serviceWorker.ready;
          for (let attempt = 0; attempt < 200; attempt += 1) {
            if (registration.waiting) {
              const meta = await caches.open("bsb-meta-v1");
              const keys = await meta.keys();
              const key = keys.find((request) =>
                request.url.includes("shell-pending-"));
              if (!key) throw new Error("pending shell metadata missing");
              return meta.match(key).then((response) => response.json());
            }
            await new Promise((resolve) => setTimeout(resolve, 25));
          }
          throw new Error("updated worker did not enter waiting");
        }
        """
    )
    page.evaluate("window.BsbPwa.garbageCollect()")
    assert page.evaluate(
        """
        async (manifest) => {
          const cache = await caches.open("bsb-content-v1");
          for (const resource of manifest.resources) {
            const key = new URL(
              `../__bsb_content__/${resource.content_hash}`,
              location.href,
            );
            if (!await cache.match(key)) {
              return false;
            }
          }
          return true;
        }
        """,
        pending,
    )

    page.evaluate("window.BsbPwa.refreshApp()")
    page.wait_for_function(
        """
        async (revision) => {
          const response = await caches.open("bsb-meta-v1").then((cache) =>
            cache.match(new URL("../__bsb_meta__/shell.json", location.href)));
          const value = response ? await response.json() : null;
          return value && value.revision !== revision;
        }
        """,
        arg=shell_before["revision"],
    )
    pending_count = page.evaluate(
        "caches.open('bsb-meta-v1').then((cache) => cache.keys())"
        ".then((keys) => keys.filter((key) => "
        "key.url.includes('shell-pending-')).length)"
    )
    assert pending_count == 0
    context.set_offline(True)
    page.goto(f"{update_server}/settings/index.html")
    assert page.get_by_role("heading", name="设置").is_visible()
    context.close()


def test_service_worker_controlled_online_archive_keeps_same_origin_behavior(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.goto(f"{pwa_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('appearance')"
    )
    page.locator('[data-subject-id="101"] [data-open-subject]').first.click()
    assert page.locator("[data-detail-panel]").is_visible()
    page.locator('[data-media-mode="movie"]').click()
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
    assert all(url.startswith(pwa_server) for url in requests)
    assert not any("api.bgm.tv" in url for url in requests)
    context.close()
