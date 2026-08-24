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

pytestmark = pytest.mark.browser

_NEXT_SAFE_PORT = 18080


def _server(handler: object) -> http.server.ThreadingHTTPServer:
    """Use high, Chromium-safe loopback ports instead of unsafe-port roulette."""
    global _NEXT_SAFE_PORT
    for _ in range(100):
        port = _NEXT_SAFE_PORT
        _NEXT_SAFE_PORT += 1
        try:
            return http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
    raise RuntimeError("could not allocate a safe browser test port")


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


def _wait_for_pwa_startup_queue_quiescent(page: Page) -> None:
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    assert page.evaluate("typeof navigator.locks?.request === 'function'")
    page.evaluate(
        """
        async () => navigator.locks.request(
          "bsb-offline-queue-mutation",
          { mode: "exclusive" },
          async () => {},
        )
        """
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
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def delayed_cover_server(
    pwa_site: Path,
) -> Iterator[tuple[str, threading.Event, threading.Event, threading.Event]]:
    armed = threading.Event()
    started = threading.Event()
    release = threading.Event()

    class DelayedCoverHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if (
                armed.is_set()
                and self.path.split("?", 1)[0].endswith("/covers/101.webp")
            ):
                started.set()
                if not release.wait(5):
                    self.send_error(504, "repair response timeout")
                    return
            super().do_GET()

    handler = functools.partial(
        DelayedCoverHandler,
        directory=str(pwa_site.parent),
    )
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_port}/bangumi-side-b",
            armed,
            started,
            release,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def delayed_manifest_server(pwa_site: Path) -> Iterator[str]:
    manifest_condition = threading.Condition()
    active_manifests = 0
    delay_next_manifest = False

    class DelayedManifestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal active_manifests, delay_next_manifest
            path = self.path.split("?", 1)[0]
            if path.endswith("/__test__/delay-next-manifest"):
                with manifest_condition:
                    manifest_condition.wait_for(
                        lambda: active_manifests == 0,
                        timeout=5,
                    )
                    delay_next_manifest = True
                self.send_response(204)
                self.end_headers()
                return
            if not path.endswith("/data/offline/2026-07.json"):
                super().do_GET()
                return
            with manifest_condition:
                active_manifests += 1
                should_delay = delay_next_manifest
                if should_delay:
                    delay_next_manifest = False
            try:
                if should_delay:
                    time.sleep(0.75)
                super().do_GET()
            finally:
                with manifest_condition:
                    active_manifests -= 1
                    manifest_condition.notify_all()

    handler = functools.partial(
        DelayedManifestHandler,
        directory=str(pwa_site.parent),
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


def _arm_delayed_manifest(page: Page) -> None:
    page.evaluate("fetch('../__test__/delay-next-manifest')")


def _write_offline_manifest(
    pwa_site: Path,
    quarter: str,
    manifest: dict[str, object],
) -> None:
    (pwa_site / "data" / "offline" / f"{quarter}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _seed_quarter_state(page: Page, quarter: str, state: dict[str, object]) -> None:
    page.evaluate(
        """
        async ({ quarter, state }) => {
          const meta = await caches.open('bsb-meta-v1');
          await meta.put(
            new Request(new URL(
              `../__bsb_meta__/quarters/${quarter}.json`, location.href)),
            new Response(JSON.stringify(state), {
              headers: { 'Content-Type': 'application/json' },
            }),
          );
        }
        """,
        {"quarter": quarter, "state": state},
    )


@pytest.fixture
def failing_manifest_server(pwa_site: Path) -> Iterator[str]:
    class FailingManifestHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/data/offline/2026-07.json"):
                self.send_error(503, "manifest unavailable")
                return
            super().do_GET()

    handler = functools.partial(
        FailingManifestHandler,
        directory=str(pwa_site.parent),
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


@pytest.fixture
def slow_pwa_server(pwa_site: Path) -> Iterator[str]:
    class SlowHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/sw.js"):
                time.sleep(7)
            super().do_GET()

    handler = functools.partial(SlowHandler, directory=str(pwa_site.parent))
    server = _server(handler)
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
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/bangumi-side-b", attempts
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def flaky_resource_server(pwa_site: Path) -> Iterator[tuple[str, list[int]]]:
    attempts = [0]

    class FlakyResourceHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0].endswith("/data/quarters/2026-07.json"):
                attempts[0] += 1
                if attempts[0] == 1:
                    self.send_error(503, "resource unavailable")
                    return
            super().do_GET()

    handler = functools.partial(
        FlakyResourceHandler,
        directory=str(pwa_site.parent),
    )
    server = _server(handler)
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
    server = _server(handler)
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
    server = _server(handler)
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


def test_shell_manifest_rejects_conflicting_content_identity(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    shell_before = page.evaluate(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          const response = await cache.match(new Request(new URL(
            "../__bsb_meta__/shell.json", location.href)));
          return response.json();
        }
        """
    )
    shell_path = pwa_site / "data" / "pwa-shell.json"
    shell = json.loads(shell_path.read_text("utf-8"))
    first = shell["resources"][0]
    shell["resources"].append(
        {
            **first,
            "url": "identity-conflict.html",
            "size_bytes": first["size_bytes"] + 1,
        }
    )
    shell_path.write_text(
        json.dumps(shell, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    sw_path = pwa_site / "sw.js"
    sw_path.write_bytes(sw_path.read_bytes() + b"\n// identity conflict fixture\n")
    page.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.update())"
    )
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => "
        "queue.state === 'idle' && queue.succeeded.includes('2026-07'))"
    )
    shell_after = page.evaluate(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          const response = await cache.match(new Request(new URL(
            "../__bsb_meta__/shell.json", location.href)));
          return response.json();
        }
        """
    )
    pending = page.evaluate(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          return (await cache.keys()).some((request) =>
            request.url.includes("shell-pending-"));
        }
        """
    )
    assert shell_after == shell_before
    assert pending is False
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
    quarter = context.new_page()
    quarter.set_viewport_size({"width": 390, "height": 844})
    quarter.goto(f"{pwa_server}/2026-07/index.html")
    quarter.get_by_role("button", name="菜单").click()
    quarter.wait_for_function(
        "document.querySelector('[data-mobile-quarter-offline-actions] a') !== null"
    )
    assert (
        quarter.locator('[data-mobile-quarter-offline-actions] a').get_attribute("href")
        == "../settings/index.html"
    )
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
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json", location.href)),
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
        "window.BsbPwa.currentQueue().then((queue) => "
        "queue.state === 'waiting-service-worker')"
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
    assert page.locator("[data-retry-service-worker]").count() == 0
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
        const nativeRegister = navigator.serviceWorker.register
          .bind(navigator.serviceWorker);
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
    retry = page.locator("[data-retry-service-worker]")
    retry.wait_for()
    retry.click()
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    assert attempts == [2]
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


def test_versioned_service_worker_repair_does_not_recreate_removed_content(
    chromium: Browser,
    delayed_cover_server: tuple[str, threading.Event, threading.Event, threading.Event],
) -> None:
    pwa_server, armed, started, release = delayed_cover_server
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    cover = next(
        item
        for item in state["active"]["resources"]
        if item["url"] == "covers/101.webp"
    )
    page.evaluate(
        """
        async (resource) => {
          const content = await caches.open("bsb-content-v1");
          await content.delete(new Request(new URL(
            `../__bsb_content__/${resource.content_hash}`, location.href)));
          const runtime = await caches.open("bsb-runtime-v1");
          for (const url of [
            resource.url,
            `${resource.url}?v=${resource.content_hash}`,
          ]) {
            await runtime.delete(new Request(new URL(`../${url}`, location.href)));
          }
        }
        """,
        cover,
    )
    armed.set()
    page.evaluate(
        """
        ({ url, hash }) => {
          window.__repair = fetch(`${url}?v=${hash}`)
            .then(async (response) => ({
              status: response.status,
              bytes: (await response.arrayBuffer()).byteLength,
            }));
        }
        """,
        {"url": f"{pwa_server}/{cover['url']}", "hash": cover["content_hash"]},
    )
    assert started.wait(5)
    page.evaluate("void (window.__remove = window.BsbPwa.removeQuarter('2026-07'))")
    page.wait_for_function(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status === 'NONE'"
    )
    release.set()
    result = page.evaluate("window.__repair")
    assert result["status"] == 200
    assert result["bytes"] == cover["size_bytes"]
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        cover["content_hash"],
    ) is False
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


def test_content_gc_waits_for_cross_page_reference_lease(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context(service_workers="block")
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    orphan_hash = "f" * 64
    page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          await content.put(
            new Request(new URL(`../__bsb_content__/${hash}`, location.href)),
            new Response("orphan"),
          );
        }
        """,
        orphan_hash,
    )
    page.evaluate(
        """
        () => void navigator.locks.request(
          "bsb-pwa-content-maintenance",
          { mode: "shared" },
          async () => {
            window.__contentLeaseHeld = true;
            await new Promise((resolve) => { window.__releaseContentLease = resolve; });
          },
        )
        """
    )
    page.wait_for_function("window.__contentLeaseHeld === true")
    page.evaluate("void (window.__contentGc = window.BsbPwa.garbageCollect())")
    page.wait_for_timeout(100)
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        orphan_hash,
    )
    page.evaluate("window.__releaseContentLease()")
    page.evaluate("window.__contentGc")
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        orphan_hash,
    ) is False
    context.close()


