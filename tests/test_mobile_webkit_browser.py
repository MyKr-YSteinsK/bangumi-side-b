from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.test_site_builder import _build_fixture

pytestmark = pytest.mark.browser

_NEXT_SAFE_PORT = 18480


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


@pytest.fixture(scope="session")
def mobile_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mobile-motion-site")
    builder, _ = _build_fixture(
        root,
        include_same_quarter_tv=True,
        extra_same_quarter_tv=24,
    )
    builder.build()
    return root / "dist" / "site"


@pytest.fixture(scope="session")
def site_server(mobile_site: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(mobile_site),
    )
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture(scope="session", params=("chromium", "webkit"))
def motion_browser(request: pytest.FixtureRequest) -> Iterator[Browser]:
    with sync_playwright() as runner:
        browser = getattr(runner, request.param).launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def motion_page(motion_browser: Browser) -> Iterator[Page]:
    context = motion_browser.new_context(
        service_workers="block",
        viewport={"width": 393, "height": 852},
    )
    page = context.new_page()
    page.emulate_media(reduced_motion="no-preference")
    page.set_default_timeout(8000)
    try:
        yield page
    finally:
        context.close()


def _open_quarter(page: Page, site_server: str) -> None:
    page.goto(f"{site_server}/2026-07/index.html")
    page.wait_for_selector(".subject-row")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )


def _assert_no_root_motion_or_black_overlay(page: Page) -> None:
    state = page.evaluate(
        """() => {
          const nearBlack = (style) => {
            const match = style.backgroundColor.match(/\\d+/g)?.map(Number) || [];
            return match.length >= 3 && Math.max(...match.slice(0, 3)) < 16;
          };
          return {
            htmlAnimations: document.documentElement.getAnimations().length,
            bodyAnimations: document.body.getAnimations().length,
            rootAnimations: [...document.querySelectorAll('[data-page]')]
              .reduce((count, node) => count + node.getAnimations().length, 0),
            blackOverlay: [...document.body.querySelectorAll('*')].some((node) => {
              const style = getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return style.position === 'fixed'
                && rect.width >= innerWidth * .95
                && rect.height >= innerHeight * .95
                && nearBlack(style);
            }),
          };
        }"""
    )
    assert state == {
        "htmlAnimations": 0,
        "bodyAnimations": 0,
        "rootAnimations": 0,
        "blackOverlay": False,
    }


