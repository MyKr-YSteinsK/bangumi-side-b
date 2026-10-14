from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.test_site_builder import _build_fixture

pytestmark = [pytest.mark.browser, pytest.mark.deep_regression]

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


@pytest.fixture(scope="module", params=("chromium", "webkit"))
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
    page.set_default_timeout(20000)
    page.set_default_navigation_timeout(20000)
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
            width: rect.width,
            gap: rect.top - triggerRect.bottom,
            rightDelta: Math.abs(rect.right - triggerRect.right),
            withinViewport: rect.left >= 0 && rect.right <= innerWidth
              && rect.top >= 0 && rect.bottom <= innerHeight,
            hitMenu: hit === menu || menu.contains(hit),
          };
        }"""
    )
    assert 4 <= state["gap"] <= 14, state
    assert state["width"] <= 225, state
    assert state["rightDelta"] <= 2, state
    assert state["withinViewport"] is True
    assert state["hitMenu"] is True
    assert menu.get_by_role("link", name="档案", exact=True).is_visible()
    assert menu.get_by_role("link", name="Archive", exact=True).count() == 0
    _assert_no_root_motion_or_black_overlay(motion_page)


def test_grid_list_motion_has_shared_geometry_in_chromium_and_webkit(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.evaluate("""() => {
      const probe = window.__viewMotionStarts = {
        row: 0, cover: 0, content: 0, score: 0,
      };
      const animate = Element.prototype.animate;
      Element.prototype.animate = function(...args) {
        if (this.matches('.subject-row')) probe.row += 1;
        if (this.classList.contains('subject-row__cover')) probe.cover += 1;
        if (this.classList.contains('subject-row__content')) probe.content += 1;
        if (this.classList.contains('subject-row__score')) probe.score += 1;
        return animate.apply(this, args);
      };
    }""")
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
            motionStarts: window.__viewMotionStarts,
          };
        }"""
    )
    assert state["mode"] == "list", state
    assert state["columns"] == 1
    assert state["rowAnimations"] > 0 or state["motionStarts"]["row"] > 0, state
    assert state["coverAnimations"] > 0 or state["motionStarts"]["cover"] > 0, state
    assert state["contentAnimations"] > 0 or state["motionStarts"]["content"] > 0, state
    assert state["scoreAnimations"] > 0 or state["motionStarts"]["score"] > 0, state
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


@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
@pytest.mark.parametrize("page_kind", ["quarter", "archive"])
def test_search_updates_in_place_without_result_motion(
    motion_page: Page,
    site_server: str,
    reduced_motion: str,
    page_kind: str,
) -> None:
    motion_page.set_viewport_size({"width": 390, "height": 844})
    if page_kind == "quarter":
        _open_quarter(motion_page, site_server)
        subject_id = "101"
    else:
        motion_page.set_default_timeout(20000)
        motion_page.set_default_navigation_timeout(20000)
        motion_page.goto(f"{site_server}/archive/index.html?year=2026")
        motion_page.wait_for_function(
            "document.querySelector('[data-results-summary]')"
            "?.textContent.includes('appearance')"
        )
        subject_id = "303"
    motion_page.emulate_media(reduced_motion=reduced_motion)
    row = motion_page.locator(f'[data-subject-id="{subject_id}"]').first
    row.evaluate("node => { node.dataset.searchIdentity = 'stable'; }")
    motion_page.evaluate("""() => {
      const probe = window.__searchMotionProbe = {
        animationScans: 0, animationsStarted: 0,
      };
      const getAnimations = Element.prototype.getAnimations;
      Element.prototype.getAnimations = function(...args) {
        if (this.closest?.('[data-record-key]')) probe.animationScans += 1;
        return getAnimations.apply(this, args);
      };
      const animate = Element.prototype.animate;
      Element.prototype.animate = function(...args) {
        if (this.closest?.('[data-record-key]')) probe.animationsStarted += 1;
        return animate.apply(this, args);
      };
    }""")

    search = motion_page.locator("[data-search]")
    search.fill(subject_id)
    assert motion_page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    ) == [subject_id]
    assert motion_page.locator(
        f'[data-subject-id="{subject_id}"]'
    ).first.get_attribute("data-search-identity") == "stable"
    assert motion_page.evaluate(
        "document.activeElement === document.querySelector('[data-search]')"
    )
    assert motion_page.evaluate("window.__searchMotionProbe") == {
        "animationScans": 0,
        "animationsStarted": 0,
    }


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