def test_content_gc_snapshot_cannot_delete_new_quarter_content(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    manifest = json.loads(
        (pwa_site / "data" / "offline" / "2026-07.json").read_text("utf-8")
    )
    target_hashes = {item["content_hash"] for item in manifest["resources"]}
    orphan_hash = "e" * 64
    context = chromium.new_context()
    first = context.new_page()
    first.goto(f"{pwa_server}/settings/index.html")
    first.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    first.wait_for_function("navigator.serviceWorker.controller !== null")
    first.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          await content.put(
            new Request(new URL(`../__bsb_content__/${hash}`, location.href)),
            new Response("orphan"),
          );
        }
        """,
        orphan_hash,
    )
    first.evaluate(
        """
        () => {
          const nativeOpen = caches.open.bind(caches);
          const wrapped = new WeakSet();
          window.__gcKeysEntered = false;
          window.__releaseGcKeys = null;
          caches.open = async (name) => {
            const cache = await nativeOpen(name);
            if (name !== "bsb-content-v1" || wrapped.has(cache)) return cache;
            wrapped.add(cache);
            const nativeKeys = cache.keys.bind(cache);
            cache.keys = async (...args) => {
              if (!window.__gcKeysEntered) {
                window.__gcKeysEntered = true;
                await new Promise((resolve) => {
                  window.__releaseGcKeys = resolve;
                });
              }
              return nativeKeys(...args);
            };
            return cache;
          };
        }
        """
    )
    first.evaluate("void (window.__gcPromise = window.BsbPwa.garbageCollect())")
    first.wait_for_function("window.__gcKeysEntered === true")

    second = context.new_page()
    second.goto(f"{pwa_server}/settings/index.html")
    second.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    second.wait_for_function("navigator.serviceWorker.controller !== null")
    second.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    second.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.current === '2026-07')"
    )
    second.wait_for_timeout(100)
    assert first.evaluate("window.__gcKeysEntered")
    assert first.evaluate(
        """
        async (hash) => {
          const cache = await caches.open("bsb-content-v1");
          return Boolean(await cache.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        orphan_hash,
    )
    first.evaluate("window.__releaseGcKeys()")
    first.evaluate("window.__gcPromise")
    _wait_for_queue(second, 1)
    state = second.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert state["status"] == "COMPLETE"
    assert all(
        second.evaluate(
            """
            async (hash) => {
              const content = await caches.open("bsb-content-v1");
              return Boolean(await content.match(new Request(new URL(
                `../__bsb_content__/${hash}`, location.href))));
            }
            """,
            content_hash,
        )
        for content_hash in target_hashes
    )
    assert not second.evaluate(
        """
        async (hash) => {
          const cache = await caches.open("bsb-content-v1");
          return Boolean(await cache.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        orphan_hash,
    )
    context.set_offline(True)
    second.goto(f"{pwa_server}/2026-07/index.html")
    second.wait_for_selector('[data-subject-id="101"]')
    second.wait_for_function(
        "document.querySelector('[data-subject-id=\"101\"] img')?.complete"
    )
    context.close()


def test_no_web_locks_defers_destructive_content_gc(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        "Object.defineProperty(navigator, 'locks', {"
        " configurable: true, value: undefined });"
    )
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    assert page.evaluate("navigator.locks === undefined")
    page.evaluate(
        """
        async () => {
          const content = await caches.open("bsb-content-v1");
          await content.put(
            new Request(new URL(`../__bsb_content__/${"d".repeat(64)}`, location.href)),
            new Response("orphan"),
          );
        }
        """
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-04', '2026-07'])")
    _wait_for_queue(page, 2)
    before_remove = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert before_remove["status"] == "COMPLETE"
    shared_cover = next(
        item for item in before_remove["active"]["resources"]
        if item["url"] == "covers/101.webp"
    )
    page.evaluate("async () => window.BsbPwa.removeQuarter('2026-04')")
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-04')).status"
    ) == "NONE"
    after_remove = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert after_remove["status"] == "COMPLETE"
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        "d" * 64,
    )
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        shared_cover["content_hash"],
    )
    context.close()


def test_quarter_progress_counts_logical_resources_and_shares_inflight_hash(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    original = next(
        item for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    other = next(
        item for item in manifest["resources"]
        if item["url"] == "2026-07/index.html"
    )
    alias_url = "data/quarters/2026-07-alias.json"
    alias_path = pwa_site / alias_url
    alias_path.write_bytes((pwa_site / original["url"]).read_bytes())
    alias = {**original, "url": alias_url}
    manifest["resources"] = [original, alias, other]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    context = chromium.new_context()
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    progress = page.evaluate(
        """
        async () => {
          const state = await window.BsbPwa.getQuarterState("2026-07");
          const hash = state.active.resources[0].content_hash;
          return window.BsbPwa.__quarterProgress(
            "2026-07",
            state.active.resources,
            new Set([hash]),
          );
        }
        """
    )
    assert progress["verified_resources"] == 2
    assert progress["total_resources"] == 3
    assert progress["verified_bytes"] == original["size_bytes"] * 2
    resource_requests = [
        url
        for url in requests
        if url.endswith(original["url"]) or url.endswith(alias_url)
    ]
    assert len(resource_requests) == 1, resource_requests
    context.close()


def test_same_hash_dedupe_survives_delayed_metadata_commit(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    original = next(
        item for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    alias_url = "data/quarters/2026-07-alias.json"
    (pwa_site / alias_url).write_bytes((pwa_site / original["url"]).read_bytes())
    other = [
        item for item in manifest["resources"]
        if item["url"] in {"2026-07/index.html", "covers/101.webp"}
    ]
    manifest["resources"] = [original, *other, {**original, "url": alias_url}]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    context = chromium.new_context()
    page = context.new_page()
    page.add_init_script(
        """
        (() => {
          const nativePut = Cache.prototype.put;
          const nativeFetch = window.fetch.bind(window);
          let held = false;
          window.__holdQuarterState = false;
          window.__quarterStatePutHeld = false;
          window.__releaseQuarterStatePut = null;
          window.fetch = async (...args) => {
            const response = await nativeFetch(...args);
            const url = String(args[0]?.url || args[0] || "");
            if (url.includes("data/quarters/2026-07.json")) {
              window.__holdQuarterState = true;
            }
            return response;
          };
          Cache.prototype.put = function(request, response) {
            if (window.__holdQuarterState && !held && request.url.includes(
              "/__bsb_meta__/quarters/2026-07.json")) {
              held = true;
              window.__quarterStatePutHeld = true;
              const copy = response.clone();
              return new Promise((resolve, reject) => {
                window.__releaseQuarterStatePut = () =>
                  nativePut.call(this, request, copy).then(resolve, reject);
              });
            }
            return nativePut.call(this, request, response);
          };
        })();
        """
    )
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.wait_for_function("window.__quarterStatePutHeld === true")
    page.evaluate(
        """
        async (hash) => {
          const cache = await caches.open("bsb-content-v1");
          await cache.delete(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href)));
        }
        """,
        original["content_hash"],
    )
    page.wait_for_timeout(250)
    resource_requests = [
        url for url in requests
        if url.endswith(original["url"]) or url.endswith(alias_url)
    ]
    assert len(resource_requests) == 1, resource_requests
    page.evaluate(
        """
        async (resource) => {
          const response = await fetch(new URL(`../${resource.url}`, location.href));
          const cache = await caches.open("bsb-content-v1");
          await cache.put(new Request(new URL(
            `../__bsb_content__/${resource.content_hash}`, location.href)), response);
        }
        """,
        original,
    )
    page.evaluate("window.__releaseQuarterStatePut()")
    _wait_for_queue(page, 1)
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
    page.locator("[data-queue-kind] .select-trigger").click()
    page.locator('[data-queue-kind] [role="option"]', has_text="全部季度").click()
    page.wait_for_function(
        "document.querySelector('.queue-preview')?.textContent.includes('2 个季度')"
    )
    page.locator("[data-queue-kind] .select-trigger").click()
    page.locator('[data-queue-kind] [role="option"]', has_text="当前季度").click()
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


def test_simultaneous_cross_tab_enqueue_without_web_locks_keeps_both_labels(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        "Object.defineProperty(navigator, 'locks', {"
        " configurable: true, value: undefined });"
    )
    first = context.new_page()
    second = context.new_page()
    first.goto(f"{pwa_server}/settings/index.html")
    second.goto(f"{pwa_server}/settings/index.html")
    for page in (first, second):
        page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
        page.evaluate("async () => window.BsbPwa.pauseQueue()")
    context.set_offline(True)

    channel_name = "bsb-test-no-lock-enqueue"
    first.evaluate(
        """
        (channelName) => {
          const channel = new BroadcastChannel(channelName);
          let start;
          window.__enqueueStart = () => start();
          window.__enqueueChannel = channel;
          window.__enqueueResult = new Promise((resolve) => { start = resolve; })
            .then(() => window.BsbPwa.enqueue(["2026-07"]));
        }
        """,
        channel_name,
    )
    second.evaluate(
        """
        (channelName) => {
          const channel = new BroadcastChannel(channelName);
          window.__enqueueResult = new Promise((resolve) => {
            channel.addEventListener("message", (event) => {
              if (event.data === "start") resolve();
            }, { once: true });
          }).then(() => window.BsbPwa.enqueue(["2026-04"]));
        }
        """,
        channel_name,
    )
    first.evaluate(
        "() => { window.__enqueueStart(); "
        "window.__enqueueChannel.postMessage('start'); }"
    )
    first_result = first.evaluate("window.__enqueueResult")
    second_result = second.evaluate("window.__enqueueResult")
    first.wait_for_function(
        """
        () => caches.open("bsb-meta-v1")
          .then((cache) => cache.keys())
          .then((keys) => !keys.some((key) =>
            key.url.includes("/__bsb_meta__/locks/queue-mutation/")))
        """
    )
    first.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.labels.length === 2)"
    )
    queue = first.evaluate("async () => window.BsbPwa.currentQueue()")
    assert first_result["generation"] == second_result["generation"]
    assert queue["labels"] == ["2026-07", "2026-04"]
    context.close()


def test_quarter_metadata_writes_merge_monotonically_when_older_write_is_delayed(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    _wait_for_pwa_startup_queue_quiescent(page)
    page.evaluate(
        """
        (() => {
          const nativePut = Cache.prototype.put;
          let held = false;
          window.__holdQuarterWrites = false;
          window.__releaseQuarterPut = null;
          window.__quarterPutHeld = false;
          Cache.prototype.put = function(request, response) {
            if (window.__holdQuarterWrites && !held && request.url.includes(
              "/__bsb_meta__/quarters/2026-07.json")) {
              held = true;
              const copy = response.clone();
              return new Promise((resolve, reject) => {
                window.__releaseQuarterPut = () =>
                  nativePut.call(this, request, copy).then(resolve, reject);
                window.__quarterPutHeld = true;
              });
            }
            return nativePut.call(this, request, response);
          };
        })();
        """
    )
    page.evaluate(
        """
        async () => {
          const meta = await caches.open("bsb-meta-v1");
          const put = (path, value) => meta.put(
            new Request(new URL(`../__bsb_meta__/${path}`, location.href)),
            new Response(JSON.stringify(value)),
          );
          await put("queue.json", {
            schema: 2,
            generation: "generation-ab",
            state: "downloading",
            labels: ["2026-07"],
            current: "2026-07",
            succeeded: [],
            failed: [],
            errors: [],
          });
          await put("quarters/2026-07.json", {
            schema: 1,
            quarter: "2026-07",
            status: "INCOMPLETE",
            active: null,
            staging: {
              quarter: "2026-07",
              revision: "staging-revision",
              resources: [],
              verified_hashes: [],
            },
            error: null,
          });
          window.__holdQuarterWrites = true;
          const addHash = (hash) => window.BsbPwa.__updateQuarterDownloadState(
            "2026-07",
            "generation-ab",
            (current) => ({
              state: {
                ...current,
                staging: {
                  ...current.staging,
                  verified_hashes: [...new Set([
                    ...(current.staging?.verified_hashes || []), hash,
                  ])].sort(),
                },
              },
            }),
          );
          window.__addQuarterHash = addHash;
          window.__firstQuarterPutOutcome = null;
          window.__firstQuarterPut = addHash("a");
          void window.__firstQuarterPut.then(
            () => { window.__firstQuarterPutOutcome = { rejected: false }; },
            (error) => {
              window.__firstQuarterPutOutcome = {
                rejected: true,
                message: String(error?.message || error),
              };
            },
          );
        }
        """
    )
    page.wait_for_function(
        "window.__quarterPutHeld === true "
        "|| window.__firstQuarterPutOutcome !== null"
    )
    first_outcome = page.evaluate("window.__firstQuarterPutOutcome")
    assert page.evaluate("window.__quarterPutHeld === true"), (
        "delayed quarter Cache.put was not reached; "
        f"first mutation outcome: {first_outcome}"
    )
    assert page.evaluate("typeof window.__releaseQuarterPut === 'function'")
    page.evaluate(
        """
        async () => {
          const second = window.__addQuarterHash("b");
          if (!window.__quarterPutHeld) {
            throw new Error("second mutation started after delayed write was released");
          }
          window.__secondMutationStartedWhileFirstHeld = true;
          window.__releaseQuarterPut();
          await Promise.all([window.__firstQuarterPut, second]);
        }
        """
    )
    assert page.evaluate("window.__secondMutationStartedWhileFirstHeld === true")
    state = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert state["staging"]["verified_hashes"] == ["a", "b"]
    context.close()


def test_stale_quarter_generation_cannot_overwrite_new_staging(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    _wait_for_pwa_startup_queue_quiescent(page)
    page.evaluate(
        """
        async () => {
          const meta = await caches.open("bsb-meta-v1");
          const put = (path, value) => meta.put(
            new Request(new URL(`../__bsb_meta__/${path}`, location.href)),
            new Response(JSON.stringify(value)),
          );
          await put("queue.json", {
            schema: 2,
            generation: "generation-new",
            state: "downloading",
            labels: ["2026-07"],
            current: "2026-07",
            succeeded: [],
            failed: [],
            errors: [],
          });
          await put("quarters/2026-07.json", {
            schema: 1,
            quarter: "2026-07",
            status: "INCOMPLETE",
            active: null,
            staging: {
              quarter: "2026-07",
              revision: "generation-new-revision",
              resources: [],
              verified_hashes: ["new"],
            },
            error: null,
          });
          try {
            await window.BsbPwa.__updateQuarterDownloadState(
              "2026-07",
              "generation-old",
              () => ({
                state: {
                  status: "INCOMPLETE",
                  staging: { verified_hashes: ["old"] },
                },
              }),
            );
            throw new Error("stale generation unexpectedly wrote");
          } catch (error) {
            if (error.message === "stale generation unexpectedly wrote") throw error;
          }
        }
        """
    )
    state = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert state["staging"]["revision"] == "generation-new-revision"
    assert state["staging"]["verified_hashes"] == ["new"]
    context.close()


def test_new_generation_clears_stale_progress_before_manifest_failure(
    chromium: Browser,
    failing_manifest_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{failing_manifest_server}/settings/index.html")
    _wait_for_pwa_startup_queue_quiescent(page)
    page.evaluate(
        """
        async () => {
          const meta = await caches.open("bsb-meta-v1");
          const put = (path, value) => meta.put(
            new Request(new URL(`../__bsb_meta__/${path}`, location.href)),
            new Response(JSON.stringify(value)),
          );
          await put("queue.json", {
            schema: 2,
            generation: "old-generation",
            state: "cancelled",
            labels: [],
            current: null,
            succeeded: [],
            failed: [],
            errors: [],
          });
          await put("quarters/2026-07.json", {
            schema: 1,
            quarter: "2026-07",
            status: "INCOMPLETE",
            active: null,
            staging: null,
            error: null,
          });
          await put("progress/2026-07.json", {
            quarter: "2026-07",
            verified_resources: 7,
            total_resources: 9,
            verified_bytes: 70,
            total_bytes: 90,
          });
        }
        """
    )
    generation = page.evaluate(
        "async () => (await window.BsbPwa.enqueue(['2026-07'])).generation"
    )
    page.wait_for_function(
        """
        async (expectedGeneration) => {
          const queue = await window.BsbPwa.currentQueue();
          return queue.generation === expectedGeneration
            && queue.state === "idle"
            && queue.failed.includes("2026-07");
        }
        """,
        arg=generation,
    )
    page.wait_for_function(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          return !await cache.match(new Request(new URL(
            "../__bsb_meta__/progress/2026-07.json", location.href)));
        }
        """
    )
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


