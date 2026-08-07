"""Small Chromium regressions for portable, fully static archive output."""

from __future__ import annotations

import functools
import hashlib
import http.server
import json
import shutil
import socket
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Error, Page, sync_playwright

from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.release.manifest import (
    build_snapshot_manifest,
    index_candidate,
    manifest_json,
)
from bgm_side_b.repository import (
    EpisodeRecord,
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)

ROOT = Path(__file__).parents[1]


@pytest.fixture
def static_site(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "facts.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        for subject_id, month, title in (
            (101, 4, "很长的测试标题一"),
            (102, 1, "测试标题二"),
        ):
            repository.upsert_subject(
                connection,
                SubjectRecord(
                    subject_id,
                    "tv",
                    "用于 Chromium 回归的完整简介。" * 8,
                    date(2026, month, 1),
                    51,
                    7.5 if subject_id == 101 else None,
                    100 if subject_id == 101 else None,
                ),
            )
            repository.replace_titles(
                connection, subject_id, [SubjectTitle("preferred", title)]
            )
            repository.replace_infobox(
                connection,
                subject_id,
                [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", "Japan")],
            )
            repository.replace_quarters(
                connection, subject_id, [SubjectQuarter(2026, month, "new")]
            )
        repository.replace_main_episodes(
            connection,
            101,
            [
                EpisodeRecord(
                    number,
                    number,
                    number,
                    f"Episode {number}",
                    None,
                    None,
                    None,
                    None,
                    number - 1,
                )
                for number in range(1, 52)
            ],
        )
    settings, tags, sources = load_rules(ROOT / "config")
    ArchiveBuilder(
        ROOT,
        database,
        settings,
        tags,
        sources,
        workspace_directory=workspace,
        distribution_directory=tmp_path / "site" / "bangumi-side-b",
        reports_directory=tmp_path / "reports",
    ).build(None)
    return tmp_path / "site" / "bangumi-side-b"


@pytest.fixture
def browser() -> Browser:
    with sync_playwright() as runner:
        chromium = runner.chromium.launch()
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture
def firefox_browser() -> Browser:
    with sync_playwright() as runner:
        try:
            firefox = runner.firefox.launch()
        except Error:
            pytest.skip("Playwright Firefox is not installed")
        try:
            yield firefox
        finally:
            firefox.close()


def _published_snapshot(static_site: Path, name: str) -> Path:
    """Create an isolated, Pages-shaped candidate with one cached cover."""
    published_root = static_site.parent / name / "bangumi-side-b"
    shutil.copytree(static_site / "pages", published_root)
    cover = published_root / "media" / "covers" / "browser-fixture.png"
    cover.parent.mkdir(parents=True)
    shutil.copyfile(published_root / "icons" / "icon-192.png", cover)
    subject = published_root / "subjects" / "101" / "index.html"
    subject.write_text(
        subject.read_text("utf-8").replace(
            "</main>",
            "<img data-browser-cover "
            'src="../../media/covers/browser-fixture.png" alt="">\n</main>',
        ),
        encoding="utf-8",
    )
    entries = index_candidate(published_root, "/bangumi-side-b/")
    manifest = build_snapshot_manifest(
        entries,
        release_version="2026.07.30.1",
        app_version="0.1.0",
        deployment_path="/bangumi-side-b/",
    )
    manifest_text = manifest_json(manifest)
    (published_root / "snapshot-manifest.json").write_bytes(manifest_text.encode())
    (published_root / "release.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "release_version": "2026.07.30.1",
                "app_version": "0.1.0",
                "generated_at": "2026-07-31T00:00:00Z",
                "published_at": "2026-07-31T00:00:00Z",
                "quarter_count": 1,
                "subject_count": 1,
                "latest_quarter": "2026-04",
                "total_bytes": sum(entry.size_bytes for entry in entries),
                "content_hash": manifest.content_hash,
                "manifest_url": "snapshot-manifest.json",
                "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
                "change_kind": "data",
                "summary": {"system": [], "data": []},
            }
        ),
        "utf-8",
    )
    return published_root