@pytest.mark.parametrize("viewport", [(393, 852), (1280, 900)])
@pytest.mark.parametrize("reduced_motion", ["no-preference", "reduce"])
def test_quarter_navigation_keeps_native_default_and_arrival_feedback(
    motion_page: Page,
    site_server: str,
    viewport: tuple[int, int],
    reduced_motion: str,
) -> None:
    motion_page.add_init_script("""(() => {
      const key = '__plan50-nav-observation';
      const originalSetTimeout = window.setTimeout.bind(window);
      const originalGetItem = Storage.prototype.getItem;
      window.__plan50NavCurrent = null;
      window.__plan50ArrivalAnimations = 0;
      window.__plan50MotionTokenReads = [];
      Storage.prototype.getItem = function(storageKey) {
        const value = originalGetItem.call(this, storageKey);
        if (storageKey === 'bsb-quarter-motion') {
          window.__plan50MotionTokenReads.push(value);
        }
        return value;
      };
      window.setTimeout = (callback, delay, ...args) => {
        if (window.__plan50NavCurrent && Number(delay) >= 80) {
          window.__plan50NavCurrent.longTimers.push(Number(delay));
        }
        return originalSetTimeout(callback, delay, ...args);
      };
      const animate = Element.prototype.animate;
      Element.prototype.animate = function(...args) {
        if (this.closest?.('[data-page="quarter"] [data-record-key]')) {
          window.__plan50ArrivalAnimations += 1;
        }
        return animate.apply(this, args);
      };
      document.addEventListener('click', event => {
        const link = event.target.closest?.(
          'a[data-quarter-prev], a[data-quarter-next], a[data-quarter-option]'
        );
        if (!link || event.button !== 0 || event.metaKey || event.ctrlKey
          || event.shiftKey || event.altKey) return;
        const observation = {
          href: link.href,
          clickEpochMs: Date.now(),
          defaultPrevented: null,
          longTimers: [],
              button: event.button,
              detail: event.detail,
              isTrusted: event.isTrusted,
        };
        window.__plan50NavCurrent = observation;
        sessionStorage.setItem(key, JSON.stringify(observation));
        queueMicrotask(() => {
          observation.defaultPrevented = event.defaultPrevented;
          sessionStorage.setItem(key, JSON.stringify(observation));
          window.__plan50NavCurrent = null;
        });
      }, true);
    })();""")
    motion_page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    motion_page.emulate_media(reduced_motion=reduced_motion)
    _open_quarter(motion_page, site_server)

    def activate(
        selector: str,
        expected_path: str,
        *,
        via_enter: bool = False,
    ) -> dict[str, object]:
        link = motion_page.locator(selector)
        if via_enter:
            link.focus()
            motion_page.keyboard.press("Enter")
        else:
            link.click()
        motion_page.wait_for_url(f"**{expected_path}")
        motion_page.wait_for_load_state("load")
        motion_page.wait_for_function(
            "document.querySelector('[data-results-summary]')"
            "?.textContent.trim().length > 0"
        )
        state = motion_page.evaluate("""() => {
          const observation = JSON.parse(sessionStorage.getItem(
            '__plan50-nav-observation'
          ) || 'null');
          const navigation = performance.getEntriesByType('navigation')[0];
          return {
            observation,
            clickToRequestStartMs: observation && navigation
              ? performance.timeOrigin + navigation.requestStart
                - observation.clickEpochMs
              : null,
            arrivalAnimations: window.__plan50ArrivalAnimations,
            appToken: sessionStorage.getItem('bsb-quarter-motion'),
                tokenReads: window.__plan50MotionTokenReads,
                reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
                geometryCandidates: window.BsbArchive.captureResultGeometry(
                  document.querySelector('[data-page="quarter"][data-archive-app]')
                ).size,
          };
        }""")
        assert state["observation"]["href"].endswith(expected_path), state
        assert state["observation"]["defaultPrevented"] is False, state
        assert state["observation"]["longTimers"] == [], state
        assert state["clickToRequestStartMs"] is not None, state
        assert state["appToken"] is None, state
        assert state["geometryCandidates"] > 0, state
        if reduced_motion == "reduce":
            assert state["arrivalAnimations"] == 0, state
        else:
            assert state["arrivalAnimations"] > 0, (
                f"observation={state['observation']!r}, "
                f"tokenReads={state['tokenReads']!r}, "
                f"geometryCandidates={state['geometryCandidates']}, "
                f"prefersReducedMotion={state['reducedMotion']}"
            )
        _assert_no_root_motion_or_black_overlay(motion_page)
        return state

    activate("[data-quarter-prev]", "/2026-04/index.html")
    activate("[data-quarter-next]", "/2026-07/index.html", via_enter=True)
    motion_page.locator("[data-quarter-selector]").click()
    activate('[data-quarter-option="2026-04"]', "/2026-04/index.html")
    motion_page.go_back()
    motion_page.wait_for_url("**/2026-07/index.html")
    assert motion_page.locator('[data-page="quarter"][data-archive-app]').get_attribute(
        "data-quarter"
    ) == "2026-07"