def test_mobile_menu_is_anchored_without_fullscreen_overlay(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    toggle = motion_page.locator("[data-mobile-menu-toggle]")
    assert toggle.is_visible()
    toggle.click()
    menu = motion_page.locator("[data-mobile-menu]")
    assert menu.is_visible()
    state = menu.evaluate(
        """menu => {
          const rect = menu.getBoundingClientRect();
          const trigger = document.querySelector('[data-mobile-menu-toggle]');
          const triggerRect = trigger.getBoundingClientRect();
          const hit = document.elementFromPoint(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2,
          );
          return {
            gap: rect.top - triggerRect.bottom,
            rightDelta: Math.abs(rect.right - triggerRect.right),
            withinViewport: rect.left >= 0 && rect.right <= innerWidth
              && rect.top >= 0 && rect.bottom <= innerHeight,
            hitMenu: hit === menu || menu.contains(hit),
          };
        }"""
    )
    assert 4 <= state["gap"] <= 14, state
    assert state["rightDelta"] <= 2, state
    assert state["withinViewport"] is True
    assert state["hitMenu"] is True
    _assert_no_root_motion_or_black_overlay(motion_page)


def test_grid_list_motion_has_shared_geometry_in_chromium_and_webkit(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.locator('[data-view-mode="list"]').click()
    state = motion_page.evaluate(
        """() => {
          const root = document.querySelector(
            '[data-page="quarter"][data-archive-app]'
          );
          const list = root.querySelector('[data-list-section="tv"] .result-list');
          const row = list.querySelector('.subject-row');
          const cover = row.querySelector('.subject-row__cover');
          return {
            mode: root.getAttribute('data-view-mode'),
            columns: getComputedStyle(list).gridTemplateColumns.split(' ').length,
            rowAnimations: row.getAnimations().length,
            coverAnimations: cover.getAnimations().length,
            contentAnimations: row.querySelector('.subject-row__content')
              .getAnimations().length,
            scoreAnimations: row.querySelector('.subject-row__score')
              .getAnimations().length,
          };
        }"""
    )
    assert state["mode"] == "list", state
    assert state["columns"] == 1
    assert state["rowAnimations"] > 0, state
    assert state["coverAnimations"] > 0, state
    assert state["contentAnimations"] > 0, state
    assert state["scoreAnimations"] > 0, state
    _assert_no_root_motion_or_black_overlay(motion_page)

    motion_page.wait_for_timeout(320)
    assert motion_page.locator('[data-view-mode="grid"]').is_visible()
    motion_page.locator('[data-view-mode="grid"]').click()
    grid_state = motion_page.evaluate(
        """() => {
          const root = document.querySelector(
            '[data-page="quarter"][data-archive-app]'
          );
          const list = root.querySelector('[data-list-section="tv"] .result-list');
          const row = list.querySelector('.subject-row');
          return {
            mode: root.getAttribute('data-view-mode'),
            columns: getComputedStyle(list).gridTemplateColumns.split(' ').length,
            animations: row.getAnimations().length
              + row.querySelector('.subject-row__cover').getAnimations().length,
          };
        }"""
    )
    assert grid_state["mode"] == "grid", grid_state
    assert grid_state["columns"] == 2
    assert grid_state["animations"] > 0, grid_state
    _assert_no_root_motion_or_black_overlay(motion_page)


def test_media_collection_motion_stays_inside_results(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.locator('[data-media-mode="movie"]').click()
    state = motion_page.evaluate(
        """() => ({
          regionAnimations: document.querySelector('[data-list-sections]')
            .getAnimations().length,
          rowAnimations: document.querySelector(
            '[data-list-sections] .subject-row:not([hidden])'
          )
            .getAnimations().length,
          headerAnimations: document.querySelector('header.site-header')
            .getAnimations().length,
          controlAnimations: document.querySelector('.browser-controls')
            .getAnimations().length,
        })"""
    )
    assert state["regionAnimations"] > 0, state
    assert state["rowAnimations"] > 0, state
    assert state["headerAnimations"] == 0, state
    assert state["controlAnimations"] == 0, state
    _assert_no_root_motion_or_black_overlay(motion_page)


def test_quarter_departure_and_arrival_use_result_only_motion(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    departure = motion_page.evaluate(
        """() => {
          const link = document.querySelector('[data-quarter-prev]');
          const originalSetTimeout = window.setTimeout;
          window.setTimeout = () => 0;
          const event = new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            button: 0,
          });
          link.dispatchEvent(event);
          window.setTimeout = originalSetTimeout;
          return {
            defaultPrevented: event.defaultPrevented,
            token: JSON.parse(sessionStorage.getItem('bsb-quarter-motion')),
            rowAnimations: document.querySelector('[data-list-sections] .subject-row')
              .getAnimations().length,
          };
        }"""
    )
    assert departure["defaultPrevented"] is True
    assert departure["token"]["from"] == "2026-07"
    assert departure["token"]["to"] == "2026-04"
    assert departure["token"]["direction"] == "prev"
    assert departure["rowAnimations"] > 0, departure
    _assert_no_root_motion_or_black_overlay(motion_page)

    arrival = motion_page.evaluate(
        """() => {
          const root = document.querySelector(
            '[data-page="quarter"][data-archive-app]'
          );
          sessionStorage.setItem('bsb-quarter-motion', JSON.stringify({
            from: '2026-04',
            to: root.dataset.quarter,
            direction: 'next',
            timestamp: Date.now(),
          }));
          const played = window.BsbArchive.playQuarterArrival(root);
          return {
            played,
            token: sessionStorage.getItem('bsb-quarter-motion'),
            rowAnimations: root.querySelector('[data-list-sections] .subject-row')
              .getAnimations().length,
          };
        }"""
    )
    assert arrival["played"] is True
    assert arrival["token"] is None
    assert arrival["rowAnimations"] > 0, arrival
    _assert_no_root_motion_or_black_overlay(motion_page)


def test_reduced_motion_remains_a_direct_path(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.emulate_media(reduced_motion="reduce")
    motion_page.locator('[data-view-mode="list"]').click()
    motion_page.locator('[data-media-mode="movie"]').click()
    state = motion_page.evaluate(
        """() => ({
          rowAnimations: [...document.querySelectorAll('.subject-row')]
            .reduce((count, node) => count + node.getAnimations().length, 0),
          resultRegionAnimations: document.querySelector('[data-list-sections]')
            .getAnimations().length,
        })"""
    )
    assert state == {"rowAnimations": 0, "resultRegionAnimations": 0}