def _pwa_server(
    published_root: Path,
) -> tuple[http.server.ThreadingHTTPServer, type[http.server.SimpleHTTPRequestHandler]]:
    target = "/bangumi-side-b/subjects/101/index.html"
    target_file = published_root / "subjects" / "101" / "index.html"

    class Handler(http.server.SimpleHTTPRequestHandler):
        mode = "normal"
        requests: list[str] = []

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(published_root.parent), **kwargs)

        def log_message(self, *_: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
            type(self).requests.append(self.path.split("?", 1)[0])
            if self.path.split("?", 1)[0] != target or type(self).mode == "normal":
                try:
                    super().do_GET()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                return
            if type(self).mode == "not-found":
                self.send_error(404)
                return
            original = target_file.read_bytes()
            if type(self).mode == "html":
                body = b"<!doctype html><title>not the requested file</title>"
            elif type(self).mode == "hash":
                body = bytes([original[0] ^ 1]) + original[1:]
            elif type(self).mode == "interrupt":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(original)))
                self.end_headers()
                self.wfile.write(original[:32])
                self.wfile.flush()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            else:
                raise AssertionError(f"unknown fixture mode: {type(self).mode}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler


def _wait_for_release(page: Page) -> None:
    page.wait_for_function(
        "window.BsbPwa.state().available_release?.release_version === '2026.07.30.1'"
    )


def _start_download(page: Page) -> None:
    page.evaluate("window.BsbPwa.initialize()")


def test_local_file_archive_restores_state_and_remains_offline(
    static_site: Path, browser: Browser
) -> None:
    page = browser.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto((static_site / "local" / "quarters" / "2026-04" / "index.html").as_uri())
    page.locator("[data-search-input]").fill("标题一")
    assert page.locator(".subject-card:not([hidden])").count() == 1
    page.locator("[data-open-drawer]").click()
    assert page.locator("#subject-drawer").evaluate("node => node.open")
    page.keyboard.press("Escape")
    page.locator(".subject-card__detail-link").click()
    assert page.locator("[data-subject-detail]").count() == 1
    page.locator("[data-toggle-episodes]").click()
    assert page.locator("[data-extra-episode][hidden]").count() == 0
    page.go_back()
    assert page.locator("[data-search-input]").input_value() == "标题一"
    assert all(url.startswith("file://") for url in requests)


def test_pages_subpath_loads_static_assets_without_character_media(
    static_site: Path, browser: Browser
) -> None:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(static_site.parent)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page = browser.new_page()
        page.goto(
            f"http://127.0.0.1:{server.server_port}/bangumi-side-b/pages/"
            "quarters/2026-04/index.html"
        )
        assert page.locator(".subject-card").count() == 1
        stylesheet = page.locator('link[rel="stylesheet"]').get_attribute("href")
        assert stylesheet is not None and not stylesheet.startswith("assets/")
        assert not (static_site / "pages" / "media" / "characters").exists()
    finally:
        server.shutdown()
        thread.join()


def test_pages_pwa_installs_a_complete_snapshot_and_navigates_offline(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "complete-published")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/settings/index.html")
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        _wait_for_release(page)
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
        assert (
            page.evaluate("window.BsbPwa.state().active.release_version")
            == "2026.07.30.1"
        )

        context.set_offline(True)
        page.goto(f"{root}/index.html")
        page.goto(f"{root}/quarters/2026-04/index.html")
        assert page.locator(".subject-card").count() == 1
        page.goto(f"{root}/subjects/101/index.html")
        page.locator("[data-browser-cover]").wait_for()
        page.wait_for_function(
            "document.querySelector('[data-browser-cover]').complete && "
            "document.querySelector('[data-browser-cover]').naturalWidth > 0"
        )
        page.reload()
        assert page.locator("[data-subject-detail]").count() == 1
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_gate_reloads_once_after_activation(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "gate-reload-published")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        navigations: list[str] = []
        page.on(
            "framenavigated",
            lambda frame: navigations.append(frame.url)
            if frame == page.main_frame
            else None,
        )
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/quarters/2026-04/index.html")
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        _wait_for_release(page)
        page.locator("[data-pwa-start]").click()
        page.wait_for_function("window.BsbPwa.state().active !== null")
        page.locator("[data-pwa-gate]").wait_for(state="hidden")
        assert page.locator(".subject-card").count() == 1
        assert navigations.count(f"{root}/quarters/2026-04/index.html") == 2
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_root_user_click_initializes_and_browses_offline(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "root-user-journey")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/")
        page.locator("[data-pwa-gate]").wait_for(state="visible")
        page.locator("[data-pwa-start]").click()
        page.locator(".subject-card").wait_for()
        page.wait_for_function("document.documentElement.classList.contains('js-ready')")
        page.locator("[data-open-drawer]").click()
        assert page.locator("#subject-drawer").evaluate("node => node.open")
        page.locator("#subject-drawer [data-detail-link]").click()
        page.locator("[data-subject-detail]").wait_for()
        context.set_offline(True)
        page.reload()
        assert page.locator("[data-subject-detail]").count() == 1
    finally:
        context.close()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("entry", "target"),
    (
        ("/settings/index.html", ".subject-card"),
        ("/quarters/2026-04/index.html", ".subject-card"),
        ("/subjects/101/index.html", "[data-subject-detail]"),
    ),
)
def test_pages_pwa_direct_entries_complete_initialization_with_user_click(
    static_site: Path, browser: Browser, entry: str, target: str
) -> None:
    published_root = _published_snapshot(static_site, f"direct-{entry.count('/')}")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}{entry}")
        if entry == "/settings/index.html":
            page.locator("[data-pwa-initialize]").click()
            page.wait_for_function("window.BsbPwa.state().status === 'ready'")
            page.locator("[data-pwa-enter]").click()
        else:
            page.locator("[data-pwa-gate]").wait_for(state="visible")
            page.locator("[data-pwa-start]").click()
        page.locator(target).wait_for()
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_clear_and_reinitialize_with_user_click(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "clear-reinitialize")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/settings/index.html")
        page.locator("[data-pwa-initialize]").click()
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("[data-pwa-clear]").click()
        page.locator("[data-pwa-initialize]").wait_for(state="visible")
        page.locator("[data-pwa-initialize]").click()
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
        assert page.evaluate("window.BsbPwa.state().staging") is None
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_firefox_user_click_initializes_when_available(
    static_site: Path, firefox_browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "firefox-user-journey")
    server, _ = _pwa_server(published_root)
    context = firefox_browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(20000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/settings/index.html")
        page.locator("[data-pwa-initialize]").click()
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
        page.locator("[data-pwa-enter]").click()
        page.locator(".subject-card").wait_for()
        context.set_offline(True)
        page.reload()
        assert page.locator(".subject-card").count() == 1
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_reports_faults_and_recovers_without_an_active_snapshot(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "fault-published")
    server, handler = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/settings/index.html")
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        _wait_for_release(page)

        handler.mode = "not-found"
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'failed'")
        failed = page.evaluate("window.BsbPwa.state()")
        failure = failed["staging"]["failure"]
        assert failure["error_code"] == "file-unavailable"
        assert failure["http_status"] == 404
        assert failure["failed_url"] == "/bangumi-side-b/subjects/101/index.html"
        assert "HTTP 404" in page.locator("[data-pwa-status]").inner_text()
        assert not page.locator("[data-pwa-resume-settings]").is_hidden()
        assert not page.locator("[data-pwa-cancel-settings]").is_hidden()

        handler.mode = "normal"
        commands = page.evaluate(
            "Promise.all([window.BsbPwa.resume(), window.BsbPwa.resume()])"
        )
        assert commands[1]["command_error"] == "operation-busy"
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
        page.evaluate("window.BsbPwa.clear()")
        page.wait_for_function(
            "window.BsbPwa.state().active === null && "
            "window.BsbPwa.state().staging === null"
        )

        handler.mode = "html"
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'failed'")
        assert (
            page.evaluate("window.BsbPwa.state().staging.failure.error_code")
            == "file-size-invalid"
        )
        page.locator("[data-pwa-cancel-settings]").click()
        page.wait_for_function("window.BsbPwa.state().staging === null")

        handler.mode = "hash"
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'failed'")
        assert (
            page.evaluate("window.BsbPwa.state().staging.failure.error_code")
            == "file-hash-invalid"
        )
        page.evaluate("window.BsbPwa.cancel()")
        page.wait_for_function("window.BsbPwa.state().staging === null")

        handler.mode = "interrupt"
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'failed'")
        assert (
            page.evaluate("window.BsbPwa.state().staging.failure.error_code")
            == "file-unavailable"
        )
        page.evaluate("window.BsbPwa.cancel()")
        page.wait_for_function("window.BsbPwa.state().staging === null")

        handler.mode = "normal"
        worker = context.service_workers[0]
        worker.evaluate(
            """() => {
              globalThis.__browserTestCachePut = Cache.prototype.put;
              Cache.prototype.put = function(request, response) {
                if (!new URL(request.url).pathname.includes('/__bsb_control__/')) {
                  throw new Error('fixture-cache-write-failed');
                }
                return globalThis.__browserTestCachePut.call(this, request, response);
              };
            }"""
        )
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'failed'")
        assert (
            page.evaluate("window.BsbPwa.state().staging.failure.error_code")
            == "file-cache-write-failed"
        )
        page.evaluate("window.BsbPwa.cancel()")
        page.wait_for_function("window.BsbPwa.state().staging === null")
        worker.evaluate(
            """() => { Cache.prototype.put = globalThis.__browserTestCachePut; }"""
        )

        handler.mode = "normal"
        _start_download(page)
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_recovers_when_the_command_owner_has_disappeared(
    static_site: Path, browser: Browser
) -> None:
    published_root = _published_snapshot(static_site, "orphaned-published")
    server, _ = _pwa_server(published_root)
    context = browser.new_context()
    try:
        page = context.new_page()
        page.set_default_timeout(15000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/settings/index.html")
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        _wait_for_release(page)
        page.evaluate(
            """async () => {
              const registration = await navigator.serviceWorker.ready;
              const key = new URL('__bsb_control__/state', registration.scope);
              const state = {
                schema: 2,
                active: null,
                staging: {
                  operation_id: 'orphaned-operation',
                  owner_client_id: 'closed-client',
                  lease_until: new Date(Date.now() + 120000).toISOString(),
                  release_version: null,
                  manifest_hash: null,
                  attempt_cache_name: 'bsb-snapshot-orphaned-operation',
                  status: 'downloading',
                  reason: 'resume',
                  completed_urls: [],
                  downloaded_bytes: 0,
                  total_bytes: 0,
                  updated_at: new Date().toISOString(),
                  last_error: null,
                  failure: null,
                },
                status: 'downloading',
                available_release: null,
                available_update: null,
                cleanup_warning: null,
              };
              await (await caches.open('bsb-control-v1')).put(
                key, new Response(JSON.stringify(state), {
                  headers: { 'Content-Type': 'application/json' },
                })
              );
            }"""
        )
        page.reload()
        page.wait_for_function(
            "window.BsbPwa.state().staging?.operation_id === 'orphaned-operation'"
        )
        page.evaluate("window.BsbPwa.resume()")
        page.wait_for_function("window.BsbPwa.state().status === 'ready'")
    finally:
        context.close()
        server.shutdown()
        server.server_close()


def test_pages_pwa_downloads_a_verified_snapshot_only_after_user_action(
    static_site: Path, browser: Browser
) -> None:
    published_root = static_site.parent / "published" / "bangumi-side-b"
    shutil.copytree(static_site / "pages", published_root)
    cover = published_root / "media" / "covers" / "browser-fixture.png"
    cover.parent.mkdir(parents=True)
    shutil.copyfile(published_root / "icons" / "icon-192.png", cover)
    subject = published_root / "subjects" / "101" / "index.html"
    subject.write_text(
        subject.read_text("utf-8").replace(
            "</main>",
            '<img data-browser-cover '
            'src="../../media/covers/browser-fixture.png" alt="">\n</main>',
        ),
        encoding="utf-8",
    )
    entries = index_candidate(published_root, "/bangumi-side-b/")
    manifest = build_snapshot_manifest(
        entries,
        release_version="2026.07.30.1",
        app_version="0.1.0",
        deployment_path="/bangumi-side-b/",
    )
    manifest_text = manifest_json(manifest)
    (published_root / "snapshot-manifest.json").write_bytes(manifest_text.encode())
    release = {
        "schema": 1,
        "release_version": "2026.07.30.1",
        "app_version": "0.1.0",
        "generated_at": "2026-07-30T00:00:00Z",
        "published_at": "2026-07-30T00:00:00Z",
        "quarter_count": 1,
        "subject_count": 1,
        "latest_quarter": "2026-04",
        "total_bytes": sum(entry.size_bytes for entry in entries),
        "content_hash": manifest.content_hash,
        "manifest_url": "snapshot-manifest.json",
        "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        "change_kind": "data",
        "summary": {"system": [], "data": []},
    }
    (published_root / "release.json").write_text(json.dumps(release), "utf-8")
    update_requests: list[str] = []

    class TrackingHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(published_root.parent), **kwargs)

        def log_message(self, *_: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
            update_requests.append(self.path.split("?", 1)[0])
            super().do_GET()

    handler = TrackingHandler
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(5000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/quarters/2026-04/index.html")
        page.locator("[data-pwa-gate]").wait_for(state="visible")
        page.wait_for_function(
            "window.BsbPwa.state().available_release?.release_version"
            " === '2026.07.30.1'"
        )
        assert page.locator("[data-pwa-gate-release]").inner_text() == "2026.07.30.1"
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        context.set_offline(True)
        page.goto(f"{root}/subjects/101/index.html?from=2026-04")
        assert page.get_by_role("heading", name="需要初始化本地资料库").count() == 1
        context.set_offline(False)
        page.goto(f"{root}/settings/index.html")
        page.evaluate(
            "window.__pwa_states = []; window.addEventListener('bsb-pwa-state', "
            "(event) => window.__pwa_states.push(event.detail));"
        )
        page.locator("[data-pwa-initialize]").dblclick()
        page.wait_for_function(
            "window.BsbPwa.state().status === 'ready'", timeout=15000
        )
        state = page.evaluate("window.BsbPwa.state()")
        assert state["status"] == "ready", state
        assert page.locator("[data-pwa-enter]").inner_text() == "进入 2026-04 资料库"
        assert (
            page.locator("[data-pwa-complete]").inner_text()
            == "1 部作品已完成校验，可离线浏览。"
        )
        generated_at = page.locator("[data-pwa-generated-at]")
        assert generated_at.get_attribute("data-iso") == "2026-07-30T00:00:00Z"
        assert "年" in generated_at.inner_text()
        page.wait_for_function(
            "document.activeElement === document.querySelector('[data-pwa-enter]')"
        )
        assert page.evaluate(
            "window.__pwa_states.some((state) => "
            "state.command_error === 'initialization-in-progress')"
        )
        page.goto(f"{root}/quarters/2026-04/index.html")
        page.locator("[data-pwa-gate]").wait_for(state="hidden", timeout=15000)
        context.set_offline(True)
        page.goto(f"{root}/subjects/101/index.html?from=2026-04")
        assert page.locator("[data-subject-detail]").count() == 1
        assert page.evaluate(
            """async () => {
              const href = document.querySelector('link[rel="stylesheet"]').href;
              try {
                const response = await fetch(`${href}?unexpected=1`);
                return response.status;
              } catch { return 0; }
            }"""
        ) == 503
        context.set_offline(False)
        subject.write_bytes(subject.read_bytes() + b"\n<!-- release two -->\n")
        next_entries = index_candidate(published_root, "/bangumi-side-b/")
        next_manifest = build_snapshot_manifest(
            next_entries,
            release_version="2026.07.30.2",
            app_version="0.1.0",
            deployment_path="/bangumi-side-b/",
        )
        next_manifest_text = manifest_json(next_manifest)
        (published_root / "snapshot-manifest.json").write_bytes(
            next_manifest_text.encode()
        )
        release.update(
            {
                "release_version": "2026.07.30.2",
                "content_hash": next_manifest.content_hash,
                "total_bytes": sum(entry.size_bytes for entry in next_entries),
                "manifest_sha256": hashlib.sha256(
                    next_manifest_text.encode()
                ).hexdigest(),
                "summary": {"system": [], "data": ["资料更新"]},
            }
        )
        (published_root / "release.json").write_text(json.dumps(release), "utf-8")
        page.goto(f"{root}/settings/index.html")
        page.wait_for_timeout(2000)
        settings_state = page.evaluate("window.BsbPwa.state()")
        assert settings_state["active"], settings_state
        page.locator("[data-pwa-check]").click()
        page.locator("[data-pwa-update-dialog]").wait_for(state="visible")
        update_requests.clear()
        page.evaluate(
            "window.__pwa_update_states = []; window.addEventListener('bsb-pwa-state', "
            "(event) => window.__pwa_update_states.push(event.detail));"
        )
        page.locator("[data-pwa-update-start]").click()
        page.wait_for_function(
            "window.BsbPwa.state().active?.release_version === '2026.07.30.2'",
            timeout=5000,
        )
        update_states = page.evaluate("window.__pwa_update_states")
        update = next(
            state["staging"]
            for state in reversed(update_states)
            if state.get("staging")
            and state["staging"].get("release_version") == "2026.07.30.2"
        )
        assert update["reused_files"] > 0
        assert update["downloaded_files"] == 1
        assert update["reused_bytes"] > update["downloaded_bytes"]
        assert "/bangumi-side-b/subjects/101/index.html" in update_requests
        assert "/bangumi-side-b/media/covers/browser-fixture.png" not in update_requests
        page.evaluate(
            """async () => {
              const cache = await caches.open(window.BsbPwa.state().active.cache_name);
              const manifest = document.querySelector('link[rel="manifest"]').href;
              const url = new URL('media/covers/browser-fixture.png', manifest);
              const request = new Request(url.href);
              await cache.put(request, new Response(new Uint8Array([0])));
            }"""
        )
        repaired_entries = index_candidate(published_root, "/bangumi-side-b/")
        repaired_manifest = build_snapshot_manifest(
            repaired_entries,
            release_version="2026.07.30.3",
            app_version="0.1.0",
            deployment_path="/bangumi-side-b/",
        )
        repaired_manifest_text = manifest_json(repaired_manifest)
        (published_root / "snapshot-manifest.json").write_bytes(
            repaired_manifest_text.encode()
        )
        release.update(
            {
                "release_version": "2026.07.30.3",
                "content_hash": repaired_manifest.content_hash,
                "total_bytes": sum(entry.size_bytes for entry in repaired_entries),
                "manifest_sha256": hashlib.sha256(
                    repaired_manifest_text.encode()
                ).hexdigest(),
            }
        )
        (published_root / "release.json").write_text(json.dumps(release), "utf-8")
        page.locator("[data-pwa-check]").click()
        page.locator("[data-pwa-update-dialog]").wait_for(state="visible")
        update_requests.clear()
        page.evaluate(
            "window.__pwa_repair_states = []; window.addEventListener('bsb-pwa-state', "
            "(event) => window.__pwa_repair_states.push(event.detail));"
        )
        page.locator("[data-pwa-update-start]").click()
        page.wait_for_function(
            "window.BsbPwa.state().active?.release_version === '2026.07.30.3'",
            timeout=5000,
        )
        repair_states = page.evaluate("window.__pwa_repair_states")
        repair = next(
            state["staging"]
            for state in reversed(repair_states)
            if state.get("staging")
            and state["staging"].get("release_version") == "2026.07.30.3"
        )
        assert repair["downloaded_files"] == 1
        assert "/bangumi-side-b/media/covers/browser-fixture.png" in update_requests
        clear_messages: list[str] = []
        page.once(
            "dialog",
            lambda dialog: (clear_messages.append(dialog.message), dialog.accept()),
        )
        page.locator("[data-pwa-clear]").click()
        page.wait_for_function("window.BsbPwa.state().active === null", timeout=5000)
        assert clear_messages == [
            "将删除已下载的季度页面、作品详情与封面。\n"
            "程序外壳仍会保留，之后需要重新初始化。\n确定清除吗？"
        ]
        assert not page.locator("[data-pwa-initialize]").is_hidden()
        context.set_offline(True)
        page.goto(f"{root}/subjects/101/index.html")
        assert page.get_by_role("heading", name="需要初始化本地资料库").count() == 1
        context.close()
    finally:
        server.shutdown()
        thread.join()


def test_pages_pwa_accepts_slow_download_then_pauses_and_resumes(
    static_site: Path, browser: Browser
) -> None:
    published_root = static_site.parent / "slow-published" / "bangumi-side-b"
    shutil.copytree(static_site / "pages", published_root)
    entries = index_candidate(published_root, "/bangumi-side-b/")
    manifest = build_snapshot_manifest(
        entries,
        release_version="2026.07.30.1",
        app_version="0.1.0",
        deployment_path="/bangumi-side-b/",
    )
    manifest_text = manifest_json(manifest)
    (published_root / "snapshot-manifest.json").write_bytes(manifest_text.encode())
    (published_root / "release.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "release_version": "2026.07.30.1",
                "app_version": "0.1.0",
                "generated_at": "2026-07-30T00:00:00Z",
                "published_at": "2026-07-30T00:00:00Z",
                "quarter_count": 1,
                "subject_count": 1,
                "total_bytes": sum(entry.size_bytes for entry in entries),
                "content_hash": manifest.content_hash,
                "manifest_url": "snapshot-manifest.json",
                "manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
                "change_kind": "data",
                "summary": {"system": [], "data": []},
            }
        ),
        "utf-8",
    )

    class DelayedHandler(http.server.SimpleHTTPRequestHandler):
        delay_seconds = 9.0

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
            if "/subjects/101/index.html" in self.path and type(self).delay_seconds:
                time.sleep(type(self).delay_seconds)
            super().do_GET()

    handler = functools.partial(DelayedHandler, directory=str(published_root.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(5000)
        root = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
        page.goto(f"{root}/quarters/2026-04/index.html")
        page.locator("[data-pwa-gate]").wait_for(state="visible")
        page.wait_for_function("navigator.serviceWorker.controller !== null")
        page.wait_for_function("window.BsbPwa.state().available_release !== null")
        started = time.monotonic()
        accepted = page.evaluate("window.BsbPwa.initialize()")
        assert time.monotonic() - started < 2
        assert accepted["staging"]["operation_id"]
        page.wait_for_timeout(1000)
        state = page.evaluate("window.BsbPwa.state()")
        assert state["status"] == "downloading", state["staging"]["last_error"]
        page.wait_for_function(
            "window.BsbPwa.state().staging.downloaded_bytes > 0", timeout=5000
        )
        paused = page.evaluate("window.BsbPwa.pause()")
        assert paused["status"] == "paused"
        page.wait_for_timeout(250)
        assert page.evaluate("window.BsbPwa.state().status") == "paused"
        page.close()
        DelayedHandler.delay_seconds = 0
        resumed_page = context.new_page()
        resumed_page.goto(f"{root}/settings/index.html")
        resumed_page.wait_for_function("window.BsbPwa !== undefined")
        assert resumed_page.evaluate("window.BsbPwa.state().status") == "paused"
        resumed_page.evaluate("window.BsbPwa.resume()")
        resumed_page.wait_for_function(
            "window.BsbPwa.state().status === 'ready'", timeout=15000
        )
        assert resumed_page.evaluate("window.BsbPwa.state().active")
        context.close()
    finally:
        server.shutdown()
        thread.join()