def test_cancelled_generation_stops_resource_retry(
    chromium: Browser,
    flaky_resource_server: tuple[str, list[int]],
) -> None:
    pwa_server, attempts = flaky_resource_server
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.current === '2026-07')"
    )
    for _ in range(80):
        if attempts[0] >= 1:
            break
        page.wait_for_timeout(25)
    assert attempts[0] == 1
    page.evaluate("async () => window.BsbPwa.cancelQueue()")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.state === 'cancelled')"
    )
    page.wait_for_timeout(1200)
    assert attempts[0] == 1
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


def test_remove_current_queue_quarter_requires_cancel_first(
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
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.current === '2026-07')"
    )
    result = page.evaluate(
        """
        async () => {
          try {
            await window.BsbPwa.removeQuarter("2026-07");
            return null;
          } catch (error) {
            return error.message;
          }
        }
        """
    )
    assert "取消当前下载或更新" in result
    assert page.evaluate(
        "async () => (await window.BsbPwa.currentQueue()).current"
    ) == "2026-07"
    page.evaluate("async () => window.BsbPwa.cancelQueue()")
    context.close()


def test_remove_quarter_waits_for_runner_before_gc(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    manifest = json.loads(
        (pwa_site / "data" / "offline" / "2026-07.json").read_text("utf-8")
    )
    target = next(
        item for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate(
        """
        () => {
          const nativePut = Cache.prototype.put;
          window.__contentPutStarted = false;
          window.__releaseContentPut = null;
          Cache.prototype.put = async function(request, response) {
            if (
              !window.__contentPutStarted
              && request.url.includes("/__bsb_content__/")
            ) {
              window.__contentPutStarted = true;
              await new Promise((resolve) => {
                window.__releaseContentPut = resolve;
              });
            }
            return nativePut.call(this, request, response);
          };
        }
        """
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.wait_for_function("window.__contentPutStarted === true")
    page.evaluate("async () => window.BsbPwa.cancelQueue()")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.state === 'cancelled')"
    )
    page.evaluate(
        """
        () => {
          window.__removeDone = false;
          window.__removeError = null;
          window.__removePromise = window.BsbPwa.removeQuarter('2026-07')
            .then(() => { window.__removeDone = true; })
            .catch((error) => { window.__removeError = error.message; });
        }
        """
    )
    page.wait_for_timeout(100)
    assert page.evaluate("window.__removeDone") is False
    assert page.evaluate("window.__removeError") is None
    page.evaluate("window.__releaseContentPut()")
    page.wait_for_function(
        "window.__removeDone || window.__removeError !== null"
    )
    assert page.evaluate("window.__removeError") is None
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "NONE"
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        target["content_hash"],
    ) is False
    context.close()