def test_rapid_quarter_activation_keeps_one_native_navigation(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.goto(f"{site_server}/2026-04/index.html")
    motion_page.wait_for_selector(".subject-row")
    target_url = f"{site_server}/2026-07/index.html"
    history_length = motion_page.evaluate("history.length")
    motion_page.evaluate(
        "sessionStorage.setItem('__plan50-history-before', String(history.length))"
    )
    target_requests: list[str] = []
    motion_page.on(
        "request",
        lambda request: target_requests.append(request.url)
        if request.url == target_url
        else None,
    )

    motion_page.evaluate("""() => {
      const link = document.querySelector('[data-quarter-next]');
      link.click();
      link.click();
    }""")
    motion_page.wait_for_function(
        "location.pathname === '/2026-07/index.html'",
        timeout=20000,
    )
    motion_page.wait_for_selector('[data-page="quarter"][data-archive-app]')
    motion_page.wait_for_load_state("networkidle")

    assert motion_page.url == target_url
    assert target_requests, target_requests
    assert motion_page.evaluate(
        "history.length - Number(sessionStorage.getItem('__plan50-history-before'))"
    ) == 1
    assert history_length == motion_page.evaluate(
        "Number(sessionStorage.getItem('__plan50-history-before'))"
    )
    motion_page.go_back()
    motion_page.wait_for_url("**/2026-04/index.html")
    assert motion_page.locator(
        '[data-page="quarter"][data-archive-app]'
    ).get_attribute("data-quarter") == "2026-04"


def test_quarter_navigation_ignores_modified_and_nonlocal_clicks(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.emulate_media(reduced_motion="no-preference")
    results = motion_page.evaluate("""() => {
      const link = document.querySelector('[data-quarter-prev]');
      const originalHref = link.href;
      const originalTarget = link.getAttribute('target');
      const originalDownload = link.getAttribute('download');
      const run = overrides => {
        sessionStorage.removeItem('bsb-quarter-motion');
        const event = {
          target: { closest: selector => link.matches(selector) ? link : null },
          button: 0,
          defaultPrevented: false,
          metaKey: false,
          ctrlKey: false,
          shiftKey: false,
          altKey: false,
          preventDefault() { this.defaultPrevented = true; },
          ...overrides,
        };
        window.BsbArchive.prepareQuarterDeparture(event);
        return {
          defaultPrevented: event.defaultPrevented,
          token: sessionStorage.getItem('bsb-quarter-motion'),
        };
      };
      const cases = {};
      cases.primary = run({});
      cases.keyboard = run({ button: -1, detail: 0 });
      cases.alreadyPrevented = run({ defaultPrevented: true });
      cases.middle = run({ button: 1 });
      for (const modifier of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey']) {
        cases[modifier] = run({ [modifier]: true });
      }
      link.target = '_blank';
      cases.blankTarget = run({});
      link.target = 'named-context';
      cases.namedTarget = run({});
      link.removeAttribute('target');
      const base = document.createElement('base');
      base.target = '_blank';
      document.head.append(base);
      cases.baseTarget = run({});
      base.remove();
      link.setAttribute('download', 'quarter.html');
      cases.download = run({});
      link.removeAttribute('download');
      link.href = 'https://example.invalid/2026-04/index.html';
      cases.external = run({});
      link.href = location.href;
      cases.sameQuarter = run({});
      link.href = originalHref;
      const originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key === 'bsb-quarter-motion') throw new Error('storage unavailable');
        return originalSetItem.call(this, key, value);
      };
      cases.storageUnavailable = run({});
      Storage.prototype.setItem = originalSetItem;
      if (originalTarget === null) link.removeAttribute('target');
      else link.setAttribute('target', originalTarget);
      if (originalDownload === null) link.removeAttribute('download');
      else link.setAttribute('download', originalDownload);
      link.href = originalHref;
      return cases;
    }""")
    assert results["primary"]["defaultPrevented"] is False
    assert results["primary"]["token"] is not None
    assert results["keyboard"]["defaultPrevented"] is False
    assert results["keyboard"]["token"] is not None
    for name in (
        "alreadyPrevented", "middle", "metaKey", "ctrlKey", "shiftKey",
        "altKey", "blankTarget", "namedTarget", "baseTarget", "download", "external",
        "sameQuarter", "storageUnavailable",
    ):
        assert results[name]["defaultPrevented"] is (name == "alreadyPrevented"), name
        assert results[name]["token"] is None, name


def test_quarter_arrival_rejects_expired_and_wrong_target_tokens(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.emulate_media(reduced_motion="no-preference")
    result = motion_page.evaluate("""() => {
      const root = document.querySelector('[data-page="quarter"][data-archive-app]');
      const originalAnimate = Element.prototype.animate;
      let animationsStarted = 0;
      Element.prototype.animate = function(...args) {
        animationsStarted += 1;
        return originalAnimate.apply(this, args);
      };
      const apply = (to, timestamp) => {
        sessionStorage.setItem('bsb-quarter-motion', JSON.stringify({
          from: '2026-04', to, direction: 'next', timestamp,
        }));
        return {
          played: window.BsbArchive.playQuarterArrival(root),
          token: sessionStorage.getItem('bsb-quarter-motion'),
        };
      };
      const expired = apply(root.dataset.quarter, Date.now() - 10001);
      const wrongTarget = apply('2026-04', Date.now());
      Element.prototype.animate = originalAnimate;
      return { expired, wrongTarget, animationsStarted };
    }""")
    assert result == {
        "expired": {"played": False, "token": None},
        "wrongTarget": {"played": False, "token": None},
        "animationsStarted": 0,
    }


def test_quarter_blank_target_keeps_native_new_tab_navigation(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    link = motion_page.locator("[data-quarter-prev]")
    link.evaluate("element => { element.target = '_blank'; }")
    with motion_page.expect_popup() as popup_info:
        link.click()
    popup = popup_info.value
    popup.wait_for_load_state("load")
    popup.wait_for_selector('[data-page="quarter"][data-archive-app]')
    assert popup.locator(
        '[data-page="quarter"][data-archive-app]'
    ).get_attribute("data-quarter") == "2026-04"
    assert motion_page.url.endswith("/2026-07/index.html")
    assert motion_page.evaluate(
        "sessionStorage.getItem('bsb-quarter-motion')"
    ) is None
    assert popup.evaluate("sessionStorage.getItem('bsb-quarter-motion')") is None


def test_reduced_motion_remains_a_direct_path(
    motion_page: Page,
    site_server: str,
) -> None:
    _open_quarter(motion_page, site_server)
    motion_page.emulate_media(reduced_motion="reduce")
    motion_page.evaluate("""() => {
      const probe = window.__reducedMotionWork = {
        enabled: true, geometryReads: 0, animationScans: 0, animationsStarted: 0,
      };
      const rect = Element.prototype.getBoundingClientRect;
      Element.prototype.getBoundingClientRect = function() {
        if (probe.enabled && this.closest('[data-record-key]')) {
          probe.geometryReads += 1;
        }
        return rect.call(this);
      };
      const getAnimations = Element.prototype.getAnimations;
      Element.prototype.getAnimations = function(...args) {
        if (probe.enabled && this.closest('[data-archive-app]')) {
          probe.animationScans += 1;
        }
        return getAnimations.apply(this, args);
      };
      const animate = Element.prototype.animate;
      Element.prototype.animate = function(...args) {
        if (probe.enabled) probe.animationsStarted += 1;
        return animate.apply(this, args);
      };
    }""")
    motion_page.locator('[data-view-mode="list"]').click()
    motion_page.locator('[data-media-mode="movie"]').click()
    work = motion_page.evaluate("""() => {
      const probe = window.__reducedMotionWork;
      probe.enabled = false;
      return {
        geometryReads: probe.geometryReads,
        animationScans: probe.animationScans,
        animationsStarted: probe.animationsStarted,
      };
    }""")
    state = motion_page.evaluate(
        """() => ({
          rowAnimations: [...document.querySelectorAll('.subject-row')]
            .reduce((count, node) => count + node.getAnimations().length, 0),
          resultRegionAnimations: document.querySelector('[data-list-sections]')
            .getAnimations().length,
        })"""
    )
    assert state == {"rowAnimations": 0, "resultRegionAnimations": 0}
    assert work == {
        "geometryReads": 0,
        "animationScans": 0,
        "animationsStarted": 0,
    }
