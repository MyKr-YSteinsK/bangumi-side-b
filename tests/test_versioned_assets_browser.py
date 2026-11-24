"""Exercise v2's actual generated A/B/C assets through a loopback worker."""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

from bgm_side_b.build.cover_preview import cover_preview_path
from bgm_side_b.build.serve import create_preview_server
from bgm_side_b.build.site_paths import (
    OFFLINE_SCHEMA,
    app_asset_identity,
    quarter_paths,
)
from bgm_side_b.domain import Quarter
from bgm_side_b.repository import SubjectRepository
from tests.test_pwa_browser import (
    _wait_for_element_text,
    _wait_for_pwa_startup_queue_quiescent,
)
from tests.test_site_builder import ROOT, _build_fixture, _set_preview_fixture_cover

pytestmark = [pytest.mark.browser, pytest.mark.deep_regression]


def _wait_complete(page: Page, label: str, revision: str) -> None:
    page.evaluate(
        """async ([quarter, revision]) => {
          const deadline = Date.now() + 45000;
          while (Date.now() < deadline) {
            const state = await BsbPwa.getQuarterState(quarter);
            const queue = await BsbPwa.currentQueue();
            if (state.status === "COMPLETE" && state.active?.revision === revision
                && queue.state === "idle" && queue.succeeded.includes(quarter)) return;
            await new Promise(resolve => setTimeout(resolve, 50));
          }
          throw new Error("verified download did not settle");
        }""",
        [label, revision],
    )


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_generated_asset_generations_preserve_verified_packages(
    tmp_path: Path, engine: str
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.root = tmp_path / "frontend"
    shutil.copytree(ROOT / "static", builder.root / "static")
    css = builder.root / "static/css/site.css"
    app = builder.root / "static/js/app.js"
    css.write_bytes(css.read_bytes() + b"\n/* generation A */\n")
    app.write_bytes(app.read_bytes() + b"\n/* generation A */\n")
    builder.build()
    site = builder.site_directory
    repository = SubjectRepository(database)
    april, july = "2026-04", "2026-07"

    def manifest(label: str) -> dict:
        value = json.loads((site / quarter_paths(label)[2]).read_bytes())
        assert value["schema"] == OFFLINE_SCHEMA
        return value

    def app_path(package: dict, name: str) -> dict:
        return next(
            entry
            for entry in package["resources"]
            if (identity := app_asset_identity(entry["url"]))
            and identity[0] == name
        )

    def update(score: float, before: bytes, after: bytes) -> dict:
        for source in (css, app):
            source.write_bytes(source.read_bytes().replace(before, after))
        movie = repository.get_subject_facts(202)
        assert movie is not None
        with repository.transaction() as connection:
            repository.replace_subject_snapshot(
                connection,
                replace(movie, subject=replace(movie.subject, rating_score=score)),
            )
        builder.build()
        return manifest(july)

    first = manifest(july)
    retained = manifest(april)
    server = create_preview_server(site, port=0)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    url = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
    try:
        with (
            sync_playwright() as playwright,
            getattr(playwright, engine).launch() as browser,
        ):
            context = browser.new_context()
            settings = context.new_page()
            settings.goto(f"{url}/settings/index.html")
            settings.wait_for_function(
                "navigator.serviceWorker.controller !== null"
            )
            _wait_for_pwa_startup_queue_quiescent(settings)
            settings.wait_for_function(
                "sessionStorage.getItem('bsb-offline-auto-maintenance-v2')"
                " === 'complete'"
            )
            settings.evaluate("() => BsbPwa.enqueue(['2026-07'])")
            _wait_complete(settings, july, first["revision"])

            # Legacy browser state and cache content stay owned by the legacy protocol.
            settings.evaluate(
                """async () => {
                  const cache = await caches.open('bsb-meta-v1');
                  const key = new URL(
                    '../__bsb_meta__/quarters/2026-04.json', location.href);
                  const legacy = { quarter: '2026-04', status: 'COMPLETE' };
                  await cache.put(key, new Response(JSON.stringify(legacy)));
                  await (await caches.open('bsb-content-v1')).put(
                    new URL('../__bsb_content__/legacy', location.href),
                    new Response('old'));
                }"""
            )
            assert (
                settings.evaluate(
                    "async () => (await BsbPwa.getQuarterState('2026-04')).status"
                )
                == "NONE"
            )
            assert (
                settings.evaluate(
                    "async () => (await fetch('../data/offline/2026-04.json')).status"
                )
                == 404
            )

            state = repository.get_sync_state(Quarter(2026, 4))
            assert state is not None
            with repository.transaction() as connection:
                repository.write_sync_state(
                    connection, replace(state, facts_status="incomplete")
                )
            _set_preview_fixture_cover(tmp_path, database, 202, large=True)
            second = update(8.5, b"generation A", b"generation B")
            assert manifest(april) == retained
            quarter_json = json.loads((site / quarter_paths(july)[1]).read_bytes())
            movie = quarter_json["movie"]["premiere"][0]
            preview = cover_preview_path(movie["cover_url"])
            assert preview is not None
            target = site / preview
            correct = target.read_bytes()
            target.write_bytes(b"X" * len(correct))
            settings.evaluate("() => BsbPwa.enqueue(['2026-04'])")
            _wait_complete(settings, april, retained["revision"])

            settings.evaluate("() => BsbPwa.enqueue(['2026-07'])")
            evidence = settings.evaluate(
                """async () => {
                  const deadline = Date.now() + 45000;
                  while (Date.now() < deadline) {
                    const queue = await BsbPwa.currentQueue();
                    const state = await BsbPwa.getQuarterState('2026-07');
                    if (queue.state === 'idle' && queue.failed.includes('2026-07')
                        && state.active && state.staging && state.error) {
                      return {queue, state};
                    }
                    await new Promise(resolve => setTimeout(resolve, 50));
                  }
                  const state = await BsbPwa.getQuarterState('2026-07');
                  const queue = await BsbPwa.currentQueue();
                  throw new Error(JSON.stringify({
                    status: state.status, active: state.active?.revision,
                    staging: state.staging?.revision, error: state.error, queue,
                  }));
                }"""
            )
            assert evidence["queue"]["errors"][-1]["stage"] == "resource"
            saved = evidence["state"]
            assert saved["status"] == "UPDATE_INCOMPLETE", saved
            assert saved["active"]["revision"] == first["revision"]
            assert saved["staging"]["revision"] == second["revision"]

            offline = context.new_page()
            offline.goto(f"{url}/settings/index.html")
            offline.wait_for_function("navigator.serviceWorker.controller !== null")
            if engine == "chromium":
                context.set_offline(True)
                offline.goto(f"{url}/{july}/index.html")
                offline.locator('[data-subject-id="101"]').wait_for()
                assert first["data_revision"] in offline.locator(
                    "main"
                ).get_attribute("data-data-url")
                assert app_path(first, "assets/app.css")["url"] in (
                    offline.locator('link[rel="stylesheet"]').get_attribute("href")
                )
            else:
                offline.goto(f"{url}/archive/index.html")
                context.set_offline(True)
                offline.locator('[data-scope-choice="local"]').click()
                _wait_for_element_text(
                    offline, "[data-local-status]", "本机覆盖 2 个季度"
                )
                local = offline.evaluate(
                    "() => BsbPwa.readActiveQuarterData('2026-07')"
                )
                assert local["revision"] == first["revision"]
                assert local["data_revision"] == first["data_revision"]
            target.write_bytes(correct)
            context.set_offline(False)
            settings.evaluate("() => BsbPwa.enqueue(['2026-07'])")
            _wait_complete(settings, july, second["revision"])

            third = update(9.5, b"generation B", b"generation C")
            assert manifest(april) == retained
            assert (
                len(
                    {
                        app_path(package, "assets/app.js")["url"]
                        for package in (first, second, third)
                    }
                )
                == 3
            )
            online = offline
            online.goto(f"{url}/{july}/index.html")
            online.locator("[data-quarter-refresh]").click(timeout=30_000)
            online.wait_for_function(
                "revision => document.querySelector('main')?.dataset.dataUrl"
                ".includes(revision)",
                arg=third["data_revision"],
            )
            assert app_path(third, "assets/app.css")["url"] in (
                online.locator('link[rel="stylesheet"]').get_attribute("href")
            )
            assert (
                settings.evaluate(
                    "async () => (await BsbPwa.getQuarterState('2026-07'))"
                    ".active.revision"
                )
                == second["revision"]
            )

            settings.evaluate("() => BsbPwa.enqueue(['2026-07'])")
            _wait_complete(settings, july, third["revision"])
            shell = json.loads((site / "data/pwa-shell-v2.json").read_bytes())
            worker = site / "sw-v2.js"
            stamp = worker.stat()
            os.utime(worker, (stamp.st_atime, stamp.st_mtime + 2))
            settings.evaluate(
                "async () => (await navigator.serviceWorker.ready).update()"
            )
            settings.evaluate(
                """async revision => {
                  const registration = await navigator.serviceWorker.ready;
                  const cache = await caches.open(BsbPwa.META_CACHE);
                  const key = new Request(new URL(
                    '../__bsb_meta__/shell-pending-' + revision + '.json',
                    location.href));
                  const deadline = Date.now() + 30000;
                  while (Date.now() < deadline) {
                    if (registration.waiting && await cache.match(key)) return;
                    await new Promise(resolve => setTimeout(resolve, 50));
                  }
                  throw new Error("latest shell did not enter waiting");
                }""",
                shell["revision"],
            )
            with settings.expect_event("domcontentloaded"):
                settings.locator("[data-pwa-refresh]").click()
            settings.wait_for_function("window.BsbPwa?.capabilityState() === 'ready'")
            assert settings.evaluate(
                """async revision => {
                  const cache = await caches.open(BsbPwa.META_CACHE);
                  const key = new Request(new URL(
                    '../__bsb_meta__/shell.json', location.href));
                  const stored = await cache.match(key);
                  return (await stored?.json())?.revision === revision;
                }""",
                shell["revision"],
            )
            settings.evaluate("() => BsbPwa.garbageCollect()")
            hashes = [
                app_path(pkg, "assets/app.js")["content_hash"]
                for pkg in (first, second, third)
            ]
            cache_state = settings.evaluate(
                """async (hashes) => {
                  const cache = await caches.open(BsbPwa.CONTENT_CACHE);
                  const keys = (await cache.keys()).map(
                    key => key.url.split('/').at(-1));
                  const state = await BsbPwa.getQuarterState('2026-07');
                  return {
                    cached: hashes.map(hash => keys.includes(hash)), keys,
                    active: state.active?.revision, status: state.status,
                  };
                }""",
                hashes,
            )
            cached = cache_state["cached"]
            assert cached[0] and cached[2], cache_state
            if settings.evaluate("typeof navigator.locks?.request === 'function'"):
                assert not cached[1]
            assert {"bsb-meta-v1", "bsb-content-v1"}.issubset(
                settings.evaluate("() => caches.keys()")
            )
            context.close()
    finally:
        server.shutdown()
        runner.join()
        server.server_close()


def test_legacy_worker_requires_explicit_switch_before_v2_download(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    site = builder.site_directory
    (site / "legacy.html").write_text("<p>legacy fixture</p>", encoding="utf-8")
    (site / "sw.js").write_text(
        "self.addEventListener('activate', event => event.waitUntil(clients.claim()));",
        encoding="utf-8",
    )
    server = create_preview_server(site, port=0)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    url = f"http://127.0.0.1:{server.server_port}/bangumi-side-b"
    try:
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.goto(f"{url}/legacy.html")
            page.evaluate("() => navigator.serviceWorker.register('./sw.js')")
            controller = "navigator.serviceWorker.controller?.scriptURL"
            page.wait_for_function(f"{controller}.endsWith('/sw.js')")
            page.goto(f"{url}/settings/index.html")
            page.wait_for_function(
                "() => BsbPwa.capabilityState() === 'upgrade-required' "
                "&& BsbPwa.updateAvailable()"
            )
            await_marker = f"{controller}.endsWith('/sw-v2.js')"
            assert not page.evaluate(await_marker)
            assert page.evaluate(
                "async () => (await BsbPwa.getQuarterState('2026-07')).status"
            ) == "NONE"
            page.locator("[data-pwa-refresh]").click()
            page.wait_for_function(await_marker)
            _wait_for_pwa_startup_queue_quiescent(page)
            page.evaluate("() => BsbPwa.enqueue(['2026-07'])")
            package = json.loads((site / quarter_paths("2026-07")[2]).read_bytes())
            _wait_complete(page, "2026-07", package["revision"])
    finally:
        server.shutdown()
        runner.join()
        server.server_close()