def test_remove_pending_queue_quarter_scrubs_it_before_runner_reaches_it(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()

    def delay_current_manifest(route) -> None:
        time.sleep(0.5)
        route.continue_()

    page.route("**/data/offline/2026-07.json", delay_current_manifest)
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07', '2026-04'])")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => queue.current === '2026-07')"
    )
    page.evaluate("async () => window.BsbPwa.removeQuarter('2026-04')")
    _wait_for_queue(page, 1)
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["labels"] == ["2026-07"]
    assert queue["succeeded"] == ["2026-07"]
    assert not any("data/offline/2026-04.json" in url for url in requests)
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-04')).status"
    ) == "NONE"
    context.close()


def test_update_detection_does_not_resurrect_removed_quarter_during_fetch(
    chromium: Browser,
    delayed_manifest_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{delayed_manifest_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    page.evaluate(
        """
        async (manifest) => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "COMPLETE",
              active: { ...manifest, revision: "old-revision" },
              staging: null,
              error: null,
            })),
          );
        }
        """,
        manifest,
    )

    _arm_delayed_manifest(page)
    changed = page.evaluate(
        """
        async () => {
          const detection = window.BsbPwa.detectUpdates();
          const removal = new Promise((resolve, reject) => {
            setTimeout(() => window.BsbPwa.removeQuarter("2026-07")
              .then(resolve, reject), 25);
          });
          const result = await detection;
          await removal;
          return result;
        }
        """
    )
    assert changed["dataUpdates"] == []
    assert changed["packageMaintenance"] == []
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "NONE"
    context.close()


def test_update_detection_does_not_overwrite_completed_update(
    chromium: Browser,
    delayed_manifest_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{delayed_manifest_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    page.evaluate(
        """
        async (manifest) => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "COMPLETE",
              active: { ...manifest, revision: "old-revision" },
              staging: null,
              error: null,
            })),
          );
        }
        """,
        manifest,
    )

    _arm_delayed_manifest(page)
    result = page.evaluate(
        """
        async () => {
          const detection = window.BsbPwa.detectUpdates();
          const update = new Promise((resolve, reject) => {
            setTimeout(async () => {
              try {
                await window.BsbPwa.enqueue(["2026-07"]);
                for (let attempt = 0; attempt < 400; attempt += 1) {
                  const queue = await window.BsbPwa.currentQueue();
                  if (queue.state === "idle" && queue.succeeded.includes("2026-07")) {
                    resolve();
                    return;
                  }
                  await new Promise((wait) => setTimeout(wait, 25));
                }
                reject(new Error("update did not finish"));
              } catch (error) {
                reject(error);
              }
            }, 25);
          });
          const changed = await detection;
          await update;
          return {
            changed,
            state: await window.BsbPwa.getQuarterState("2026-07"),
          };
        }
        """
    )
    assert result["changed"]["dataUpdates"] == []
    assert result["changed"]["packageMaintenance"] == []
    assert result["state"]["active"]["revision"] == manifest["revision"]
    assert result["state"]["staging"] is None
    context.close()


def test_update_detection_preserves_partial_staging_during_fetch(
    chromium: Browser,
    delayed_manifest_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{delayed_manifest_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifest = page.evaluate(
        "fetch('../data/offline/2026-07.json').then((response) => response.json())"
    )
    page.evaluate(
        """
        async (manifest) => {
          const meta = await caches.open("bsb-meta-v1");
          await meta.put(
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json", location.href)),
            new Response(JSON.stringify({
              schema: 1,
              quarter: "2026-07",
              status: "COMPLETE",
              active: { ...manifest, revision: "old-revision" },
              staging: null,
              error: null,
            })),
          );
        }
        """,
        manifest,
    )
    staged_hash = manifest["resources"][0]["content_hash"]

    _arm_delayed_manifest(page)
    result = page.evaluate(
        """
        async ({ manifest, stagedHash }) => {
          const detection = window.BsbPwa.detectUpdates();
          const staging = new Promise((resolve) => {
            setTimeout(async () => {
              const meta = await caches.open("bsb-meta-v1");
              await meta.put(
                new Request(new URL(
                  "../__bsb_meta__/quarters/2026-07.json", location.href)),
                new Response(JSON.stringify({
                  schema: 1,
                  quarter: "2026-07",
                  status: "INCOMPLETE",
                  active: { ...manifest, revision: "old-revision" },
                  staging: { ...manifest, verified_hashes: [stagedHash] },
                  error: null,
                })),
              );
              resolve();
            }, 25);
          });
          const changed = await detection;
          await staging;
          return {
            changed,
            state: await window.BsbPwa.getQuarterState("2026-07"),
          };
        }
        """,
        {"manifest": manifest, "stagedHash": staged_hash},
    )
    assert result["changed"]["dataUpdates"] == []
    assert result["changed"]["packageMaintenance"] == []
    assert result["state"]["status"] == "INCOMPLETE"
    assert result["state"]["staging"]["verified_hashes"] == [staged_hash]
    context.close()


def test_update_detection_separates_data_and_package_revisions(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    def write_state(active: dict[str, object]) -> None:
        page.evaluate(
            """
            async (active) => {
              const meta = await caches.open("bsb-meta-v1");
              await meta.put(
                new Request(new URL(
                  "../__bsb_meta__/quarters/2026-07.json", location.href)),
                new Response(JSON.stringify({
                  schema: 1,
                  quarter: "2026-07",
                  status: "COMPLETE",
                  active,
                  staging: null,
                  error: null,
                })),
              );
            }
            """,
            active,
        )

    write_state({**manifest, "revision": "old-package-revision"})
    manifest["revision"] = "package-only-revision"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    package_only = page.evaluate("async () => window.BsbPwa.detectUpdates()")
    assert package_only == {
        "dataUpdates": [],
        "packageMaintenance": ["2026-07"],
        "current": [],
    }
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "COMPLETE"

    legacy = {key: value for key, value in manifest.items() if key != "data_revision"}
    write_state({**legacy, "revision": "legacy-package-revision"})
    manifest["revision"] = "legacy-migrated-revision"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    migrated = page.evaluate("async () => window.BsbPwa.detectUpdates()")
    assert migrated["dataUpdates"] == []
    assert migrated["packageMaintenance"] == ["2026-07"]
    migrated_state = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert migrated_state["active"]["data_revision"] == manifest["data_revision"]

    manifest["revision"] = "data-update-revision"
    manifest["data_revision"] = "d" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    data_update = page.evaluate("async () => window.BsbPwa.detectUpdates()")
    assert data_update["dataUpdates"] == ["2026-07"]
    assert data_update["packageMaintenance"] == []
    assert page.evaluate(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status"
    ) == "UPDATE_AVAILABLE"
    context.close()


def test_auto_maintenance_is_nonblocking_and_active_only(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    _seed_quarter_state(page, "2026-04", {
        "schema": 1,
        "quarter": "2026-04",
        "status": "INCOMPLETE",
        "active": None,
        "staging": {"revision": "incomplete"},
        "error": "test",
    })
    _seed_quarter_state(page, "2026-01", {
        "schema": 1,
        "quarter": "2026-01",
        "status": "INCOMPLETE",
        "active": None,
        "staging": {"revision": "incomplete"},
        "error": "test",
    })
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    def delay_manifest(route) -> None:
        time.sleep(0.5)
        route.continue_()

    page.route("**/data/offline/2026-07.json", delay_manifest)
    page.goto(f"{pwa_server}/2026-07/index.html", wait_until="domcontentloaded")
    page.wait_for_selector(".subject-row", timeout=1000)
    assert page.locator("main[data-page=quarter]").is_visible()
    page.unroute("**/data/offline/2026-07.json")
    page.wait_for_function(
        "sessionStorage.getItem('bsb-offline-auto-maintenance') === 'complete'"
    )
    manifest_requests = [url for url in requests if "data/offline/" in url]
    assert any("data/offline/2026-07.json" in url for url in manifest_requests)
    assert not any("data/offline/2026-04.json" in url for url in manifest_requests)
    assert not any("data/offline/2026-01.json" in url for url in manifest_requests)
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["state"] == "idle"
    assert queue["succeeded"] == ["2026-07"]
    assert queue["failed"] == []
    context.close()


def test_auto_maintenance_updates_data_and_package_without_data_notice(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)

    quarter_path = pwa_site / "data" / "quarters" / "2026-07.json"
    changed_bytes = quarter_path.read_bytes() + b" "
    quarter_path.write_bytes(changed_bytes)
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    data_resource = next(
        item for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    data_resource["content_hash"] = hashlib.sha256(changed_bytes).hexdigest()
    data_resource["size_bytes"] = len(changed_bytes)
    manifest["revision"] = "d" * 64
    manifest["data_revision"] = data_resource["content_hash"]
    _write_offline_manifest(pwa_site, "2026-07", manifest)
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    _wait_for_queue(page, 1)
    data_state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert data_state["status"] == "COMPLETE"
    assert data_state["active"]["revision"] == "d" * 64
    assert data_state["active"]["data_revision"] == manifest["data_revision"]
    assert data_state["staging"] is None

    manifest["revision"] = "p" * 64
    _write_offline_manifest(pwa_site, "2026-07", manifest)
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    _wait_for_queue(page, 1)
    package_state = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert package_state["status"] == "COMPLETE"
    assert package_state["active"]["revision"] == "p" * 64
    assert package_state["active"]["data_revision"] == manifest["data_revision"]
    assert package_state["staging"] is None
    page.goto(f"{pwa_server}/settings/index.html")
    row = page.locator('[data-offline-quarter="2026-07"]')
    row.get_by_text("已下载").wait_for(state="visible")
    assert "有更新" not in row.inner_text()
    context.close()


def test_auto_package_failure_keeps_active_downloaded_and_records_error(
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
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    original = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    app_resource = next(
        item for item in manifest["resources"] if item["url"] == "assets/app.js"
    )
    app_resource["content_hash"] = "e" * 64
    manifest["revision"] = "f" * 64
    _write_offline_manifest(pwa_site, "2026-07", manifest)
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    _wait_for_queue(page, 1)
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert state["status"] == "COMPLETE"
    assert state["active"]["revision"] == original["active"]["revision"]
    assert state["staging"] is None
    assert state["error"]
    assert any(error["quarter"] == "2026-07" for error in queue["errors"])
    context.close()


def test_auto_maintenance_session_guard_and_offline_recovery(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    page.wait_for_function(
        "sessionStorage.getItem('bsb-offline-auto-maintenance') === 'complete'"
    )
    page.goto(f"{pwa_server}/2026-07/index.html")
    page.goto(f"{pwa_server}/archive/index.html")
    page.goto(f"{pwa_server}/settings/index.html")
    manifest_requests = [url for url in requests if "data/offline/2026-07.json" in url]
    assert len(manifest_requests) == 1

    page.wait_for_timeout(300)
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    requests.clear()
    context.set_offline(True)
    page.wait_for_function("navigator.onLine === false")
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    page.wait_for_timeout(150)
    assert not any("data/offline/2026-07.json" in url for url in requests)
    context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function(
        "sessionStorage.getItem('bsb-offline-auto-maintenance') === 'complete'"
    )
    assert any("data/offline/2026-07.json" in url for url in requests)
    context.close()


def test_auto_maintenance_merges_existing_queue_generation(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["revision"] = "m" * 64
    _write_offline_manifest(pwa_site, "2026-07", manifest)
    manifest_04 = json.loads(
        (pwa_site / "data" / "offline" / "2026-04.json").read_text("utf-8")
    )
    _seed_quarter_state(page, "2026-04", {
        "schema": 1,
        "quarter": "2026-04",
        "status": "COMPLETE",
        "active": manifest_04,
        "staging": None,
        "error": None,
    })
    generation = "manual-generation"
    page.evaluate(
        """
        async (generation) => {
          const meta = await caches.open('bsb-meta-v1');
          await meta.put(
            new Request(new URL('../__bsb_meta__/queue.json', location.href)),
            new Response(JSON.stringify({
              schema: 2,
              generation,
              state: 'paused',
              labels: ['2026-04'],
              current: null,
              succeeded: [],
              failed: [],
              errors: [],
            })),
          );
        }
        """,
        generation,
    )
    page.evaluate("sessionStorage.removeItem('bsb-offline-auto-maintenance')")
    page.evaluate("async () => window.BsbPwa.autoMaintainDownloadedQuarters()")
    page.wait_for_function(
        "window.BsbPwa.currentQueue().then((queue) => "
        "queue.generation === 'manual-generation' "
        "&& queue.state === 'paused' "
        "&& queue.labels.includes('2026-04') "
        "&& queue.labels.includes('2026-07'))"
    )
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert queue["generation"] == generation
    assert queue["labels"] == ["2026-07", "2026-04"]
    assert queue["state"] == "paused"
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
        "?.textContent.includes('更新未完成')"
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


def test_final_promotion_repairs_missing_verified_content_blob(
    chromium: Browser,
    pwa_server: str,
    pwa_site: Path,
) -> None:
    manifest = json.loads(
        (pwa_site / "data" / "offline" / "2026-07.json").read_text("utf-8")
    )
    target = next(
        item for item in manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    unique_count = len({item["content_hash"] for item in manifest["resources"]})
    context = chromium.new_context()
    context.add_init_script(
        f"""
        (() => {{
          const nativePut = Cache.prototype.put;
          const targetHash = {json.dumps(target['content_hash'])};
          const uniqueCount = {unique_count};
          let deleted = false;
          window.__closureDeleted = false;
          Cache.prototype.put = async function(request, response) {{
            const copy = response.clone();
            const result = await nativePut.call(this, request, response);
            if (!deleted && request.url.includes(
              "/__bsb_meta__/quarters/2026-07.json")) {{
              try {{
                const value = await copy.json();
                const hashes = new Set(value.staging?.verified_hashes || []);
                if (hashes.size >= uniqueCount) {{
                  deleted = true;
                  window.__closureDeleted = true;
                  const content = await caches.open("bsb-content-v1");
                  await content.delete(new Request(new URL(
                    `../__bsb_content__/${{targetHash}}`, location.href)));
                }}
              }} catch {{}}
            }}
            return result;
          }};
        }})();
        """
    )
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate(
        """
        async (resource) => {
          const runtime = await caches.open("bsb-runtime-v1");
          const candidates = [
            resource.url,
            `${resource.url}?v=${resource.content_hash}`,
          ];
          for (const url of candidates) {
            await runtime.delete(new Request(new URL(`../${url}`, location.href)));
          }
        }
        """,
        target,
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert page.evaluate("window.__closureDeleted")
    assert state["status"] == "COMPLETE"
    assert state["staging"] is None
    target_requests = [url for url in requests if url.endswith(target["url"])]
    assert target_requests
    assert page.evaluate(
        """
        async (hash) => {
          const content = await caches.open("bsb-content-v1");
          return Boolean(await content.match(new Request(new URL(
            `../__bsb_content__/${hash}`, location.href))));
        }
        """,
        target["content_hash"],
    )
    context.close()


def test_missing_verified_blob_repair_failure_keeps_old_active_incomplete(
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
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    original = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    target = next(
        item for item in original["active"]["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    unique_count = len({
        item["content_hash"] for item in original["active"]["resources"]
    })
    page.evaluate(
        """
        ({ targetHash, uniqueCount }) => {
          const nativePut = Cache.prototype.put;
          let deleted = false;
          window.__closureDeleted = false;
          Cache.prototype.put = async function(request, response) {
            const copy = response.clone();
            const result = await nativePut.call(this, request, response);
            if (!deleted && request.url.includes(
              "/__bsb_meta__/quarters/2026-07.json")) {
              try {
                const value = await copy.json();
                const hashes = new Set(value.staging?.verified_hashes || []);
                if (hashes.size >= uniqueCount) {
                  deleted = true;
                  window.__closureDeleted = true;
                  const content = await caches.open("bsb-content-v1");
                  await content.delete(new Request(new URL(
                    `../__bsb_content__/${targetHash}`, location.href)));
                }
              } catch {}
            }
            return result;
          };
        }
        """,
        {"targetHash": target["content_hash"], "uniqueCount": unique_count},
    )
    manifest_path = pwa_site / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["revision"] = "closure-repair-failure-revision"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    (pwa_site / target["url"]).write_bytes(b"closure repair must fail")
    page.evaluate(
        """
        async (resource) => {
          const runtime = await caches.open("bsb-runtime-v1");
          const candidates = [
            resource.url,
            `${resource.url}?v=${resource.content_hash}`,
          ];
          for (const url of candidates) {
            await runtime.delete(new Request(new URL(`../${url}`, location.href)));
          }
        }
        """,
        target,
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    failed = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert page.evaluate("window.__closureDeleted")
    assert failed["status"] == "INCOMPLETE"
    assert failed["active"]["revision"] == original["active"]["revision"]
    assert failed["staging"]["revision"] == "closure-repair-failure-revision"
    assert failed["staging"]["verified_hashes"]
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
        "data_revision": "b" * 64,
        "resources": [
            {"url": "index.html", "content_hash": "a" * 64, "size_bytes": 1}
        ],
    }
    assert page.evaluate(
        "([value]) => window.BsbPwa.validateQuarterManifest(value, '2026-07')",
        [valid],
    )["quarter"] == "2026-07"
    invalid_data_revision = {**valid, "data_revision": "invalid"}
    assert page.evaluate(
        """
        ([value]) => {
          try {
            window.BsbPwa.validateQuarterManifest(value, "2026-07");
            return false;
          } catch { return true; }
        }
        """,
        [invalid_data_revision],
    )
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
    alias = {**valid, "resources": [
        valid["resources"][0],
        {**valid["resources"][0], "url": "alias.html"},
    ]}
    assert page.evaluate(
        "([value]) => window.BsbPwa.validateQuarterManifest(value, '2026-07')",
        [alias],
    )["resources"][1]["content_hash"] == "a" * 64
    conflicting_size = {**valid, "resources": [
        valid["resources"][0],
        {**valid["resources"][0], "url": "conflict.html", "size_bytes": 2},
    ]}
    assert page.evaluate(
        """
        ([value]) => {
          try {
            window.BsbPwa.validateQuarterManifest(value, "2026-07");
            return false;
          } catch { return true; }
        }
        """,
        [conflicting_size],
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
            new Request(new URL(
              "../__bsb_meta__/quarters/2026-07.json", location.href)),
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
    for heading in ("离线季度", "下载任务", "批量下载", "存储与应用", "高级诊断"):
        assert page.get_by_role("heading", name=heading).is_visible()
    assert page.locator("select").count() == 0
    assert page.get_by_text("2.0 KiB").is_visible()
    assert page.get_by_text("8.0 KiB").is_visible()
    assert page.evaluate("window.__persistCalls") == 0
    page.get_by_role("button", name="申请持久存储").click()
    page.wait_for_function("window.__persistCalls === 1")
    assert page.get_by_text("Persistent storage: not granted").is_visible()
    assert page.get_by_role("button", name="安装应用").count() == 0
    assert page.get_by_text("添加到主屏幕").is_visible()

    selector = page.locator("[data-queue-kind]")
    assert selector.locator('[role="option"]').all_text_contents() == [
        "当前季度",
        "指定年份",
        "年份范围",
        "全部季度",
    ]
    kind_trigger = selector.locator(".select-trigger")
    kind_trigger.press("Enter")
    assert selector.locator('[role="listbox"]').is_visible()
    kind_trigger.press("Escape")
    assert selector.locator('[role="listbox"]').is_hidden()
    assert kind_trigger.evaluate("node => node === document.activeElement")
    july = page.locator('[data-offline-quarter="2026-07"]')
    july.get_by_role("button", name="下载").click()
    _wait_for_queue(page, 1)
    page.evaluate("window.dispatchEvent(new CustomEvent('bsb:pwa-state'))")
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('已下载')"
    )
    july.get_by_role("button", name="…").click()
    assert july.get_by_role("button", name="移除离线资料").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    july.get_by_role("button", name="移除离线资料").click()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('未下载')"
    )
    context.close()


def test_settings_progress_keeps_selector_identity_focus_and_skips_storage_refresh(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    context.add_init_script(
        """
        window.__estimateCalls = 0;
        Object.defineProperty(navigator, "storage", {
          configurable: true,
          value: {
            estimate: async () => {
              window.__estimateCalls += 1;
              return { usage: 1024, quota: 8192 };
            },
            persisted: async () => false,
          },
        });
        """
    )
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function(
        "document.querySelector('[data-queue-kind] .select-trigger') !== null"
    )
    selector = page.locator("[data-settings-selector]")
    selector.evaluate("node => { node.dataset.identityMarker = 'stable'; }")
    trigger = page.locator("[data-queue-kind] .select-trigger")
    trigger.press("Enter")
    assert page.locator('[data-queue-kind] [role="listbox"]').is_visible()
    estimate_calls = page.evaluate("window.__estimateCalls")
    generation = page.evaluate(
        "async () => { const queue = await window.BsbPwa.enqueue(['2026-07']); "
        "await window.BsbPwa.pauseQueue(); return queue.generation; }"
    )
    page.evaluate(
        "async () => { const cache = await caches.open('bsb-meta-v1'); "
        "const request = new Request(new URL('../__bsb_meta__/queue.json', "
        "location.href)); "
        "const queue = await (await cache.match(request)).json(); "
        "queue.current = '2026-07'; "
        "await cache.put(request, new Response(JSON.stringify(queue), "
        "{headers: {'Content-Type': 'application/json'}})); }"
    )
    update_result = page.evaluate(
        "async generation => window.BsbPwa.__updateQuarterDownloadState("
        "'2026-07', generation, () => ({progress: {quarter: '2026-07', "
        "verified_resources: 3, total_resources: 10, verified_bytes: 30, "
        "total_bytes: 100}}))",
        generation,
    )
    assert update_result["progress"]["verified_resources"] == 3
    page.wait_for_function(
        "document.querySelector('[data-queue-progress]')?.textContent.includes('30%')"
    )
    assert selector.get_attribute("data-identity-marker") == "stable"
    assert page.locator('[data-queue-kind] [role="listbox"]').is_visible()
    assert page.evaluate("window.__estimateCalls") == estimate_calls
    context.close()


def test_settings_can_remove_incomplete_download_and_partial_content(
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
    (pwa_site / "data" / "quarters" / "2026-07.json").write_bytes(b"corrupt")
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    state = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    assert state["status"] == "INCOMPLETE"
    assert state["staging"]["verified_hashes"]
    quarter_page_hash = next(
        item["content_hash"]
        for item in state["staging"]["resources"]
        if item["url"] == "2026-07/index.html"
    )
    row = page.locator('[data-offline-quarter="2026-07"]')
    assert row.get_by_role("button", name="继续").is_visible()
    assert row.locator("[data-quarter-remove]").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    row.locator("[data-quarter-remove]").click()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('未下载')"
    )
    page.wait_for_function(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status === 'NONE'"
    )
    page.wait_for_function(
        """async (hash) => {
          const cache = await caches.open("bsb-content-v1");
          const response = await cache.match(new URL(
            `../__bsb_content__/${hash}`, location.href));
          return !response;
        }""",
        arg=quarter_page_hash,
    )
    assert page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.match(new URL(
            `../__bsb_content__/${hash}`, location.href)))
          .then(Boolean)
        """,
        quarter_page_hash,
    ) is False
    assert page.evaluate(
        "async () => (await window.BsbPwa.currentQueue()).progress"
    ) is None
    context.close()


def test_settings_can_remove_update_incomplete_and_keep_shared_content(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{pwa_server}/settings/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    manifests = page.evaluate(
        """
        async () => Promise.all([
          fetch('../data/offline/2026-04.json').then((response) => response.json()),
          fetch('../data/offline/2026-07.json').then((response) => response.json()),
        ])
        """
    )
    shared_hash = next(
        item["content_hash"]
        for item in manifests[0]["resources"]
        if item["url"] == "covers/101.webp"
    )
    unique_hash = next(
        item["content_hash"]
        for item in manifests[1]["resources"]
        if item["url"] == "2026-07/index.html"
    )
    page.evaluate(
        """
        async ({ april, july, sharedHash, uniqueHash }) => {
          const meta = await caches.open("bsb-meta-v1");
          const put = (path, value) => meta.put(
            new Request(new URL(`../__bsb_meta__/${path}`, location.href)),
            new Response(JSON.stringify(value)),
          );
          await put("queue.json", {
            schema: 2,
            generation: null,
            state: "idle",
            labels: [],
            current: null,
            succeeded: [],
            failed: [],
            errors: [],
          });
          await put("quarters/2026-04.json", {
            schema: 1,
            quarter: "2026-04",
            status: "COMPLETE",
            active: april,
            staging: null,
            error: null,
          });
          await put("quarters/2026-07.json", {
            schema: 1,
            quarter: "2026-07",
            status: "INCOMPLETE",
            active: july,
            staging: {
              ...july,
              revision: "update-revision",
              verified_hashes: [sharedHash, uniqueHash],
            },
            error: "update paused",
          });
          const content = await caches.open("bsb-content-v1");
          for (const hash of [sharedHash, uniqueHash]) {
            await content.put(
              new Request(new URL(`../__bsb_content__/${hash}`, location.href)),
              new Response(hash),
            );
          }
        }
        """,
        {
            "april": manifests[0],
            "july": manifests[1],
            "sharedHash": shared_hash,
            "uniqueHash": unique_hash,
        },
    )
    page.reload()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('更新未完成')"
    )
    row = page.locator('[data-offline-quarter="2026-07"]')
    assert row.get_by_role("button", name="继续更新").is_visible()
    assert row.get_by_role("button", name="移除离线数据").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name="移除离线数据").click()
    page.wait_for_function(
        "document.querySelector('[data-offline-quarter=\"2026-07\"]')"
        "?.textContent.includes('未下载')"
    )
    page.wait_for_function(
        "async () => (await window.BsbPwa.getQuarterState('2026-07')).status === 'NONE'"
    )
    page.wait_for_function(
        """async (hash) => {
          const cache = await caches.open("bsb-content-v1");
          const response = await cache.match(new URL(
            `../__bsb_content__/${hash}`, location.href));
          return !response;
        }""",
        arg=unique_hash,
    )
    assert page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.match(new URL(
            `../__bsb_content__/${hash}`, location.href)))
          .then(Boolean)
        """,
        shared_hash,
    ) is True
    assert page.evaluate(
        """
        (hash) => caches.open("bsb-content-v1")
          .then((cache) => cache.match(new URL(
            `../__bsb_content__/${hash}`, location.href)))
          .then(Boolean)
        """,
        unique_hash,
    ) is False
    context.close()


def test_quarter_page_offline_action_tracks_download_and_confirmed_remove(
    chromium: Browser,
    pwa_server: str,
) -> None:
    context = chromium.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{pwa_server}/2026-07/index.html")
    page.wait_for_function("Boolean(window.BsbPwa)")
    page.get_by_role("button", name="菜单").click()
    control = page.locator("[data-mobile-quarter-offline]")
    control.get_by_role("button", name="下载当前季度供离线使用").click()
    _wait_for_queue(page, 1)
    page.wait_for_function(
        "document.querySelector('[data-mobile-quarter-offline-status]')"
        "?.textContent.includes('已下载')"
    )
    assert control.get_by_role("button", name="移除离线缓存").is_visible()
    page.on("dialog", lambda dialog: dialog.accept())
    control.get_by_role("button", name="移除离线缓存").click()
    page.wait_for_function(
        "document.querySelector('[data-mobile-quarter-offline-status]')"
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
    downloaded = page.locator('[data-offline-quarter="2026-07"]').get_by_text(
        "已下载"
    )
    downloaded.wait_for(state="visible")
    assert downloaded.is_visible()

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


def test_auto_maintenance_rechecks_offline_updates_after_reconnect(
    chromium: Browser,
    update_server: str,
    update_site: tuple[object, object, Path, Path],
) -> None:
    _builder, _database, _isolated_root, served = update_site
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{update_server}/settings/index.html")
    page.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    _wait_for_queue(page, 1)
    manifest_path = served / "data" / "offline" / "2026-07.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["revision"] = "reconnect-update-revision"
    manifest["data_revision"] = "f" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    context.set_offline(True)
    page.reload()
    page.wait_for_function(
        "document.querySelector('[data-page=\"settings\"]') "
        "&& Boolean(window.BsbPwa)"
    )
    context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_timeout(1000)
    after = page.evaluate("async () => window.BsbPwa.getQuarterState('2026-07')")
    queue = page.evaluate("async () => window.BsbPwa.currentQueue()")
    assert after["status"] == "COMPLETE", (after, queue, page.evaluate(
        "sessionStorage.getItem('bsb-offline-auto-maintenance')"
    ))
    assert after["active"]["revision"] == "reconnect-update-revision", after
    assert after["staging"] is None
    assert queue["state"] == "idle"
    assert queue["current"] is None
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

    with page.expect_navigation():
        assert page.evaluate("async () => window.BsbPwa.refreshApp()") is True
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


def test_shell_activation_gc_serializes_with_quarter_download(
    chromium: Browser,
    update_server: str,
    update_site: tuple[object, object, Path, Path],
) -> None:
    builder, _database, isolated_root, served = update_site
    context = chromium.new_context()
    page = context.new_page()
    page.goto(f"{update_server}/settings/index.html")
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    control = context.new_page()
    control.goto(f"{update_server}/settings/index.html")
    control.wait_for_function("navigator.serviceWorker.controller !== null")
    shell_before = page.evaluate(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          const response = await cache.match(new Request(new URL(
            "../__bsb_meta__/shell.json", location.href)));
          return response.json();
        }
        """
    )
    page.evaluate(
        """
        () => {
          const nativePut = Cache.prototype.put;
          window.__contentPutStarted = false;
          window.__releaseContentPut = null;
          Cache.prototype.put = async function(request, response) {
            if (
              !window.__contentPutStarted
              && request.url.includes("/__bsb_content__/")
            ) {
              window.__contentPutStarted = true;
              await new Promise((resolve) => {
                window.__releaseContentPut = resolve;
              });
            }
            return nativePut.call(this, request, response);
          };
        }
        """
    )
    page.evaluate("async () => window.BsbPwa.enqueue(['2026-07'])")
    page.wait_for_function("window.__contentPutStarted === true")

    pwa_source = isolated_root / "static" / "js" / "pwa.js"
    pwa_source.write_text(
        pwa_source.read_text("utf-8") + "\n/* activation race fixture */\n",
        encoding="utf-8",
    )
    builder.build()
    worker_stat = (served / "sw.js").stat()
    os.utime(served / "sw.js", (worker_stat.st_atime + 2, worker_stat.st_mtime + 2))
    control.evaluate(
        "navigator.serviceWorker.ready.then((registration) => registration.update())"
    )
    pending = control.evaluate(
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
    assert control.evaluate("async () => window.BsbPwa.refreshApp()") is True
    control.wait_for_timeout(100)
    shell_during = page.evaluate(
        """
        async () => {
          const cache = await caches.open("bsb-meta-v1");
          const response = await cache.match(new Request(new URL(
            "../__bsb_meta__/shell.json", location.href)));
          return response.json();
        }
        """
    )
    assert shell_during["revision"] == shell_before["revision"]
    assert pending["revision"] != shell_before["revision"]
    page.evaluate("window.__releaseContentPut()")
    _wait_for_queue(page, 1)
    page.wait_for_function(
        """
        async (revision) => {
          const cache = await caches.open("bsb-meta-v1");
          const response = await cache.match(new Request(new URL(
            "../__bsb_meta__/shell.json", location.href)));
          const value = response ? await response.json() : null;
          return value && value.revision !== revision;
        }
        """,
        arg=shell_before["revision"],
    )
    state = page.evaluate(
        "async () => window.BsbPwa.getQuarterState('2026-07')"
    )
    assert state["status"] == "COMPLETE"
    assert state["staging"] is None
    assert all(
        page.evaluate(
            """
            async (hash) => {
              const content = await caches.open("bsb-content-v1");
              return Boolean(await content.match(new Request(new URL(
                `../__bsb_content__/${hash}`, location.href))));
            }
            """,
            resource["content_hash"],
        )
        for resource in state["active"]["resources"]
    )
    context.set_offline(True)
    page.goto(f"{update_server}/2026-07/index.html")
    page.wait_for_selector('[data-subject-id="101"]')
    page.wait_for_function(
        "document.querySelector('[data-subject-id=\"101\"] img')?.complete"
    )
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
