"""Targeted browser coverage for the unified static site."""

from __future__ import annotations

import functools
import http.server
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from bgm_side_b import __version__
from bgm_side_b.repository import SubjectRepository
from tests.test_site_builder import _build_fixture, _build_preview_fixture

pytestmark = [pytest.mark.browser, pytest.mark.deep_regression]

_NEXT_SAFE_PORT = 18280


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


def _catalog_gate(status: int = 200) -> dict[str, object]:
    return {
        "requested": threading.Event(),
        "release": threading.Event(),
        "finished": threading.Event(),
        "status": status,
    }


@contextmanager
def _archive_catalog_server(
    site: Path,
    gates: dict[str, list[dict[str, object]]],
) -> Iterator[str]:
    request_counts: dict[str, int] = {}
    lock = threading.Lock()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self) -> None:
            parts = urlparse(self.path).path.split("/")
            gate_key = None
            gate = None
            if len(parts) == 3 and parts[1:] == ["data", "archive-index.json"]:
                gate_key = "archive-index"
            elif len(parts) == 4 and parts[1:3] == ["data", "catalog"]:
                filename = parts[3]
                if filename.endswith(".json") and filename[:-5].isdigit():
                    year = int(filename[:-5])
                    gate_key = f"catalog:{year}"
            elif len(parts) == 4 and parts[1:3] == ["data", "quarters"]:
                filename = parts[3]
                if filename.endswith(".json"):
                    gate_key = f"quarter:{filename[:-5]}"
            if gate_key is not None:
                with lock:
                    position = request_counts.get(gate_key, 0)
                    request_counts[gate_key] = position + 1
                    resource_gates = gates.get(gate_key, [])
                    gate = (
                        resource_gates[position]
                        if position < len(resource_gates)
                        else None
                    )
                if gate is not None:
                    requested = gate["requested"]
                    release = gate["release"]
                    assert isinstance(requested, threading.Event)
                    assert isinstance(release, threading.Event)
                    requested.set()
                    if not release.wait(timeout=30):
                        self.send_error(504, "archive resource gate timed out")
                        finished = gate["finished"]
                        assert isinstance(finished, threading.Event)
                        finished.set()
                        return
                    status = int(gate["status"])
                    if status != 200:
                        self.send_response(status)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        finished = gate["finished"]
                        assert isinstance(finished, threading.Event)
                        finished.set()
                        return
            super().do_GET()
            if gate is not None:
                finished = gate["finished"]
                assert isinstance(finished, threading.Event)
                finished.set()

        def log_message(self, format: str, *args: object) -> None:
            return

    handler = functools.partial(Handler, directory=str(site))
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        for year_gates in gates.values():
            for gate in year_gates:
                release = gate["release"]
                assert isinstance(release, threading.Event)
                release.set()
        server.shutdown()
        thread.join()
        server.server_close()


def _add_second_archive_year(site: Path) -> None:
    catalog_path = site / "data" / "catalog" / "2026.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    record = next(
        item
        for item in catalog["records"]
        if item["id"] == 101
        and item["quarter"] == "2026-04"
        and item["appearance"] == "premiere"
    )
    current = {**record, "preferred_title": "Fixture 2026"}
    previous = {
        **record,
        "id": 505,
        "preferred_title": "Fixture 2025",
        "original_title": "Fixture Original 2025",
        "quarter": "2025-04",
        "air_date": "2025-04-02",
        "premiere_quarter": "2025-04",
    }
    catalog["records"] = [current]
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    (site / "data" / "catalog" / "2025.json").write_text(
        json.dumps({"year": 2025, "records": [previous]}),
        encoding="utf-8",
    )

    index_path = site / "data" / "archive-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["years"] = [2025, 2026]
    index["quarters"] = [
        {"quarter": "2025-04", "year": 2025, "count": 1},
        {"quarter": "2026-04", "year": 2026, "count": 1},
    ]
    index["latest_quarter"] = "2026-04"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    quarter_path = site / "data" / "quarters" / "2026-04.json"
    quarter = json.loads(quarter_path.read_text(encoding="utf-8"))
    quarter["tv"]["premiere"] = [
        {**quarter["tv"]["premiere"][0], "preferred_title": "Fixture 2026"}
    ]
    quarter_path.write_text(json.dumps(quarter), encoding="utf-8")
    quarter["quarter"] = "2025-04"
    quarter["tv"]["premiere"] = [
        {
            **quarter["tv"]["premiere"][0],
            "subject_id": 505,
            "preferred_title": "Fixture 2025",
            "original_title": "Fixture Original 2025",
            "air_date": "2025-04-02",
            "premiere_quarter": "2025-04",
        }
    ]
    quarter["tv"]["continuing"] = []
    quarter["movie"]["premiere"] = []
    (site / "data" / "quarters" / "2025-04.json").write_text(
        json.dumps(quarter),
        encoding="utf-8",
    )


def _open_two_year_archive(
    page: Page,
    site: Path,
    server: str,
    viewport: tuple[int, int] = (1440, 900),
) -> None:
    _add_second_archive_year(site)
    page.add_init_script(
        """(() => {
          window.__catalogResponses = [];
          const fetchResource = window.fetch.bind(window);
          window.fetch = (input, init) => {
            const url = String(input instanceof Request ? input.url : input);
            return fetchResource(input, init).then(response => {
              if (url.includes('/data/catalog/')) {
                window.__catalogResponses.push({url, status: response.status});
              }
              return response;
            });
          };
        })();"""
    )
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    page.goto(f"{server}/archive/index.html?year=2026")
    _wait_archive_scope(page, "YEAR / 2026")
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
    assert page.evaluate(
        "window.__catalogResponses?.some(response => "
        "response.url.includes('/data/catalog/2026.json'))"
    )


def _choose_archive_year(page: Page, year: int) -> None:
    page.locator("[data-archive-year-select] .select-trigger").click()
    page.get_by_role("option", name=str(year), exact=True).click()


def _wait_archive_scope(page: Page, label: str) -> None:
    page.wait_for_function(
        "expected => {"
        "const root = document.querySelector('[data-archive-app][data-page=archive]');"
        "const master = document.querySelector('.master-pane');"
        "const scope = document.querySelector('[data-archive-scope-label]');"
        "return root?.dataset.scopePending !== 'true' && !master?.inert "
        "&& scope?.textContent.includes(expected);"
        "}",
        arg=label,
    )


@pytest.fixture
def unified_site(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    yield tmp_path / "dist" / "site"


@pytest.fixture
def unified_site_571784(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(tmp_path, primary_subject_id=571784)
    builder.build()
    yield tmp_path / "dist" / "site"


@pytest.fixture(params=("chromium", "webkit"))
def preview_browser(request: pytest.FixtureRequest) -> Iterator[Browser]:
    with sync_playwright() as runner:
        browser = getattr(runner, request.param).launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def preview_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    builder, _ = _build_preview_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist/site"
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(site)
    )
    server = _server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", site
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("page_kind", ["quarter", "archive"])
def test_browse_previews_keep_original_detail_and_bounded_fallback(
    preview_browser: Browser,
    preview_server: tuple[str, Path],
    page_kind: str,
) -> None:
    base, site = preview_server
    quarter = json.loads((site / "data/quarters/2026-07.json").read_text("utf-8"))
    original = quarter["tv"]["continuing"][0]["cover_url"]
    markup = (site / "2026-07/index.html").read_text("utf-8")
    from bgm_side_b.build.site_builder import _cover_preview_path
    preview = _cover_preview_path(original)
    assert preview is not None and preview in markup
    context = preview_browser.new_context(
        service_workers="block", viewport={"width": 390, "height": 844},
    )
    page = context.new_page()
    page.set_default_timeout(20000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    path = "2026-07/index.html" if page_kind == "quarter" else "archive/index.html"
    page.goto(f"{base}/{path}")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )
    primary = page.locator('.subject-row[data-subject-id="101"]').first
    image = primary.locator(".subject-row__cover img")
    page.wait_for_function(
        "[...document.querySelectorAll('.subject-row__cover img')]"
        ".every(image => image.complete && image.naturalWidth > 0)"
    )
    assert image.evaluate("node => node.currentSrc") == f"{base}/{preview}"
    assert page.locator('.subject-row[data-subject-id="303"] img').evaluate(
        "node => node.naturalWidth"
    ) == 200
    assert page.locator(
        '.subject-row[data-subject-id="304"] .subject-row__cover--missing'
    ).is_visible()
    assert not any("/covers/101.webp" in url for url in requests)
    assert all(urlparse(url).netloc == urlparse(base).netloc for url in requests)
    primary.evaluate("node => { window.__previewRow = node; }")
    page.locator('[data-view-mode="list"]').click()
    page.locator('[data-view-mode="grid"]').click()
    page.locator("[data-search]").fill("303")
    assert page.locator(".subject-row:visible").count() == 1
    page.locator("[data-search]").fill("")
    assert primary.evaluate("node => node === window.__previewRow")
    primary.locator("[data-open-subject]").click()
    detail = page.locator("[data-detail-panel]")
    detail.wait_for(state="visible")
    page.wait_for_function(
        "document.querySelector('.detail-cover-button img')?.naturalWidth === 800"
    )
    assert detail.locator(".detail-cover-button img").evaluate(
        "node => node.currentSrc"
    ) == f"{base}/{original}"
    detail.locator("[data-lightbox]").click()
    assert page.locator(".cover-lightbox img").evaluate(
        "node => node.currentSrc"
    ) == f"{base}/{original}"
    page.keyboard.press("Escape")
    page.go_back()
    detail.wait_for(state="hidden")
    assert image.evaluate("node => node.currentSrc") == f"{base}/{preview}"
    page.route(f"**/{preview}", lambda route: route.fulfill(status=404, body="missing"))
    page.reload()
    page.wait_for_function(
        "document.querySelector('[data-subject-id=\"101\"] .subject-row__cover img')"
        "?.naturalWidth === 800"
    )
    assert image.get_attribute("src") == f"../{original}"
    requests.clear()
    page.route(
        "**/covers/101.webp*", lambda route: route.fulfill(status=404, body="missing")
    )
    page.reload()
    primary.locator(".subject-row__cover--missing").wait_for(state="visible")
    assert primary.locator("img").count() == 0
    assert sum("/covers/101.webp" in url for url in requests) <= (
        1 if page_kind == "quarter" else 2
    )
    assert page.evaluate("""async original => {
      const root = document.querySelector('[data-archive-app]');
      delete root.dataset.coverPreviewRule;
      return await window.BsbArchive.browseCoverPath(root, original);
    }""", original) == original
    context.close()


@pytest.fixture
def unified_site_mixed(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(tmp_path, include_same_quarter_tv=True)
    builder.build()
    yield tmp_path / "dist" / "site"


@pytest.fixture
def unified_site_many(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(
        tmp_path,
        include_same_quarter_tv=True,
        extra_same_quarter_tv=24,
    )
    builder.build()
    yield tmp_path / "dist" / "site"


@pytest.fixture
def unified_site_motion_large(tmp_path: Path) -> Iterator[Path]:
    builder, database = _build_fixture(
        tmp_path,
        include_same_quarter_tv=True,
        extra_same_quarter_tv=500,
    )
    repository = SubjectRepository(database)
    snapshots = repository.get_subject_facts_many(range(304, 804))
    with repository.transaction() as connection:
        for subject_id, snapshot in snapshots.items():
            varied_subject = replace(
                snapshot.subject,
                rating_score=4.0 + (subject_id % 500) / 1000,
                rating_count=100 + subject_id,
            )
            repository.replace_subject_snapshot(
                connection,
                replace(snapshot, subject=varied_subject),
            )
    builder.build()
    yield tmp_path / "dist" / "site"


class BrowserHarness:
    def __init__(self, context: BrowserContext) -> None:
        self.context = context

    def new_page(self, *, viewport: dict[str, int] | None = None) -> Page:
        page = self.context.new_page()
        # Keep interaction assertions deterministic while native View
        # Transition tests opt into motion explicitly below.  The production
        # bundle still follows the browser's actual media preference.
        page.emulate_media(reduced_motion="reduce")
        if viewport is not None:
            page.set_viewport_size(viewport)
        return page


@pytest.fixture
def chromium() -> Iterator[BrowserHarness]:
    with sync_playwright() as runner:
        browser = runner.chromium.launch()
        context = browser.new_context(service_workers="block")
        try:
            yield BrowserHarness(context)
        finally:
            context.close()
            browser.close()


@pytest.fixture
def site_server(unified_site: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site),
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


@pytest.fixture
def site_server_571784(unified_site_571784: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site_571784),
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


@pytest.fixture
def site_server_mixed(unified_site_mixed: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site_mixed),
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


@pytest.fixture
def site_server_many(unified_site_many: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site_many),
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


@pytest.fixture
def site_server_motion_large(unified_site_motion_large: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site_motion_large),
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


def _open_quarter(page: Page, root: str, viewport: tuple[int, int]) -> None:
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto(f"{root}/2026-07/index.html")
    page.wait_for_selector(".subject-row")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )


@pytest.mark.release_smoke
def test_quarter_tv_appearances_share_one_list_and_static_navigation(
    chromium: BrowserContext,
    site_server_mixed: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _open_quarter(page, site_server_mixed, (390, 844))
    assert page.evaluate(
        "getComputedStyle(document.documentElement).touchAction"
    ) == "manipulation"

    assert page.locator('[data-list-section="tv"]').count() == 1
    assert page.locator('[data-list-section="movie"]').count() == 1
    assert page.locator('[data-list-section="tv"] .subject-row').count() == 2
    assert page.locator('[data-appearance-badge="continuing"]').count() == 1
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("首播").check()
    page.get_by_role("button", name="返回结果").click()
    assert "1 / 2" in page.locator("[data-results-summary]").inner_text()

    assert page.locator("[data-quarter-prev]").get_attribute("href") == (
        "../2026-04/index.html"
    )
    assert page.locator("[data-quarter-next]").get_attribute("aria-disabled") == "true"
    page.locator("[data-quarter-selector]").click()
    assert page.locator("[data-quarter-sheet]").is_visible()
    assert page.locator('[data-quarter-option="2026-04"]').is_visible()
    assert page.locator('[data-quarter-option="2026-01"]').get_attribute(
        "aria-disabled"
    ) == "true"

    page.locator("[data-mobile-menu-toggle]").click()
    assert page.locator("[data-mobile-menu]").is_visible()
    assert page.get_by_role("link", name="离线与设置").is_visible()


@pytest.mark.release_smoke
@pytest.mark.parametrize(
    "viewport",
    [(393, 852), (390, 844), (360, 800), (430, 932), (844, 390)],
)
def test_mobile_menu_uses_top_layer_and_closes_competing_surfaces(
    chromium: BrowserContext,
    site_server_mixed: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_mixed, viewport)

    menu_toggle = page.locator("[data-mobile-menu-toggle]")
    if not menu_toggle.is_visible():
        assert viewport[0] > 767
        assert page.locator(".site-nav").is_visible()
        return
    menu_toggle.click()
    assert page.locator("[data-mobile-menu]").is_visible()
    overlay = page.locator("[data-mobile-menu]").evaluate(
        """menu => {
          const rect = menu.getBoundingClientRect();
          const trigger = document.querySelector("[data-mobile-menu-toggle]");
          const triggerRect = trigger.getBoundingClientRect();
          const x = Math.round(rect.left + rect.width / 2);
          const y = Math.round(rect.top + rect.height / 2);
          const hit = document.elementFromPoint(x, y);
          const style = getComputedStyle(menu);
          return {
            mode: menu.dataset.menuMode,
            open: menu.dataset.menuOpen,
            position: style.position,
            background: style.backgroundColor,
            width: rect.width,
            triggerGap: rect.top - triggerRect.bottom,
            rightDelta: Math.abs(rect.right - triggerRect.right),
            withinViewport: rect.left >= 0 && rect.right <= innerWidth
              && rect.top >= 0 && rect.bottom <= innerHeight,
            hitMenu: hit === menu || menu.contains(hit),
          };
        }"""
    )
    assert overlay["mode"] in {"popover", "fallback"}
    assert overlay["open"] == "true"
    assert overlay["position"] == "fixed"
    assert overlay["background"] not in {"transparent", "rgba(0, 0, 0, 0)"}
    assert overlay["width"] <= 225
    assert overlay["withinViewport"]
    assert overlay["hitMenu"]
    assert 4 <= overlay["triggerGap"] <= 14
    assert overlay["rightDelta"] <= 2
    menu = page.locator("[data-mobile-menu]")
    assert menu.get_by_role("link", name="档案", exact=True).is_visible()
    assert menu.get_by_role("link", name="Archive", exact=True).count() == 0

    # Allow native top-layer hit testing to settle before the real outside click.
    page.wait_for_function(
        """() => new Promise(resolve => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })"""
    )
    page.mouse.click(2, 2)
    page.wait_for_function(
        """() => {
          const menu = document.querySelector("[data-mobile-menu]");
          if (!menu) return true;
          return menu.dataset.menuMode === "popover"
            ? !menu.matches(":popover-open")
            : menu.dataset.menuOpen === "false";
        }"""
    )
    page.locator("[data-mobile-menu]").wait_for(state="hidden")
    assert not page.locator("[data-mobile-menu]").is_visible()
    assert page.locator("[data-mobile-menu]").get_attribute(
        "data-menu-open"
    ) == "false"
    assert menu_toggle.get_attribute("aria-expanded") == "false"
    page.locator("[data-mobile-menu-toggle]").click()
    page.keyboard.press("Escape")
    assert not page.locator("[data-mobile-menu]").is_visible()
    assert page.locator("[data-mobile-menu-toggle]").get_attribute(
        "aria-expanded"
    ) == "false"

    page.locator("[data-sort-toggle]").click()
    assert page.locator("[data-sort-popover]").is_visible()
    page.locator("[data-mobile-menu-toggle]").click()
    assert page.locator("[data-mobile-menu]").is_visible()
    assert not page.locator("[data-sort-popover]").is_visible()

    # A top-layer menu intentionally intercepts clicks on controls beneath it;
    # close it before opening the quarter sheet instead of clicking through it.
    page.keyboard.press("Escape")
    assert not page.locator("[data-mobile-menu]").is_visible()
    page.locator("[data-quarter-selector]").click()
    assert page.locator("[data-quarter-sheet]").is_visible()
    page.locator("[data-mobile-menu-toggle]").click()
    assert page.locator("[data-mobile-menu]").is_visible()
    assert not page.locator("[data-quarter-sheet]").is_visible()


@pytest.mark.parametrize("viewport", [(393, 852), (390, 844), (360, 800), (430, 932)])
def test_mobile_quarter_is_cover_first_and_continuous(
    chromium: BrowserContext,
    site_server_many: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, viewport)
    tv_rows = page.locator('[data-list-section="tv"] .subject-row')
    assert tv_rows.count() == 26
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 26
    assert page.locator("[data-pager]").is_hidden()
    assert page.locator("[data-page-size]").evaluate(
        "node => getComputedStyle(node.parentElement).display"
    ) == "none"
    columns = page.locator('[data-list-section="tv"] .result-list').evaluate(
        "node => getComputedStyle(node).gridTemplateColumns"
    )
    assert len(columns.split()) == 2
    cover = tv_rows.first.locator(".subject-row__cover")
    assert cover.evaluate("node => getComputedStyle(node).display") == "block"
    box = cover.bounding_box()
    assert box is not None and abs((box["width"] / box["height"]) - (2 / 3)) < 0.08
    score = tv_rows.first.locator(".subject-row__score")
    score_style = score.evaluate("node => getComputedStyle(node)")
    assert score_style["position"] == "absolute"
    score_box = score.bounding_box()
    assert score_box is not None and score_box["y"] <= box["y"] + 12
    assert score_box["x"] + score_box["width"] <= box["x"] + box["width"] + 1
    assert tv_rows.first.locator(".subject-row__score small").evaluate(
        "node => Number.parseFloat(getComputedStyle(node).opacity) < 1"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert tv_rows.first.locator("[data-open-subject]").bounding_box()["height"] >= 44


@pytest.mark.parametrize("viewport", [(360, 800), (390, 844), (393, 852), (430, 932)])
def test_mobile_grid_metadata_is_compact_and_one_line(
    chromium: BrowserContext,
    site_server_many: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, viewport)

    metadata = page.locator(
        '[data-list-section="tv"] .subject-row__meta'
    ).first
    assert metadata.locator(".subject-row__meta-full").is_hidden()
    assert metadata.locator(".subject-row__meta-grid").is_visible()
    details = metadata.evaluate(
        "node => ({whiteSpace: getComputedStyle(node).whiteSpace, "
        "textOverflow: getComputedStyle(node).textOverflow, "
        "scrollHeight: node.scrollHeight, clientHeight: node.clientHeight})"
    )
    assert details["whiteSpace"] == "nowrap"
    assert details["textOverflow"] == "clip"
    assert details["scrollHeight"] <= details["clientHeight"] + 1
    assert "2026-" not in metadata.locator(".subject-row__meta-grid").inner_text()


def test_archive_grid_keeps_full_date_for_list_and_desktop_grid(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 430, "height": 932})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_selector('[data-page="archive"] .subject-row')
    metadata = page.locator('[data-page="archive"] .subject-row__meta').first
    assert metadata.locator(".subject-row__meta-full").is_hidden()
    assert metadata.locator(".subject-row__meta-grid").is_visible()
    assert "26-" in metadata.locator(".subject-row__meta-grid").inner_text()

    page.locator('[data-page="archive"] [data-view-mode="list"]').click()
    assert metadata.locator(".subject-row__meta-full").is_visible()
    assert metadata.locator(".subject-row__meta-grid").is_hidden()

    page.set_viewport_size({"width": 1024, "height": 768})
    page.locator(
        '[data-page="archive"] .view-switch [data-view-mode="grid"]'
    ).click()
    assert metadata.locator(".subject-row__meta-full").is_visible()
    assert metadata.locator(".subject-row__meta-grid").is_hidden()


def test_grid_list_view_switch_persists_and_preserves_detail(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (390, 844))
    root = page.locator('[data-page="quarter"][data-archive-app]')
    switch = page.locator(".view-switch")
    assert root.get_attribute("data-view-mode") == "grid"
    assert switch.locator('[data-view-mode="grid"]').get_attribute(
        "aria-pressed"
    ) == "true"
    page.evaluate("""() => {
      window.__quarterViewRow = document.querySelector(
        '[data-list-section="tv"] .subject-row'
      );
    }""")

    switch.locator('[data-view-mode="list"]').click()
    assert root.get_attribute("data-view-mode") == "list"
    assert switch.locator('[data-view-mode="list"]').get_attribute(
        "aria-pressed"
    ) == "true"
    assert page.evaluate("""() => window.__quarterViewRow === document.querySelector(
      '[data-list-section="tv"] .subject-row'
    )""")
    assert page.evaluate("document.activeElement.dataset.viewMode") == "list"
    assert page.evaluate("localStorage.getItem('bsb-browse-view-mode')") == "list"
    assert len(
        page.locator('[data-list-section="tv"] .result-list').evaluate(
            "node => getComputedStyle(node).gridTemplateColumns"
        ).split()
    ) == 1
    list_cover = page.locator('[data-list-section="tv"] .subject-row__cover').first
    list_cover_box = list_cover.bounding_box()
    assert list_cover_box is not None and list_cover_box["width"] < 80
    assert page.locator('[data-list-section="tv"] .subject-row__score').first.evaluate(
        "node => getComputedStyle(node).position"
    ) == "static"

    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert root.get_attribute("data-view-mode") == "list"
    page.locator("[data-detail-panel] [data-detail-close]").click()
    page.reload()
    page.wait_for_selector(".subject-row")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )
    assert root.get_attribute("data-view-mode") == "list"

    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    archive_root = page.locator('[data-page="archive"][data-archive-app]')
    assert archive_root.get_attribute("data-view-mode") == "list"
    page.evaluate("""() => {
      window.__archiveViewRow = document.querySelector(
        '[data-list-section="tv"] .subject-row'
      );
    }""")
    archive_cover = page.locator(
        '[data-subject-id="101"][data-quarter="2026-07"] .subject-row__cover'
    ).first
    archive_cover_box = archive_cover.bounding_box()
    assert archive_cover_box is not None and archive_cover_box["width"] < 80
    page.locator('[data-page="archive"] [data-view-mode="grid"]').click()
    assert archive_root.get_attribute("data-view-mode") == "grid"
    assert page.locator(
        '[data-page="archive"] .view-switch [data-view-mode="grid"]'
    ).get_attribute("aria-pressed") == "true"
    assert page.evaluate("""() => window.__archiveViewRow === document.querySelector(
      '[data-list-section="tv"] .subject-row'
    )""")
    assert len(
        page.locator('[data-list-section="tv"] .result-list').evaluate(
            "node => getComputedStyle(node).gridTemplateColumns"
        ).split()
    ) == 2
    grid_cover_box = page.locator(
        '[data-subject-id="101"][data-quarter="2026-07"] .subject-row__cover'
    ).first.bounding_box()
    assert grid_cover_box is not None and grid_cover_box["width"] > 80
    page.reload()
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    assert archive_root.get_attribute("data-view-mode") == "grid"
    assert len(
        page.locator('[data-list-section="tv"] .result-list').evaluate(
            "node => getComputedStyle(node).gridTemplateColumns"
        ).split()
    ) == 2


@pytest.mark.release_smoke
@pytest.mark.parametrize(
    ("page_path", "page_kind"),
    [
        ("/2026-07/index.html", "quarter"),
        ("/archive/index.html?year=2026", "archive"),
    ],
)
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 390, "height": 844},
        {"width": 1440, "height": 900},
    ],
)
def test_grid_list_reduced_motion_disables_row_transitions_only(
    chromium: BrowserHarness,
    site_server: str,
    page_path: str,
    page_kind: str,
    viewport: dict[str, int],
) -> None:
    page = chromium.new_page(viewport=viewport)
    page.set_default_timeout(15000)
    page.goto(f"{site_server}{page_path}", wait_until="domcontentloaded")
    page.wait_for_function(
        """needle => document.querySelector('[data-results-summary]')
          ?.textContent.includes(needle)""",
        arg=" / " if page_kind == "quarter" else "appearance",
    )
    root = page.locator(
        f'.{page_kind}-page[data-page="{page_kind}"]'
    )
    row = page.locator('[data-list-section="tv"] .subject-row').first
    page.evaluate(
        """() => {
          window.__reducedMotionRow = document.querySelector(
            '[data-list-section="tv"] .subject-row'
          );
        }"""
    )

    list_button = page.locator('[data-view-mode="list"]')
    list_button.scroll_into_view_if_needed()
    scroll_before = page.evaluate("window.scrollY")
    list_button.click()
    assert root.get_attribute("data-view-mode") == "list"
    assert page.evaluate("window.scrollY") == scroll_before
    assert page.evaluate(
        """() => window.__reducedMotionRow === document.querySelector(
          '[data-list-section="tv"] .subject-row'
        )"""
    )
    assert page.evaluate("document.activeElement.dataset.viewMode") == "list"
    reduced_properties = row.evaluate(
        """node => ({
          row: getComputedStyle(node).transitionProperty,
          content: getComputedStyle(
            node.querySelector('.subject-row__content')
          ).transitionProperty,
          openButton: getComputedStyle(
            node.querySelector('.subject-row__open')
          ).transitionProperty
        })"""
    )
    assert reduced_properties == {
        "row": "none",
        "content": "none",
        "openButton": "none",
    }

    grid_button = page.locator('[data-view-mode="grid"]')
    grid_button.scroll_into_view_if_needed()
    scroll_before = page.evaluate("window.scrollY")
    grid_button.click()
    assert root.get_attribute("data-view-mode") == "grid"
    assert page.evaluate("window.scrollY") == scroll_before
    assert page.evaluate(
        """() => window.__reducedMotionRow === document.querySelector(
          '[data-list-section="tv"] .subject-row'
        )"""
    )
    assert page.evaluate("document.activeElement.dataset.viewMode") == "grid"

    page.emulate_media(reduced_motion="no-preference")
    normal_motion = row.evaluate(
        """node => ({
          properties: getComputedStyle(node).transitionProperty,
          duration: getComputedStyle(node).transitionDuration
        })"""
    )
    assert normal_motion["properties"] == "opacity, transform"
    assert normal_motion["duration"].split(", ") == ["0.18s", "0.18s"]


@pytest.mark.parametrize("mode", ["list", "grid"])
def test_browse_continuity_keeps_mode_across_sort_filter_quarter_and_back(
    chromium: BrowserContext,
    site_server_mixed: str,
    mode: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.add_init_script(
        "if (!sessionStorage.getItem('bsb-test-view-reset')) { "
        "localStorage.removeItem('bsb-browse-view-mode'); "
        "sessionStorage.setItem('bsb-test-view-reset', '1'); }"
    )
    _open_quarter(page, site_server_mixed, (390, 844))
    root = page.locator('[data-page="quarter"][data-archive-app]')
    if mode == "list":
        page.locator('[data-view-mode="list"]').click()
    assert root.get_attribute("data-view-mode") == mode

    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '1800px'; }"
    )
    page.locator("[data-sort-toggle]").scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    page.locator("[data-sort-toggle]").click()
    page.get_by_role("menuitemradio", name="评分：低到高").click()
    assert root.get_attribute("data-view-mode") == mode
    assert abs(page.evaluate("window.scrollY") - saved_scroll) <= 1
    columns = page.locator('[data-list-section="tv"] .result-list').evaluate(
        "node => getComputedStyle(node).gridTemplateColumns"
    )
    assert len(columns.split()) == (1 if mode == "list" else 2)

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("续播").check()
    page.get_by_role("button", name="返回结果").click()
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 1
    page.locator("[data-filter-toggle]").click()
    page.locator("[data-filter-workspace-clear]").click()
    page.get_by_role("button", name="返回结果").click()
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 2

    page.locator("[data-quarter-prev]").click()
    page.wait_for_url("**/2026-04/index.html")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )
    next_root = page.locator('[data-page="quarter"][data-archive-app]')
    assert next_root.get_attribute("data-view-mode") == mode
    page.go_back()
    page.wait_for_url("**/2026-07/index.html")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )
    assert page.locator('[data-page="quarter"][data-archive-app]').get_attribute(
        "data-view-mode"
    ) == mode


@pytest.mark.parametrize("mode", ["list", "grid"])
def test_saved_view_mode_applies_before_delayed_app_bundle(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
    mode: str,
) -> None:
    bundle = (unified_site / "assets" / "app.js").read_text(encoding="utf-8")
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        f"localStorage.setItem('bsb-browse-view-mode', '{mode}')"
    )
    page.route(
        "**/assets/app.js*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body=f"setTimeout(() => {{ {bundle} }}, 900);",
        ),
    )
    page.goto(f"{site_server}/2026-07/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('[data-page="quarter"][data-archive-app]')
    assert page.locator("html").get_attribute("data-browse-view-mode") == mode
    metrics = page.locator('[data-list-section="tv"] .result-list').evaluate(
        "node => ({columns: getComputedStyle(node).gridTemplateColumns, "
        "coverWidth: node.querySelector('.subject-row__cover')"
        ".getBoundingClientRect().width})"
    )
    if mode == "list":
        assert len(metrics["columns"].split()) == 1
        assert metrics["coverWidth"] < 80
    else:
        assert len(metrics["columns"].split()) == 2
    page.wait_for_timeout(1500)
    assert page.locator("html").get_attribute("data-browse-hydrated") == "true"
    assert page.locator("html").get_attribute("data-browse-view-mode") is None
    assert page.locator('main[data-page="quarter"]').get_attribute(
        "data-view-mode"
    ) == mode, errors


@pytest.mark.release_smoke
def test_desktop_grid_list_view_switch_changes_density(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (1440, 900))
    root = page.locator('[data-page="quarter"][data-archive-app]')
    result_list = page.locator('[data-list-section="tv"] .result-list')

    def column_count() -> int:
        columns = result_list.evaluate(
            "node => getComputedStyle(node).gridTemplateColumns"
        )
        return len(columns.split())

    assert root.get_attribute("data-view-mode") == "grid"
    assert column_count() >= 2
    page.locator('[data-view-mode="list"]').click()
    assert column_count() == 1
    cover = page.locator(
        '[data-list-section="tv"] .subject-row__cover'
    ).first.bounding_box()
    assert cover is not None and cover["width"] == 52
    page.locator('[data-view-mode="grid"]').click()
    assert column_count() >= 2


def test_quarter_detail_movie_history_and_lightbox(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    _open_quarter(page, site_server, (1440, 900))
    list_cover = page.locator(
        '[data-subject-id="101"] .subject-row__cover img'
    ).first
    page.wait_for_function(
        """() => {
          const image = document.querySelector(
            '[data-subject-id="101"] .subject-row__cover img'
          );
          return image?.complete && image.naturalWidth > 0;
        }"""
    )
    preview_url = list_cover.evaluate("node => node.currentSrc")
    assert "/covers/previews/" in preview_url
    assert preview_url in requests
    assert not any("/covers/101.webp?v=" in url for url in requests)

    page.goto(f"{site_server}/2026-07/index.html#bgm-101")
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail_cover = page.locator(
        '[data-detail-panel] .detail-cover-button img'
    )
    page.wait_for_function(
        """() => {
          const image = document.querySelector(
            '[data-detail-panel] .detail-cover-button img'
          );
          return image?.complete && image.naturalWidth > 0;
        }"""
    )
    detail_cover_url = detail_cover.evaluate("node => node.currentSrc")
    assert "/covers/101.webp?v=" in detail_cover_url
    assert detail_cover_url in requests
    detail = page.locator("[data-detail-panel]").inner_text()
    assert "续播" in detail
    assert "2026-04" in detail
    assert "2026-07" in detail
    assert "结束日期" not in detail

    page.locator('[data-media-mode="movie"]').click()
    assert page.locator('[data-list-section="tv"]').is_hidden()
    assert page.locator('[data-list-section="movie"]').is_visible()
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
    page.locator('[data-subject-id="202"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert "MOVIE" in page.locator("[data-detail-panel]").inner_text()
    page.locator("[data-detail-panel] [data-detail-close]").click()
    assert page.locator("[data-detail-panel]").is_hidden()


@pytest.mark.parametrize(
    "viewport",
    [(1920, 1080), (1440, 900), (1024, 768), (768, 1024), (390, 844), (360, 800)],
)
def test_settings_changelog_is_static_accessible_and_narrow_safe(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    page.goto(f"{site_server}/settings/index.html")
    page.wait_for_selector('[data-changelog-release="0.8.0"]', state="attached")
    assert page.get_by_text("当前程序版本", exact=True).is_visible()
    assert (
        page.locator(".settings-about")
        .get_by_text(__version__, exact=True)
        .is_visible()
    )
    assert page.get_by_text("Bangumi Side B｜MyKr", exact=True).is_visible()
    assert page.get_by_text("作者", exact=True).is_visible()
    assert page.get_by_text("MyKr", exact=True).is_visible()
    assert "Settings · Bangumi Side B｜MyKr" == page.title()
    assert page.locator(".site-footer").get_by_text(
        "Bangumi Side B · MyKr", exact=True
    ).is_visible()
    assert page.locator('[data-changelog-release="0.8.0"]').evaluate(
        "node => node.tagName"
    ) == "SECTION"
    assert page.locator('[data-changelog-milestone="0.6"]').count() == 1
    assert not page.locator('[data-changelog-milestone="0.6"]').evaluate(
        "node => node.open"
    )
    assert page.locator('[data-changelog-milestone="0.6"] time').get_attribute(
        "datetime"
    ) == "2026-08-23"
    assert page.locator('[data-changelog-release="0.6.0"]').count() == 1
    assert not page.locator('[data-changelog-milestone="0.2"]').evaluate(
        "node => node.open"
    )
    assert page.locator('[data-changelog-milestone="0.1"]').count() == 1
    assert page.locator('[data-changelog-milestone="0.1"] time').get_attribute(
        "datetime"
    ) == "2026-07-29"
    assert page.locator(".settings-section h2").all_text_contents() == [
        "离线季度",
        "下载任务",
        "批量下载",
        "存储与应用",
        "高级诊断",
        "更新日志",
    ]
    page.wait_for_function(
        "document.querySelector('[data-settings-app]')?.textContent"
        ".includes('Service Worker')"
    )
    assert "Service Worker" not in page.locator(
        '[aria-labelledby="settings-offline-title"]'
    ).inner_text()
    assert "Service Worker" in page.locator(
        '[aria-labelledby="settings-diagnostics-title"]'
    ).inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not any("CHANGELOG" in url or "github.com" in url for url in requests)


def test_desktop_detail_workspace_uses_viewport_height_and_internal_scroll(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (1440, 900))
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    metrics = page.locator("[data-detail-panel]").evaluate(
        "node => ({height: node.getBoundingClientRect().height, "
        "viewport: window.innerHeight, overflowY: getComputedStyle(node).overflowY})"
    )
    assert metrics["overflowY"] == "auto"
    assert metrics["height"] >= metrics["viewport"] - 48

    page.locator('[data-media-mode="tv"]').click()
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    assert "?v=" in page.locator(
        "[data-detail-panel] [data-lightbox] img"
    ).get_attribute("src")
    page.locator("[data-detail-panel] [data-lightbox]").click()
    page.wait_for_selector(".cover-lightbox", state="attached")
    assert "?v=" in page.locator(".cover-lightbox img").get_attribute("src")
    assert page.locator(".cover-lightbox .lightbox-close").count() == 0
    assert page.locator(".cover-lightbox").get_attribute(
        "aria-label"
    ) == "查看 中文 101 封面"
    page.locator(".cover-lightbox img").click()
    assert page.locator(".cover-lightbox").count() == 1
    page.keyboard.press("Escape")
    assert page.locator(".cover-lightbox").count() == 0


@pytest.mark.parametrize("viewport", [(393, 852), (430, 932), (844, 390)])
def test_cover_lightbox_is_transparent_large_and_safe_area_ready(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.locator("[data-detail-panel] [data-lightbox]").click()
    page.wait_for_selector(".cover-lightbox", state="visible")
    metrics = page.locator(".cover-lightbox").evaluate(
        "node => { const style = getComputedStyle(node); "
        "const backdrop = getComputedStyle(node, '::backdrop'); "
        "const image = node.querySelector('img'); "
        "return {background: style.backgroundColor, border: style.borderWidth, "
        "maxWidth: style.maxWidth, maxHeight: style.maxHeight, "
        "imageMaxWidth: getComputedStyle(image).maxWidth, "
        "imageMaxHeight: getComputedStyle(image).maxHeight, "
        "objectFit: getComputedStyle(image).objectFit, "
        "backdrop: backdrop.backgroundColor, "
        "closeCount: node.querySelectorAll('.lightbox-close').length, "
        "dialogBox: node.getBoundingClientRect().toJSON(), "
        "imageBox: image.getBoundingClientRect().toJSON(), "
        "viewport: {width: window.innerWidth, height: window.innerHeight}}; }"
    )
    assert metrics["background"] in {"rgba(0, 0, 0, 0)", "transparent"}
    assert metrics["border"] == "0px"
    assert metrics["maxWidth"] != "86vw"
    assert metrics["maxHeight"] != "84dvh"
    assert metrics["imageMaxWidth"] != "100%"
    assert metrics["objectFit"] == "contain"
    assert metrics["backdrop"].startswith("rgba(")
    assert "0.62" in metrics["backdrop"] or "0.619608" in metrics["backdrop"]
    assert metrics["closeCount"] == 0
    dialog_box = metrics["dialogBox"]
    image_box = metrics["imageBox"]
    viewport_box = metrics["viewport"]
    assert abs(
        (image_box["x"] + image_box["width"] / 2) - viewport_box["width"] / 2
    ) <= 2
    assert abs(
        (image_box["y"] + image_box["height"] / 2) - viewport_box["height"] / 2
    ) <= 2
    assert dialog_box["width"] >= viewport_box["width"] - 2
    assert dialog_box["height"] >= viewport_box["height"] - 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator(".cover-lightbox").click(position={"x": 2, "y": 2})
    assert page.locator(".cover-lightbox").count() == 0


@pytest.mark.parametrize(
    "viewport",
    [
        (1920, 1080),
        (1440, 900),
        (1280, 800),
        (1366, 768),
        (1199, 800),
        (1024, 768),
        (900, 900),
        (768, 1024),
        (767, 900),
        (430, 932),
        (390, 844),
        (360, 800),
    ],
)
def test_quarter_shell_is_usable_across_plan16_viewports(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    if viewport[0] < 768:
        cover = page.locator('[data-subject-id="101"] .subject-row__cover')
        assert cover.evaluate("node => getComputedStyle(node).display") == "block"
        box = cover.bounding_box()
        assert box is not None
        assert abs((box["width"] / box["height"]) - (2 / 3)) < 0.08
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.wait_for_timeout(300)
    metrics = page.locator("[data-quarter-layout]").evaluate(
        "node => ({width: node.getBoundingClientRect().width, "
        "columns: getComputedStyle(node).gridTemplateColumns, "
        "master: node.querySelector('.master-pane').getBoundingClientRect().width, "
        "workspace: node.querySelector('.workspace').getBoundingClientRect().width, "
        "masterDisplay: getComputedStyle(node.querySelector('.master-pane')).display})"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if viewport[0] < 768:
        assert metrics["masterDisplay"] == "block"
        assert metrics["workspace"] >= metrics["width"] - 2
    else:
        assert metrics["masterDisplay"] == "block"
        assert metrics["master"] >= 220
        assert metrics["workspace"] >= 300
    layout = page.locator("[data-quarter-layout]").evaluate(
        "node => getComputedStyle(node).gridTemplateColumns"
    )
    assert layout
    assert page.locator("[data-detail-panel]").is_visible()


@pytest.mark.parametrize(
    "viewport", [(1199, 800), (1024, 768), (900, 900), (768, 1024), (767, 900)]
)
def test_archive_shell_is_usable_at_plan39_boundaries(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    page.locator('[data-subject-id="101"] [data-open-subject]').first.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.wait_for_timeout(300)
    layout = page.locator("[data-archive-layout]")
    metrics = layout.evaluate(
        "node => ({width: node.getBoundingClientRect().width, "
        "master: node.querySelector('.master-pane').getBoundingClientRect().width, "
        "workspace: node.querySelector('.workspace').getBoundingClientRect().width, "
        "masterDisplay: getComputedStyle(node.querySelector('.master-pane')).display})"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if viewport[0] < 768:
        assert metrics["masterDisplay"] == "block"
        assert metrics["workspace"] >= metrics["width"] - 2
    else:
        assert metrics["masterDisplay"] == "block"
        assert metrics["master"] >= 220
        assert metrics["workspace"] >= 300


@pytest.mark.parametrize(
    "viewport",
    [
        (1920, 1080),
        (1199, 800),
        (900, 900),
        (768, 1024),
        (767, 900),
        (390, 844),
        (360, 800),
    ],
)
def test_plan39_responsive_visual_acceptance(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("[data-search]").fill("no matching visual audit title")
    assert page.locator("[data-no-results]").is_visible()
    page.locator("[data-clear-all]").click()
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.wait_for_timeout(250)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    layout = page.locator("[data-quarter-layout]")
    workspace = layout.locator(".workspace")
    if viewport[0] < 768:
        assert layout.locator(".master-pane").evaluate(
            "node => getComputedStyle(node).display"
        ) == "block"
        assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    else:
        assert workspace.bounding_box()["width"] >= 300
    page.locator("[data-detail-panel] [data-detail-close]").click()
    page.locator("[data-filter-toggle]").click()
    page.wait_for_selector('[data-filter-panel]:not([hidden])')
    page.locator("[data-filter-option-search]").fill("source")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if viewport[0] < 768:
        assert layout.locator(".master-pane").evaluate(
            "node => getComputedStyle(node).display"
        ) == "none"
        assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    page.get_by_role("button", name="关闭筛选").click()


def test_mobile_controls_have_touch_targets_and_reduced_motion(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 360, "height": 800})
    page.set_default_timeout(8000)
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{site_server}/2026-07/index.html")
    page.wait_for_selector(".subject-row")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    selectors = (
        ".control-button",
        ".select-trigger",
        ".mode-switch button",
        ".search-field input",
    )
    for selector in selectors:
        box = page.locator(selector).first.bounding_box()
        if box is not None:
            assert box["height"] >= 44
    assert page.locator(".workspace-panel--scope").evaluate(
        "node => parseFloat(getComputedStyle(node).transitionDuration) <= 0.001"
    )


def test_archive_year_range_hash_and_same_origin_network(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    assert "YEAR / 2026" in page.locator("[data-archive-scope-label]").inner_text()
    page.locator('[data-scope-choice="range"]').click()
    page.locator("[data-archive-from]").fill("2026")
    page.locator("[data-archive-to]").fill("2026")
    page.locator("[data-range-apply]").click()
    page.wait_for_function(
        "document.querySelector('[data-archive-scope-label]')?.textContent.includes('RANGE')"
    )
    assert "from=2026" in page.url and "to=2026" in page.url
    page.go_back()
    page.wait_for_function(
        "document.querySelector('[data-archive-scope-label]')"
        "?.textContent.includes('YEAR / 2026')"
    )
    assert "year=2026" in page.url
    page.go_forward()
    page.wait_for_function(
        "document.querySelector('[data-archive-scope-label]')"
        "?.textContent.includes('RANGE / 2026—2026')"
    )
    assert "from=2026" in page.url and "to=2026" in page.url
    page.goto(f"{site_server}/archive/index.html?year=2026#bgm-101")
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]").inner_text()
    assert "2026-04" in detail
    assert "TV / PREMIERE" in detail
    assert all(url.startswith(site_server) for url in requests)


def _install_archive_url_state_catalog(site: Path) -> None:
    catalog_path = site / "data" / "catalog" / "2026.json"
    payload = json.loads(catalog_path.read_text("utf-8"))
    originals = payload["records"]
    tv_base = next(record for record in originals if record["media"] == "TV")
    movie_base = next(record for record in originals if record["media"] == "MOVIE")
    records = []
    for media, base, appearance, start_id in (
        ("TV", tv_base, "continuing", 9000),
        ("MOVIE", movie_base, "premiere", 9100),
    ):
        for offset in range(60):
            record = dict(base)
            record.update({
                "id": start_id + offset,
                "subject_id": start_id + offset,
                "preferred_title": f"Share State Item {offset + 1:02}",
                "original_title": f"Share State Original {offset + 1:02}",
                "media": media,
                "appearance": appearance,
                "quarter": "2026-07",
                "air_date": f"2026-07-{offset % 28 + 1:02}",
                "source": "URL_SOURCE",
                "allowed_tags": ["URL_TAG"],
                "score": float(offset % 10 + 1),
                "rating_score": float(offset % 10 + 1),
                "rating_count": offset + 1,
            })
            records.append(record)
    records.extend(
        record for record in originals
        if int(record.get("id", record.get("subject_id", 0))) == 101
    )
    payload["records"] = records
    catalog_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_archive_shared_url_restores_applied_state_scope_history_and_occurrence(
    chromium: BrowserHarness,
    preview_server: tuple[str, Path],
) -> None:
    base, site = preview_server
    _install_archive_url_state_catalog(site)
    browser = chromium.context.browser
    assert browser is not None
    context = browser.new_context(
        service_workers="block", viewport={"width": 1440, "height": 900},
    )
    page = context.new_page()
    page.emulate_media(reduced_motion="reduce")
    page.set_default_timeout(12000)
    try:
        page.goto(f"{base}/archive/index.html?year=2026")
        page.wait_for_function(
            "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
        )
        history_length = page.evaluate("history.length")
        search = page.locator("[data-search]")
        search.press_sequentially("Share State")
        assert page.evaluate("history.length") == history_length
        assert parse_qs(urlparse(page.url).query)["q"] == ["Share State"]
        composition_state = page.evaluate("""() => {
          const input = document.querySelector('[data-search]');
          input.dispatchEvent(new CompositionEvent('compositionstart', {
            bubbles: true,
          }));
          input.value = 'Share State Item';
          input.dispatchEvent(new InputEvent('input', {
            bubbles: true, inputType: 'insertCompositionText',
            data: ' Item', isComposing: true,
          }));
          return {
            query: new URLSearchParams(location.search).get('q'),
            chip: document.querySelector('[data-active-filter-type="query"]')
              ?.textContent || '',
          };
        }""")
        assert composition_state == {
            "query": "Share State",
            "chip": "搜索：Share State ×",
        }
        page.evaluate("""() => {
          const input = document.querySelector('[data-search]');
          input.dispatchEvent(new CompositionEvent('compositionend', {
            bubbles: true, data: ' Item',
          }));
          input.dispatchEvent(new InputEvent('input', {
            bubbles: true, inputType: 'insertText', data: ' Item',
            isComposing: false,
          }));
        }""")
        assert page.evaluate("history.length") == history_length
        assert parse_qs(urlparse(page.url).query)["q"] == ["Share State Item"]

        def choose_page_size(size: str) -> None:
            page.locator("[data-page-size] .select-trigger").click()
            page.locator('[data-page-size] [role="option"]', has_text=size).click()

        choose_page_size("10")
        assert parse_qs(urlparse(page.url).query)["size"] == ["10"]
        assert page.evaluate("history.length") == history_length + 1
        page.go_back()
        page.wait_for_function(
            "document.querySelector('[data-page-size] .select-trigger')"
            "?.textContent.trim() === '20'"
        )
        assert "size" not in parse_qs(urlparse(page.url).query)
        page.go_forward()
        page.wait_for_function(
            "document.querySelector('[data-page-size] .select-trigger')"
            "?.textContent.trim() === '10'"
        )
        assert parse_qs(urlparse(page.url).query)["size"] == ["10"]
        choose_page_size("20")
        assert parse_qs(urlparse(page.url).query)["size"] == ["20"]
        choose_page_size("10")

        page.locator("[data-sort-toggle]").click()
        page.get_by_role("menuitemradio", name="评分人数：少到多").click()
        page.locator("[data-filter-toggle]").click()
        page.locator(
            '[data-filter-group="sources"][data-filter-value="URL_SOURCE"]'
        ).check()
        page.locator(
            '[data-filter-group="tags"][data-filter-value="URL_TAG"]'
        ).check()
        page.get_by_label("续播").check()
        page.locator("[data-filter-close]").click()
        page.locator('[data-pager] button[aria-label="第 2 页"]') \
            .filter(has_text="02").click()

        year_url = page.url
        year_params = parse_qs(urlparse(year_url).query)
        assert year_params["year"] == ["2026"]
        assert year_params["q"] == ["Share State Item"]
        assert year_params["sort"] == ["rating-count-asc"]
        assert year_params["source"] == ["URL_SOURCE"]
        assert year_params["tag"] == ["URL_TAG"]
        assert year_params["section"] == ["continuing"]
        assert year_params["page"] == ["2"]
        assert year_params["size"] == ["10"]
        assert page.locator('[data-pager] [aria-current="page"]').inner_text() == "02"

        def assert_shared_state(target: Page, expected_scope: str) -> None:
            target.wait_for_function(
                "document.querySelectorAll('.subject-row').length === 10"
                " && document.querySelector('[data-pager] [aria-current=page]')"
                "?.textContent === '02'"
            )
            scope_text = target.locator("[data-archive-scope-label]").inner_text()
            assert expected_scope in scope_text
            assert target.locator("[data-search]").input_value() == "Share State Item"
            selected_media = target.locator('[data-media-mode="tv"]')
            assert selected_media.get_attribute("aria-selected") == "true"
            assert target.locator("[data-sort-toggle]").inner_text() == (
                "评分人数：少到多"
            )
            assert target.locator('[data-active-filter-type="sources"]').count() == 1
            assert target.locator('[data-active-filter-type="tags"]').count() == 1
            assert target.locator('[data-active-filter-type="sections"]').count() == 1
            page_size = target.locator("[data-page-size] .select-trigger")
            assert page_size.inner_text() == "10"
            current_page = target.locator('[data-pager] [aria-current="page"]')
            assert current_page.inner_text() == "02"
            assert target.locator(".subject-row").count() == 10

        fresh_context = browser.new_context(
            service_workers="block", viewport={"width": 1440, "height": 900},
        )
        fresh_page = fresh_context.new_page()
        fresh_page.emulate_media(reduced_motion="reduce")
        fresh_page.set_default_timeout(12000)
        fresh_page.goto(year_url)
        assert_shared_state(fresh_page, "YEAR / 2026")
        fresh_context.close()

        page.locator('[data-scope-choice="range"]').click()
        page.locator("[data-archive-from]").fill("2026")
        page.locator("[data-archive-to]").fill("2026")
        page.locator("[data-range-apply]").click()
        page.wait_for_function(
            "document.querySelector('[data-archive-scope-label]')"
            "?.textContent.includes('RANGE / 2026—2026')"
        )
        range_url = page.url
        assert_shared_state(page, "RANGE / 2026—2026")
        page.reload()
        assert_shared_state(page, "RANGE / 2026—2026")
        page.go_back()
        assert_shared_state(page, "YEAR / 2026")
        page.go_forward()
        assert_shared_state(page, "RANGE / 2026—2026")

        fresh_context = browser.new_context(
            service_workers="block", viewport={"width": 1440, "height": 900},
        )
        fresh_page = fresh_context.new_page()
        fresh_page.emulate_media(reduced_motion="reduce")
        fresh_page.set_default_timeout(12000)
        fresh_page.goto(range_url)
        assert_shared_state(fresh_page, "RANGE / 2026—2026")
        fresh_context.close()

        page.goto(f"{base}/archive/index.html?year=2026#bgm-101")
        page.wait_for_selector('[data-detail-panel]:not([hidden])')
        assert "2026-04" in page.locator("[data-detail-panel]").inner_text()
        page.goto(
            f"{base}/archive/index.html?year=2026&occ=2026-07.continuing#bgm-101"
        )
        page.wait_for_selector('[data-detail-panel]:not([hidden])')
        detail = page.locator("[data-detail-panel]").inner_text()
        assert "2026-07" in detail and "续播" in detail
        assert parse_qs(urlparse(page.url).query)["occ"] == ["2026-07.continuing"]

        page.goto(f"{base}/archive/index.html?year=2026&media=movie")
        page.wait_for_function(
            "document.querySelector('[data-results-summary]')"
            "?.textContent.includes('appearance')"
        )
        selected_movie = page.locator('[data-media-mode="movie"]')
        assert selected_movie.get_attribute("aria-selected") == "true"
        assert "60 / 60" in page.locator("[data-results-summary]").inner_text()
        assert page.locator(".subject-row").count() == 10

        too_long_query = "x" * 161
        page.goto(
            f"{base}/archive/index.html?year=2026&year=2026&q={too_long_query}"
            "&media=movie&media=movie&sort=%3Cscript%3E&page=99999999&size=999"
            "&source=%3Cscript%3E&source=URL_SOURCE&source=URL_SOURCE"
            "&tag=URL_TAG&tag=URL_TAG"
        )
        page.wait_for_function(
            "document.querySelector('[data-no-results]')?.hidden === false"
        )
        bounded_params = parse_qs(urlparse(page.url).query)
        assert len(bounded_params["q"][0]) == 160
        assert bounded_params["source"] == ["URL_SOURCE"]
        assert bounded_params["tag"] == ["URL_TAG"]
        assert "media" not in bounded_params
        assert "sort" not in bounded_params
        assert "page" not in bounded_params
        assert "size" not in bounded_params
        selected_tv = page.locator('[data-media-mode="tv"]')
        assert selected_tv.get_attribute("aria-selected") == "true"
        assert page.locator("#url-injected").count() == 0

        too_many_sources = "&".join(["source=URL_SOURCE"] * 65)
        page.goto(f"{base}/archive/index.html?year=2026&{too_many_sources}&tag=URL_TAG")
        page.wait_for_function(
            "document.querySelector('[data-results-summary]')"
            "?.textContent.includes('appearance')"
        )
        bounded_filter_params = parse_qs(urlparse(page.url).query)
        assert "source" not in bounded_filter_params
        assert "tag" not in bounded_filter_params

        page.goto(
            f"{base}/archive/index.html?year=2026&q=Share%20State%20Item"
            "&page=99&size=10"
        )
        page.wait_for_function(
            "document.querySelector('[data-pager] [aria-current=page]')"
            "?.textContent === '06'"
        )
        assert page.locator(".subject-row").count() == 10
        clamped_params = parse_qs(urlparse(page.url).query)
        assert clamped_params["q"] == ["Share State Item"]
        assert clamped_params["page"] == ["6"]
    finally:
        context.close()


def test_archive_mobile_filter_draft_back_keeps_shared_url_applied_state(
    preview_browser: Browser,
    preview_server: tuple[str, Path],
) -> None:
    base, site = preview_server
    _install_archive_url_state_catalog(site)
    context = preview_browser.new_context(
        service_workers="block", viewport={"width": 390, "height": 844},
    )
    page = context.new_page()
    page.emulate_media(reduced_motion="reduce")
    page.set_default_timeout(12000)
    try:
        page.goto(f"{base}/archive/index.html?year=2026")
        page.wait_for_function(
            "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
        )
        page.locator("[data-search]").fill("Share State Item")
        page.locator('[data-scope-choice="range"]').click()
        page.locator("[data-archive-from]").fill("2026")
        page.locator("[data-archive-to]").fill("2026")
        page.locator("[data-range-apply]").click()
        page.wait_for_function(
            "document.querySelector('[data-archive-scope-label]')"
            "?.textContent.includes('RANGE / 2026—2026')"
        )
        range_url = page.url

        page.locator("[data-filter-toggle]").click()
        panel = page.locator("[data-filter-panel]")
        panel.locator(
            '[data-filter-group="sources"][data-filter-value="URL_SOURCE"]'
        ).check()
        panel.locator(
            '[data-filter-group="tags"][data-filter-value="URL_TAG"]'
        ).check()
        assert page.url == range_url
        assert "source=" not in page.url and "tag=" not in page.url

        page.go_back()
        page.wait_for_function(
            "document.querySelector('[data-archive-scope-label]')"
            "?.textContent.includes('YEAR / 2026')"
        )
        assert page.locator("[data-filter-panel]").is_hidden()
        assert page.locator("[data-search]").input_value() == "Share State Item"
        assert "source=" not in page.url and "tag=" not in page.url
        assert parse_qs(urlparse(page.url).query)["q"] == ["Share State Item"]

        page.go_forward()
        page.wait_for_function(
            "document.querySelector('[data-archive-scope-label]')"
            "?.textContent.includes('RANGE / 2026—2026')"
        )
        assert page.locator("[data-filter-panel]").is_hidden()
        assert page.locator("[data-search]").input_value() == "Share State Item"
        page.locator("[data-filter-toggle]").click()
        panel = page.locator("[data-filter-panel]")
        panel.locator(
            '[data-filter-group="sources"][data-filter-value="URL_SOURCE"]'
        ).check()
        panel.locator(
            '[data-filter-group="tags"][data-filter-value="URL_TAG"]'
        ).check()
        panel.locator('[data-filter-group="sections"][data-filter-value="continuing"]').check()
        panel.locator(".filter-apply-mobile").click()
        page.wait_for_function("new URLSearchParams(location.search).has('source')")
        params = parse_qs(urlparse(page.url).query)
        assert params["from"] == ["2026"] and params["to"] == ["2026"]
        assert params["q"] == ["Share State Item"]
        assert params["source"] == ["URL_SOURCE"]
        assert params["tag"] == ["URL_TAG"]
        assert params["section"] == ["continuing"]
    finally:
        context.close()


def test_archive_year_listbox_updates_scope_without_native_select(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    trigger = page.locator("[data-archive-year-select] .select-trigger")
    trigger.press("Enter")
    assert page.locator('[data-archive-year-select] [role="listbox"]').is_visible()
    trigger.press("Escape")
    assert page.locator('[data-archive-year-select] [role="listbox"]').is_hidden()
    assert trigger.evaluate("node => node === document.activeElement")
    trigger.click()
    page.locator('[data-archive-year-select] [role="option"]', has_text="2026").click()
    page.wait_for_function(
        "document.querySelector('[data-archive-scope-label]')"
        "?.textContent.includes('YEAR / 2026')"
    )
    assert "year=2026" in page.url


@pytest.mark.parametrize(
    ("engine", "viewport"),
    [
        ("chromium", (390, 844)),
        ("chromium", (360, 800)),
        ("chromium", (1440, 900)),
        ("webkit", (390, 844)),
    ],
)
def test_archive_pending_shell_stays_stable_through_index_and_catalog_waits(
    unified_site: Path,
    engine: str,
    viewport: tuple[int, int],
) -> None:
    index_gate = _catalog_gate()
    catalog_gate = _catalog_gate()
    with _archive_catalog_server(
        unified_site,
        {"archive-index": [index_gate], "catalog:2026": [catalog_gate]},
    ) as server:
        with sync_playwright() as runner:
            browser = getattr(runner, engine).launch()
            context = browser.new_context(
                service_workers="block",
                viewport={"width": viewport[0], "height": viewport[1]},
            )
            page = context.new_page()
            page.emulate_media(reduced_motion="reduce")
            page.set_default_timeout(10000)
            try:
                page.goto(f"{server}/archive/index.html", wait_until="domcontentloaded")
                index_requested = index_gate["requested"]
                assert isinstance(index_requested, threading.Event)
                assert index_requested.wait(timeout=3)
                initial = page.evaluate("""() => {
                  const box = selector => {
                    const node = document.querySelector(selector);
                    if (!node || node.hidden) return null;
                    const rect = node.getBoundingClientRect();
                    return {y: rect.y, height: rect.height};
                  };
                  return {
                    title: box('.archive-intro h1'),
                    tabs: box('.scope-tabs'),
                    selector: box('.archive-selector'),
                    toolbar: box('.archive-browser__toolbar'),
                    controls: box('.browser-controls'),
                    summary: box('[data-results-summary]'),
                    pending: box('[data-archive-pending]'),
                    scrollWidth: document.documentElement.scrollWidth,
                    viewportWidth: innerWidth,
                  };
                }""")
                assert page.locator('[data-scope-choice="year"]').get_attribute(
                    "aria-selected"
                ) == "true"
                assert page.locator("[data-archive-browser]").is_visible()
                assert page.locator(".master-pane").evaluate("node => node.inert")
                assert page.locator("[data-archive-browser]").get_attribute(
                    "aria-busy"
                ) == "true"
                assert "正在读取档案索引" in page.locator(
                    "[data-results-summary]"
                ).inner_text()
                assert page.locator(".subject-row").count() == 0
                assert page.locator("[data-archive-pending]").evaluate(
                    "node => node.querySelectorAll("
                    "'article, button, a, [data-record-key]').length === 0"
                )

                release_index = index_gate["release"]
                assert isinstance(release_index, threading.Event)
                release_index.set()
                catalog_requested = catalog_gate["requested"]
                assert isinstance(catalog_requested, threading.Event)
                assert catalog_requested.wait(timeout=5)
                page.wait_for_function(
                    "document.querySelector('[data-archive-app][data-page=archive]')"
                    "?.dataset.scopePending === 'true'"
                )
                waiting = page.evaluate("""() => {
                  const box = selector => {
                    const rect = document.querySelector(selector)
                      .getBoundingClientRect();
                    return {y: rect.y, height: rect.height};
                  };
                  return {
                    toolbar: box('.archive-browser__toolbar'),
                    controls: box('.browser-controls'),
                    pending: box('[data-archive-pending]'),
                    scrollWidth: document.documentElement.scrollWidth,
                  };
                }""")
                for selector in ("toolbar", "controls"):
                    assert abs(waiting[selector]["y"] - initial[selector]["y"]) <= 1
                    assert (
                        abs(waiting[selector]["height"] - initial[selector]["height"])
                        <= 1
                    )
                assert page.locator(".subject-row").count() == 0
                assert "正在读取范围目录" in page.locator(
                    "[data-results-summary]"
                ).inner_text()

                release_catalog = catalog_gate["release"]
                assert isinstance(release_catalog, threading.Event)
                release_catalog.set()
                _wait_archive_scope(page, "YEAR / 2026")
                final = page.evaluate("""() => {
                  const box = selector => {
                    const rect = document.querySelector(selector)
                      .getBoundingClientRect();
                    return {y: rect.y, height: rect.height};
                  };
                  return {
                    toolbar: box('.archive-browser__toolbar'),
                    controls: box('.browser-controls'),
                    firstRow: box('.subject-row'),
                    scrollWidth: document.documentElement.scrollWidth,
                  };
                }""")
                for selector in ("toolbar", "controls"):
                    assert abs(final[selector]["y"] - initial[selector]["y"]) <= 1
                    assert (
                        abs(final[selector]["height"] - initial[selector]["height"])
                        <= 1
                    )
                assert final["scrollWidth"] <= viewport[0]
                assert page.locator("[data-archive-pending]").is_hidden()
                assert page.locator("[data-archive-browser]").get_attribute(
                    "aria-busy"
                ) == "false"
                assert page.locator(".master-pane").evaluate("node => !node.inert")
                assert page.locator(".subject-row").evaluate_all(
                    "nodes => nodes.map(node => node.dataset.recordKey)"
                ) == ["101@2026-04@premiere", "101@2026-07@continuing"]
                assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
            finally:
                context.close()
                browser.close()


@pytest.mark.parametrize("kind", ["quarter", "range"])
def test_archive_scope_choice_before_index_preserves_latest_tab(
    unified_site: Path,
    kind: str,
) -> None:
    index_gate = _catalog_gate()
    catalog_gate = _catalog_gate()
    with _archive_catalog_server(
        unified_site,
        {"archive-index": [index_gate], "catalog:2026": [catalog_gate]},
    ) as server:
        with sync_playwright() as runner:
            browser = runner.chromium.launch()
            context = browser.new_context(
                service_workers="block", viewport={"width": 390, "height": 844}
            )
            page = context.new_page()
            page.set_default_timeout(10000)
            try:
                page.goto(f"{server}/archive/index.html", wait_until="domcontentloaded")
                requested = index_gate["requested"]
                assert isinstance(requested, threading.Event)
                assert requested.wait(timeout=3)
                page.locator(f'[data-scope-choice="{kind}"]').click()
                assert page.locator(f'[data-scope-choice="{kind}"]').get_attribute(
                    "aria-selected"
                ) == "true"
                if kind == "quarter":
                    assert page.locator("[data-archive-browser]").is_hidden()
                else:
                    assert page.locator("[data-archive-browser]").is_visible()
                    assert page.locator("[data-archive-range-selector]").is_visible()
                release = index_gate["release"]
                assert isinstance(release, threading.Event)
                release.set()
                finished = index_gate["finished"]
                assert isinstance(finished, threading.Event)
                assert finished.wait(timeout=3)
                if kind == "quarter":
                    page.wait_for_selector(".archive-quarter-grid")
                else:
                    page.wait_for_function(
                        "document.querySelector('[data-archive-app][data-page=archive]')"
                        "?.getAttribute('aria-busy') === 'false'"
                    )
                assert not catalog_gate["requested"].wait(timeout=0.35)
                assert page.locator(f'[data-scope-choice="{kind}"]').get_attribute(
                    "aria-selected"
                ) == "true"
                if kind == "quarter":
                    assert page.locator("[data-archive-browser]").is_hidden()
                else:
                    assert page.locator("[data-archive-browser]").is_visible()
                    assert page.locator("[data-archive-range-selector]").is_visible()
                    assert page.locator("[data-archive-from]").input_value() == "2026"
                    assert page.locator("[data-archive-to]").input_value() == "2026"
                    assert page.locator("[data-archive-from]").is_enabled()
                    assert page.locator(".master-pane").evaluate("node => node.inert")
                assert page.locator(
                    '[data-archive-app][data-page="archive"]'
                ).get_attribute("aria-busy") == "false"
            finally:
                context.close()
                browser.close()


def test_archive_quarter_choice_cancels_a_late_initial_catalog(
    unified_site: Path,
) -> None:
    catalog_gate = _catalog_gate()
    with _archive_catalog_server(
        unified_site, {"catalog:2026": [catalog_gate]}
    ) as server:
        with sync_playwright() as runner:
            browser = runner.chromium.launch()
            context = browser.new_context(
                service_workers="block", viewport={"width": 390, "height": 844}
            )
            page = context.new_page()
            page.set_default_timeout(10000)
            try:
                page.goto(f"{server}/archive/index.html", wait_until="domcontentloaded")
                requested = catalog_gate["requested"]
                assert isinstance(requested, threading.Event)
                assert requested.wait(timeout=5)
                page.wait_for_function(
                    "document.querySelector('[data-archive-app][data-page=archive]')"
                    "?.dataset.scopePending === 'true'"
                )
                page.locator('[data-scope-choice="quarter"]').click()
                assert page.locator("[data-archive-browser]").is_hidden()
                assert page.locator(
                    '[data-archive-app][data-page="archive"]'
                ).get_attribute("aria-busy") == "false"
                release = catalog_gate["release"]
                assert isinstance(release, threading.Event)
                release.set()
                finished = catalog_gate["finished"]
                assert isinstance(finished, threading.Event)
                assert finished.wait(timeout=3)
                page.wait_for_timeout(100)
                assert page.locator('[data-scope-choice="quarter"]').get_attribute(
                    "aria-selected"
                ) == "true"
                assert page.locator("[data-archive-browser]").is_hidden()
                assert page.locator(".subject-row").count() == 0
            finally:
                context.close()
                browser.close()


@pytest.mark.parametrize("outcome", ["index", "catalog", "empty"])
def test_archive_initial_load_errors_and_empty_state_clear_busy(
    unified_site: Path,
    outcome: str,
) -> None:
    if outcome == "index":
        gates = {"archive-index": [_catalog_gate(status=503)]}
    elif outcome == "catalog":
        gates = {"catalog:2026": [_catalog_gate(status=503)]}
    else:
        catalog_path = unified_site / "data" / "catalog" / "2026.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["records"] = []
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        gates = {}
    for resources in gates.values():
        release = resources[0]["release"]
        assert isinstance(release, threading.Event)
        release.set()
    with _archive_catalog_server(unified_site, gates) as server:
        with sync_playwright() as runner:
            browser = runner.chromium.launch()
            context = browser.new_context(
                service_workers="block", viewport={"width": 390, "height": 844}
            )
            page = context.new_page()
            page.set_default_timeout(10000)
            try:
                page.goto(f"{server}/archive/index.html", wait_until="domcontentloaded")
                page.wait_for_function(
                    "document.querySelector('[data-archive-app][data-page=archive]')"
                    "?.getAttribute('aria-busy') === 'false'"
                )
                assert page.locator("[data-archive-pending]").is_hidden()
                assert page.locator(".subject-row").count() == 0
                if outcome == "index":
                    assert "DATA UNAVAILABLE" in page.locator("body").inner_text()
                    assert page.locator("[data-archive-browser]").is_hidden()
                    assert page.locator("[data-archive-quarter-selector]").is_visible()
                    page.locator('[data-scope-choice="year"]').click()
                    assert page.locator("[data-archive-quarter-selector]").is_visible()
                    assert "DATA UNAVAILABLE" in page.locator(
                        "[data-archive-quarter-selector]"
                    ).inner_text()
                elif outcome == "catalog":
                    assert "DATA UNAVAILABLE" in page.locator("body").inner_text()
                    assert page.locator("[data-archive-browser]").is_visible()
                    assert page.locator("[data-scope-panel]").inner_text().count(
                        "DATA UNAVAILABLE"
                    ) == 1
                else:
                    assert page.locator("[data-archive-browser]").is_visible()
                    assert page.locator("[data-no-results]").is_visible()
                    assert "0 / 0" in page.locator(
                        "[data-results-summary]"
                    ).inner_text()
                    assert page.locator(".master-pane").evaluate("node => !node.inert")
            finally:
                context.close()
                browser.close()


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844)])
def test_archive_latest_scope_success_wins_when_older_request_finishes_last(
    chromium: BrowserContext,
    unified_site: Path,
    viewport: tuple[int, int],
) -> None:
    old_request = _catalog_gate()
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    with _archive_catalog_server(
        unified_site,
        {"catalog:2025": [old_request]},
    ) as server:
        _open_two_year_archive(page, unified_site, server, viewport)
        _choose_archive_year(page, 2025)
        old_requested = old_request["requested"]
        assert isinstance(old_requested, threading.Event)
        assert old_requested.wait(timeout=3)

        _choose_archive_year(page, 2026)
        _wait_archive_scope(page, "YEAR / 2026")
        assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
        assert page.locator('[data-subject-id="101"]').count() == 1
        assert page.locator('[data-subject-id="505"]').count() == 0

        with page.expect_response(
            lambda response: "catalog/2025.json" in response.url
        ) as old_response:
            old_release = old_request["release"]
            assert isinstance(old_release, threading.Event)
            old_release.set()
        assert old_response.value.status == 200
        assert "YEAR / 2026" in page.locator("[data-archive-scope-label]").inner_text()
        assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
        assert page.locator('[data-subject-id="101"]').count() == 1
        assert page.locator('[data-subject-id="505"]').count() == 0
        assert "DATA UNAVAILABLE" not in page.locator("[data-scope-panel]").inner_text()
    page.close()


def test_archive_stale_failure_cannot_poison_latest_scope(
    chromium: BrowserContext,
    unified_site: Path,
) -> None:
    old_failure = _catalog_gate(status=503)
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    with _archive_catalog_server(
        unified_site,
        {"catalog:2025": [old_failure]},
    ) as server:
        _open_two_year_archive(page, unified_site, server)
        _choose_archive_year(page, 2025)
        requested = old_failure["requested"]
        assert isinstance(requested, threading.Event)
        assert requested.wait(timeout=3)

        _choose_archive_year(page, 2026)
        _wait_archive_scope(page, "YEAR / 2026")
        release = old_failure["release"]
        assert isinstance(release, threading.Event)
        release.set()
        finished = old_failure["finished"]
        assert isinstance(finished, threading.Event)
        assert finished.wait(timeout=3)
        page.wait_for_function(
            "window.__catalogResponses?.some(response => "
            "response.url.includes('/data/catalog/2025.json') "
            "&& response.status === 503)"
        )
        assert "YEAR / 2026" in page.locator(
            "[data-archive-scope-label]"
        ).inner_text()
        assert page.locator('[data-subject-id="101"]').count() == 1
        assert page.locator('[data-subject-id="505"]').count() == 0
        assert "DATA UNAVAILABLE" not in page.locator("[data-scope-panel]").inner_text()
        page.locator("[data-search]").fill("no matching fixture")
        page.wait_for_function(
            "document.querySelector('[data-no-results]')?.hidden === false"
        )
        assert "0 / 1" in page.locator("[data-results-summary]").inner_text()
        page.locator("[data-search]").fill("")
        page.locator('[data-subject-id="101"] [data-open-subject]').click()
        page.wait_for_function(
            "document.querySelector('[data-detail-panel]')"
            "?.textContent.includes('Fixture 2026')"
        )
        assert "#bgm-101" in page.url
    page.close()


def test_archive_a_to_b_to_a_uses_latest_scope_intent(
    chromium: BrowserContext,
    unified_site: Path,
) -> None:
    first_year_request = _catalog_gate()
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    with _archive_catalog_server(
        unified_site,
        {"catalog:2025": [first_year_request]},
    ) as server:
        _open_two_year_archive(page, unified_site, server)
        _choose_archive_year(page, 2025)
        requested = first_year_request["requested"]
        assert isinstance(requested, threading.Event)
        assert requested.wait(timeout=3)

        _choose_archive_year(page, 2026)
        _wait_archive_scope(page, "YEAR / 2026")
        _choose_archive_year(page, 2025)
        assert "year=2025" in page.url
        assert "YEAR / 2025" in page.locator(
            "[data-archive-scope-label]"
        ).inner_text()

        release = first_year_request["release"]
        assert isinstance(release, threading.Event)
        release.set()
        _wait_archive_scope(page, "YEAR / 2025")
        assert page.locator('[data-subject-id="505"]').count() == 1
        assert page.locator('[data-subject-id="101"]').count() == 0
        assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
        assert "DATA UNAVAILABLE" not in page.locator("[data-scope-panel]").inner_text()
    page.close()


def test_archive_current_scope_failure_recovers_on_next_valid_choice(
    chromium: BrowserContext,
    unified_site: Path,
) -> None:
    failed_request = _catalog_gate(status=503)
    release = failed_request["release"]
    assert isinstance(release, threading.Event)
    release.set()
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    with _archive_catalog_server(
        unified_site,
        {"catalog:2025": [failed_request]},
    ) as server:
        _open_two_year_archive(page, unified_site, server)
        _choose_archive_year(page, 2025)
        page.wait_for_function(
            "document.querySelector('[data-scope-panel]')"
            "?.textContent.includes('DATA UNAVAILABLE')"
        )
        assert "YEAR / 2025" in page.locator("[data-archive-scope-label]").inner_text()
        assert page.locator(".master-pane").evaluate("node => node.inert")

        _choose_archive_year(page, 2026)
        _wait_archive_scope(page, "YEAR / 2026")
        page.locator("[data-search]").fill("Fixture 2026")
        assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
        page.locator('[data-subject-id="101"] [data-open-subject]').click()
        page.wait_for_function(
            "document.querySelector('[data-detail-panel]')"
            "?.textContent.includes('Fixture 2026')"
        )
    page.close()


def test_archive_popstate_cancels_pending_range_and_preserves_history(
    chromium: BrowserContext,
    unified_site: Path,
) -> None:
    pending_range_request = _catalog_gate()
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    with _archive_catalog_server(
        unified_site,
        {"catalog:2025": [pending_range_request]},
    ) as server:
        _open_two_year_archive(page, unified_site, server)
        initial_history_length = page.evaluate("history.length")
        page.locator('[data-scope-choice="range"]').click()
        page.locator("[data-archive-from]").fill("2025")
        page.locator("[data-archive-to]").fill("2026")
        page.locator("[data-range-apply]").click()
        assert "from=2025" in page.url and "to=2026" in page.url, (
            page.url,
            page.locator("[data-archive-scope-label]").inner_text(),
            page.locator("[data-scope-panel]").inner_text(),
        )
        assert page.evaluate("history.length") == initial_history_length + 1
        range_requested = pending_range_request["requested"]
        assert isinstance(range_requested, threading.Event)
        assert range_requested.wait(timeout=3)

        page.go_back()
        _wait_archive_scope(page, "YEAR / 2026")
        assert page.url.endswith("?year=2026")
        assert page.evaluate("history.length") == initial_history_length + 1
        assert "1 / 1" in page.locator("[data-results-summary]").inner_text()

        page.go_forward()
        assert "from=2025" in page.url and "to=2026" in page.url
        assert page.evaluate("history.length") == initial_history_length + 1

        range_release = pending_range_request["release"]
        assert isinstance(range_release, threading.Event)
        range_release.set()
        _wait_archive_scope(page, "RANGE / 2025—2026")
        assert page.evaluate("history.length") == initial_history_length + 1
        assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
        assert page.locator('[data-subject-id="101"]').count() == 1
        assert page.locator('[data-subject-id="505"]').count() == 1

        assert "RANGE / 2025—2026" in page.locator(
            "[data-archive-scope-label]"
        ).inner_text()
        assert "DATA UNAVAILABLE" not in page.locator("[data-scope-panel]").inner_text()
        assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    page.close()


def test_mobile_archive_filter_back_restores_real_scope_history_and_discards_draft(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/archive/index.html?year=2026")
    _wait_archive_scope(page, "YEAR / 2026")
    root = page.locator('[data-page="archive"][data-archive-app]')
    initial_history_length = page.evaluate("history.length")

    root.locator('[data-scope-choice="range"]').click()
    page.locator("[data-archive-from]").fill("2026")
    page.locator("[data-archive-to]").fill("2026")
    page.locator("[data-range-apply]").click()
    _wait_archive_scope(page, "RANGE / 2026—2026")
    range_url = page.url
    assert "from=2026" in range_url and "to=2026" in range_url
    assert page.evaluate("history.length") == initial_history_length + 1

    toggle = root.locator("[data-filter-toggle]")
    toggle.click()
    draft_checkbox = page.get_by_label("续播")
    assert not draft_checkbox.is_checked()
    draft_checkbox.check()

    page.evaluate("""() => {
      const calls = window.__plan57ScopeCalls = {
        pushState: 0, replaceState: 0, back: 0, focus: 0, scrollTo: 0,
      };
      const originals = window.__plan57ScopeOriginals = {
        history: {}, focus: HTMLElement.prototype.focus, scrollTo: window.scrollTo,
      };
      for (const method of ['pushState', 'replaceState', 'back']) {
        const original = History.prototype[method];
        originals.history[method] = original;
        History.prototype[method] = function(...args) {
          calls[method] += 1;
          return original.apply(this, args);
        };
      }
      HTMLElement.prototype.focus = function(...args) {
        calls.focus += 1;
        return originals.focus.apply(this, args);
      };
      window.scrollTo = (...args) => {
        calls.scrollTo += 1;
        return originals.scrollTo.apply(window, args);
      };
    }""")
    page.go_back()
    _wait_archive_scope(page, "YEAR / 2026")
    assert page.url.endswith("?year=2026")
    assert page.evaluate("history.length") == initial_history_length + 1
    assert root.locator("[data-filter-panel]").is_hidden()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("window.__plan57ScopeCalls") == {
        "pushState": 0,
        "replaceState": 0,
        "back": 0,
        "focus": 0,
        "scrollTo": 0,
    }
    page.evaluate("""() => {
      const originals = window.__plan57ScopeOriginals;
      for (const [method, original] of Object.entries(originals.history)) {
        History.prototype[method] = original;
      }
      HTMLElement.prototype.focus = originals.focus;
      window.scrollTo = originals.scrollTo;
    }""")

    page.go_forward()
    _wait_archive_scope(page, "RANGE / 2026—2026")
    assert page.url == range_url
    assert page.evaluate("history.length") == initial_history_length + 1
    assert root.locator("[data-filter-panel]").is_hidden()
    assert toggle.get_attribute("aria-expanded") == "false"
    toggle.click()
    assert not page.get_by_label("续播").is_checked()
    page.get_by_role("button", name="关闭筛选").click()


def test_archive_late_detail_cannot_replace_new_scope_selection(
    chromium: BrowserContext,
    unified_site: Path,
) -> None:
    old_detail = _catalog_gate()
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    with _archive_catalog_server(
        unified_site,
        {"quarter:2025-04": [old_detail]},
    ) as server:
        _open_two_year_archive(page, unified_site, server)
        _choose_archive_year(page, 2025)
        _wait_archive_scope(page, "YEAR / 2025")
        page.locator('[data-subject-id="505"] [data-open-subject]').click()
        detail_requested = old_detail["requested"]
        assert isinstance(detail_requested, threading.Event)
        assert detail_requested.wait(timeout=3)
        assert "正在读取季度详情" in page.locator("[data-detail-panel]").inner_text()

        _choose_archive_year(page, 2026)
        _wait_archive_scope(page, "YEAR / 2026")
        assert page.locator('[data-subject-id="101"]').count() == 1
        assert page.locator("[data-detail-panel]").is_hidden()
        with page.expect_response(
            lambda response: "quarters/2025-04.json" in response.url
        ) as detail_response:
            detail_release = old_detail["release"]
            assert isinstance(detail_release, threading.Event)
            detail_release.set()
        assert detail_response.value.status == 200
        assert page.locator("[data-detail-panel]").is_hidden()
        assert page.url.endswith("?year=2026")
        assert "YEAR / 2026" in page.locator("[data-archive-scope-label]").inner_text()
    page.close()


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_scope_detail_and_filter_use_single_pane_workspace(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    root = page.locator('[data-page="quarter"][data-archive-app]')
    layout = page.locator("[data-quarter-layout]")
    master = page.locator("[data-quarter-layout] .master-pane")
    workspace = page.locator("[data-quarter-layout] .workspace")

    assert root.get_attribute("data-workspace-mode") == "scope"
    assert workspace.evaluate("node => getComputedStyle(node).display") == "none"
    assert master.bounding_box()["width"] >= layout.bounding_box()["width"] * 0.95

    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert root.get_attribute("data-workspace-mode") == "detail"
    assert master.evaluate("node => getComputedStyle(node).display") == "block"
    assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    topbar = page.locator("[data-detail-panel] .detail-topbar")
    assert topbar.get_attribute("aria-label") == "详情导航"
    assert topbar.locator(".detail-close__icon").inner_text() == "←"
    assert topbar.locator(".detail-close__back").evaluate(
        "node => getComputedStyle(node).display"
    ) == "none"
    assert topbar.locator(".detail-topbar__context").inner_text().startswith("TV")

    page.get_by_role("button", name="返回结果").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    page.locator("[data-filter-toggle]").click()
    page.wait_for_selector('[data-filter-panel]:not([hidden])')
    assert root.get_attribute("data-workspace-mode") == "filter"
    assert master.evaluate("node => getComputedStyle(node).display") == "none"
    assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    assert page.locator("[data-filter-workspace-summary]").is_visible()
    assert page.locator("[data-filter-workspace-clear]").is_disabled()
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "scope"

    page.locator("[data-filter-toggle]").click()
    assert page.get_by_label("首播").count() == 0
    assert page.get_by_label("续播").count() == 0
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    assert page.url.endswith("/2026-07/index.html")


def test_mobile_filter_workspace_keeps_facets_active_filters_and_scroll(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    base = payload["tv"]["continuing"][0]
    payload["tv"]["continuing"] = [
        _facet_record(base, 101, "shared-source", "shared-tag"),
        _facet_record(base, 301, "tv-only-source", "tv-only-tag"),
    ]
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (390, 844))
    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '2200px'; node.style.paddingTop = '900px'; }"
    )
    toggle = page.locator("[data-filter-toggle]")
    toggle.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    assert saved_scroll > 0
    toggle.click()
    root = page.locator('main[data-page="quarter"][data-archive-app]')
    layout = page.locator("[data-quarter-layout]")
    workspace = page.locator("[data-quarter-layout] .workspace")
    assert root.get_attribute("data-workspace-mode") == "filter"
    assert page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => getComputedStyle(node).display"
    ) == "none"
    assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    option_search = page.locator("[data-filter-option-search]")
    option_search.fill("tv-only")
    page.get_by_label("tv-only-source").check()
    active = page.locator("[data-filter-workspace-active]")
    assert active.is_visible()
    assert "来源" in active.inner_text()
    assert not page.locator(
        "[data-filter-workspace-summary]"
    ).inner_text().endswith("2 部")
    page.locator("[data-filter-workspace-clear]").click()
    assert page.locator("[data-filter-workspace-active]").is_hidden()
    assert page.locator("[data-filter-workspace-summary]").inner_text().endswith("2 部")
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )


def test_mobile_filter_draft_requires_apply_and_cancel_preserves_results(
    chromium: BrowserContext,
    site_server_mixed: str,
    unified_site_mixed: Path,
) -> None:
    payload = json.loads(
        (unified_site_mixed / "data" / "quarters" / "2026-07.json").read_text(
            "utf-8"
        )
    )
    payload["tv"]["continuing"] = [
        _facet_record(
            payload["tv"]["continuing"][0], 101, "shared-source", "shared-tag"
        )
    ]
    payload["tv"]["premiere"] = [
        _facet_record(
            payload["tv"]["premiere"][0], 303, "tv-only-source", "tv-only-tag"
        )
    ]
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server_mixed, (390, 844))
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 2

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("tv-only-source").check()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 2
    assert page.locator("[data-filter-workspace-summary]").inner_text().endswith("1 部")
    page.get_by_role("button", name="关闭筛选").click()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("tv-only-source").check()
    page.get_by_role("button", name="返回结果").click()
    assert "1 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator(
        '[data-list-section="tv"] .subject-row:not([hidden])'
    ).count() == 1


def test_mobile_detail_is_full_width_and_restores_list_scroll(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (390, 844))
    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '2600px'; "
        "node.querySelector(\"[data-subject-id='101']\")?.style.setProperty("
        "'margin-top', '1200px'); }"
    )
    target = page.locator('[data-subject-id="101"] [data-open-subject]')
    target.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    assert saved_scroll > 0
    target.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    layout = page.locator("[data-quarter-layout]")
    workspace = page.locator("[data-quarter-layout] .workspace")
    assert page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => getComputedStyle(node).display"
    ) == "block"
    assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    assert page.get_by_role("button", name="返回结果").is_visible()
    page.get_by_role("button", name="返回结果").click()
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )


def test_mobile_detail_is_fixed_keeps_list_identity_and_unifies_history(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 393, "height": 852})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (393, 852))
    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.dataset.identityMarker = 'kept'; "
        "node.style.minHeight = '2400px'; }"
    )
    target = page.locator('[data-subject-id="101"] [data-open-subject]')
    target.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    target.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    metrics = page.locator("[data-detail-panel]").evaluate(
        "node => ({position: getComputedStyle(node.parentElement).position, "
        "height: node.getBoundingClientRect().height, viewport: innerHeight, "
        "safeTop: getComputedStyle(document.documentElement)"
        ".getPropertyValue('--safe-area-top'), "
        "meta: document.querySelector('meta[name=viewport]').content, "
        "listMarker: document.querySelector('.master-pane').dataset.identityMarker, "
        "bodyScroll: scrollY})"
    )
    assert metrics["position"] == "fixed"
    assert metrics["height"] >= metrics["viewport"] - 2
    assert metrics["listMarker"] == "kept"
    assert metrics["bodyScroll"] == saved_scroll
    assert metrics["bodyScroll"] == page.evaluate("window.scrollY")
    assert (
        page.evaluate("getComputedStyle(document.documentElement).overflow")
        == "hidden"
    )
    assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"
    assert page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => ({visibility: getComputedStyle(node).visibility, "
        "pointerEvents: getComputedStyle(node).pointerEvents})"
    ) == {"visibility": "visible", "pointerEvents": "none"}
    assert metrics["meta"].endswith("viewport-fit=cover")
    assert metrics["safeTop"]

    page.go_back()
    page.wait_for_function(
        "document.querySelector('[data-page=quarter][data-archive-app]')"
        "?.dataset.workspaceMode === 'scope'"
    )
    assert "#bgm-" not in page.url
    page.go_forward()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert "#bgm-101" in page.url
    page.get_by_role("button", name="返回结果").click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.hidden === true"
    )
    assert "#bgm-" not in page.url
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )


def test_mobile_detail_direct_hash_close_fallback_and_own_scroll(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 393, "height": 852})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/2026-07/index.html#bgm-101")
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.locator("[data-detail-panel]").evaluate(
        "node => { node.scrollTop = 240; return node.scrollTop; }"
    )
    detail_scroll = page.locator("[data-detail-panel]").evaluate(
        "node => node.scrollTop"
    )
    body_scroll = page.evaluate("window.scrollY")
    assert page.locator("[data-detail-panel]").evaluate(
        "node => node.scrollHeight > node.clientHeight"
    )
    page.locator("[data-detail-panel] [data-detail-close]").click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.hidden === true"
    )
    assert "#bgm-" not in page.url
    assert page.evaluate("window.scrollY") == body_scroll
    assert detail_scroll > 0


def test_mobile_short_detail_locks_document_and_long_detail_scrolls_own_panel(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    record = payload["tv"]["continuing"][0]
    record.update(
        {"display_summary": None, "aliases": [], "allowed_tags": [], "end_date": None}
    )
    page = chromium.new_page(viewport={"width": 393, "height": 852})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (393, 852))
    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '2400px'; node.style.paddingTop = '900px'; }"
    )
    target = page.locator('[data-subject-id="101"] [data-open-subject]')
    target.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    target.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]")
    assert detail.evaluate("node => node.scrollHeight <= node.clientHeight + 2")
    page.mouse.wheel(0, 500)
    assert page.evaluate("window.scrollY") == saved_scroll
    assert detail.evaluate("node => node.scrollTop") == 0
    page.get_by_role("button", name="返回结果").click()
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )

    record["display_summary"] = "长简介 " * 600
    _open_quarter(page, site_server, (393, 852))
    page.locator("[data-quarter-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '2400px'; node.style.paddingTop = '900px'; }"
    )
    target = page.locator('[data-subject-id="101"] [data-open-subject]')
    target.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    target.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]")
    assert detail.evaluate("node => node.scrollHeight > node.clientHeight")
    detail.evaluate("node => { node.scrollTop = 240; }")
    assert detail.evaluate("node => node.scrollTop > 0")
    assert page.evaluate("window.scrollY") == saved_scroll
    page.get_by_role("button", name="返回结果").click()
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )


def test_standalone_detail_edge_drag_threshold_direction_and_reduced_motion(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 393, "height": 852})
    page.set_default_timeout(8000)
    page.add_init_script(
        """
        (() => {
          const nativeMatchMedia = window.matchMedia.bind(window);
          window.matchMedia = query => query === '(display-mode: standalone)'
              ? {matches: true, media: query, onchange: null,
                 addListener() {}, removeListener() {}, addEventListener() {},
                 removeEventListener() {}, dispatchEvent() {return false;}}
            : nativeMatchMedia(query);
        })();
        """
    )
    page.emulate_media(reduced_motion="reduce")
    _open_quarter(page, site_server, (393, 852))
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]")
    surface = page.locator("[data-quarter-layout] .workspace")
    master = page.locator("[data-quarter-layout] .master-pane")
    saved_scroll = page.evaluate("window.scrollY")
    assert (
        page.evaluate("getComputedStyle(document.documentElement).overflow")
        == "hidden"
    )
    detail_scroll = detail.evaluate(
        "node => { node.scrollTop = Math.min(180, "
        "node.scrollHeight - node.clientHeight); return node.scrollTop; }"
    )
    page.evaluate(
        """() => document.querySelector('[data-detail-panel]').dispatchEvent(
          new PointerEvent('pointerdown', {
            bubbles: true, cancelable: true, pointerId: 10, isPrimary: true,
            clientX: 10, clientY: 360, pointerType: 'touch', buttons: 1
          })
        )"""
    )
    assert detail.get_attribute("data-detail-gesture") == "possible-drag"
    page.evaluate(
        """() => document.querySelector('[data-detail-panel]').dispatchEvent(
          new PointerEvent('pointerup', {
            bubbles: true, cancelable: true, pointerId: 10, isPrimary: true,
            clientX: 10, clientY: 360, pointerType: 'touch', buttons: 0
          })
        )"""
    )
    assert detail.get_attribute("data-detail-gesture") == "cancel"
    gesture = """
    ({id, points}) => {
      const node = document.querySelector('[data-detail-panel]');
      const fire = (type, point) => node.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, pointerId: id, isPrimary: true,
        clientX: point[0], clientY: point[1], pointerType: 'touch',
        buttons: type === 'pointerup' ? 0 : 1
      }));
      fire('pointerdown', points[0]);
      points.slice(1, -1).forEach(point => fire('pointermove', point));
      fire('pointerup', points.at(-1));
    }
    """
    page.evaluate(gesture, {"id": 11, "points": [[10, 360], [68, 360], [68, 360]]})
    assert detail.get_attribute("data-detail-gesture") == "cancel"
    assert detail.is_visible()
    assert detail.evaluate("node => node.scrollTop") == detail_scroll

    for gesture_id, delta in enumerate((10, 20, 30, 50), start=20):
        page.evaluate(
            """({id, delta}) => {
              const node = document.querySelector('[data-detail-panel]');
              const event = (type, x) => node.dispatchEvent(new PointerEvent(type, {
                bubbles: true, cancelable: true, pointerId: id, isPrimary: true,
                clientX: x, clientY: 360, pointerType: 'touch',
                buttons: type === 'pointerup' ? 0 : 1
              }));
              event('pointerdown', 10);
              event('pointermove', 10 + delta);
            }""",
            {"id": gesture_id, "delta": delta},
        )
        assert detail.get_attribute("data-detail-gesture") == "dragging"
        assert surface.evaluate("node => node.style.transform")
        assert detail.evaluate("node => node.style.transform") == ""
        assert master.evaluate("node => getComputedStyle(node).visibility") == "visible"
        page.evaluate(
            """({id, delta}) => document.querySelector('[data-detail-panel]')
              .dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true, cancelable: true, pointerId: id, isPrimary: true,
                clientX: 10 + delta, clientY: 360, pointerType: 'touch', buttons: 0
              }))""",
            {"id": gesture_id, "delta": delta},
        )
        assert detail.get_attribute("data-detail-gesture") == "cancel"
        assert detail.evaluate("node => node.scrollTop") == detail_scroll
        assert page.evaluate("window.scrollY") == saved_scroll

    page.evaluate(
        """() => document.querySelector('[data-detail-panel]').dispatchEvent(
          new PointerEvent('pointerdown', {
            bubbles: true, cancelable: true, pointerId: 14, isPrimary: true,
            clientX: 10, clientY: 360, pointerType: 'touch', buttons: 1
          })
        )"""
    )
    page.evaluate(
        """() => document.querySelector('[data-detail-panel]').dispatchEvent(
          new PointerEvent('pointermove', {
            bubbles: true, cancelable: true, pointerId: 14, isPrimary: true,
            clientX: 90, clientY: 360, pointerType: 'touch', buttons: 1
          })
        )"""
    )
    assert detail.get_attribute("data-detail-gesture") == "dragging"
    assert page.evaluate(
        "getComputedStyle(document.documentElement).overflow"
    ) == "hidden"
    page.evaluate(
        """() => document.querySelector('[data-detail-panel]').dispatchEvent(
          new PointerEvent('pointerup', {
            bubbles: true, cancelable: true, pointerId: 14, isPrimary: true,
            clientX: 90, clientY: 360, pointerType: 'touch', buttons: 0
          })
        )"""
    )
    assert detail.get_attribute("data-detail-gesture") == "cancel"

    page.evaluate(gesture, {"id": 12, "points": [[10, 360], [40, 430], [40, 430]]})
    assert detail.get_attribute("data-detail-gesture") == "cancel"
    assert detail.is_visible()

    page.evaluate(
        gesture, {"id": 13, "points": [[10, 360], [150, 360], [150, 360]]}
    )
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.hidden === true"
    )
    assert detail.get_attribute("data-detail-gesture") == "commit"
    assert "#bgm-" not in page.url


def test_mobile_archive_filter_workspace_is_full_width(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    base = next(record for record in payload["records"] if record["media"] == "TV")
    payload["records"] = [
        _facet_record(base, 101, "shared-source", "shared-tag"),
        _facet_record(base, 301, "tv-only-source", "tv-only-tag"),
    ]
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    page.locator("[data-archive-layout] .master-pane").evaluate(
        "node => { node.style.minHeight = '2200px'; node.style.paddingTop = '900px'; }"
    )
    toggle = page.locator("[data-filter-toggle]")
    toggle.scroll_into_view_if_needed()
    saved_scroll = page.evaluate("window.scrollY")
    assert saved_scroll > 0
    toggle.click()
    root = page.locator('main[data-page="archive"][data-archive-app]')
    layout = page.locator("[data-archive-layout]")
    workspace = page.locator("[data-archive-layout] .workspace")
    assert root.get_attribute("data-workspace-mode") == "filter"
    assert page.locator("[data-archive-layout] .master-pane").evaluate(
        "node => getComputedStyle(node).display"
    ) == "none"
    assert workspace.bounding_box()["width"] >= layout.bounding_box()["width"] - 2
    page.locator("[data-filter-option-search]").fill("tv-only")
    page.get_by_label("tv-only-source").check()
    assert page.locator("[data-filter-workspace-active]").is_visible()
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    page.wait_for_function(
        "target => Math.abs(window.scrollY - target) <= 2", arg=saved_scroll
    )


def test_filter_media_switch_and_archive_detail_history(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (1440, 900))
    page.locator("[data-filter-toggle]").click()
    assert page.get_by_label("续播").count() == 0
    page.get_by_role("button", name="关闭筛选").click()
    page.locator('[data-media-mode="movie"]').click()
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()

    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    workspace = page.locator("[data-archive-layout] .workspace")
    scope_width = workspace.bounding_box()["width"]
    occurrences = page.locator('[data-subject-id="101"] [data-open-subject]')
    occurrences.nth(0).click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.wait_for_function(
        "width => document.querySelector('[data-archive-layout] .workspace')"
        ".getBoundingClientRect().width > width",
        arg=scope_width,
    )
    assert workspace.bounding_box()["width"] > scope_width
    occurrences.nth(1).click()
    occurrences.nth(0).click()
    page.go_back()
    page.wait_for_function(
        "document.querySelector('[data-page=\"archive\"][data-archive-app]')"
        "?.dataset.workspaceMode === 'scope'"
    )
    assert "#bgm-" not in page.url

    occurrences.nth(0).click()
    page.locator("[data-filter-toggle]").click()
    page.locator("[data-filter-option-search]").fill("续播")
    assert page.get_by_label("续播").is_visible()
    assert page.get_by_label("首播").is_hidden()
    page.get_by_role("button", name="关闭筛选").click()
    assert page.locator('[data-page="archive"][data-archive-app]').get_attribute(
        "data-workspace-mode"
    ) == "detail"
    assert page.evaluate("window.BsbArchive.sourceLabel('unknown')") == "来源未知"


def test_query_and_media_changes_close_detail_outside_current_result(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    base = payload["tv"]["continuing"][0]
    payload["tv"]["continuing"] = [
        _facet_record(base, 101, "source-a", "tag-a"),
        _facet_record(base, 301, "source-b", "tag-b"),
    ]
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (1440, 900))
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert "#bgm-101" in page.url
    page.locator("[data-search]").fill("Facet 301")
    assert page.locator("[data-detail-panel]").is_hidden()
    assert page.locator('main[data-page="quarter"]').get_attribute(
        "data-workspace-mode"
    ) == "scope"
    assert "#bgm-" not in page.url
    assert "is-selected" not in (
        page.locator('[data-subject-id="101"]').get_attribute("class") or ""
    )
    page.locator("[data-search]").fill("")
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.locator('[data-media-mode="movie"]').click()
    assert page.locator("[data-detail-panel]").is_hidden()
    assert page.locator('main[data-page="quarter"]').get_attribute(
        "data-workspace-mode"
    ) == "scope"
    assert "#bgm-" not in page.url


def test_detail_omits_missing_facts_and_uses_safe_fallback_labels(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    record = payload["tv"]["continuing"][0]
    record["cover"] = None
    record["cover_url"] = None
    record["source"] = "unknown"
    record["episode_count"] = 0
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (390, 844))
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    detail = page.locator("[data-detail-panel]")
    assert detail.locator(".detail-cover--missing").is_visible()
    assert "来源未知" in detail.inner_text()
    assert "集数" not in detail.inner_text()
    assert detail.evaluate("node => node.scrollWidth <= node.clientWidth")


def test_bgm_571784_detail_renders_twelve_episodes(
    chromium: BrowserContext,
    site_server_571784: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_571784, (390, 844))
    page.locator('[data-subject-id="571784"] [data-open-subject]').click()
    detail = page.locator("[data-detail-panel]")
    assert "集数" in detail.inner_text()
    assert "12" in detail.inner_text()
    assert "集数 0" not in detail.inner_text()


def test_rating_format_is_consistent_in_lists_and_details(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    quarter = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    for group in (quarter["tv"]["premiere"], quarter["tv"]["continuing"]):
        for record in group:
            if record["subject_id"] == 101:
                record["rating_score"] = 7

    catalog = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    for record in catalog["records"]:
        if record["id"] == 101:
            record["score"] = 7

    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(quarter),
        ),
    )
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(catalog),
        ),
    )

    _open_quarter(page, site_server, (1440, 900))
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes("
        "'1 / 1')"
    )
    row = page.locator('[data-subject-id="101"]')
    assert row.locator(".subject-row__score b").inner_text() == "8.0"
    row.locator("[data-open-subject]").click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]")
    assert detail.locator(".detail-score").inner_text() == "7.0"
    detail.locator("[data-detail-close]").click()
    row.locator("[data-open-subject]").click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert page.locator("[data-detail-panel] .detail-score").inner_text() == "7.0"

    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    archive_row = page.locator('[data-subject-id="101"][data-quarter="2026-07"]')
    assert archive_row.locator(".subject-row__score b").inner_text() == "7.0"
    archive_row.locator("[data-open-subject]").click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert page.locator("[data-detail-panel] .detail-score").inner_text() == "7.0"


def _facet_record(
    base: dict[str, object], subject_id: int, source: str, tag: str
) -> dict[str, object]:
    record = dict(base)
    record["id"] = subject_id
    record["subject_id"] = subject_id
    record["preferred_title"] = f"Facet {subject_id}"
    record["original_title"] = f"Facet Original {subject_id}"
    record["source"] = source
    record["allowed_tags"] = [tag]
    return record


def test_section_facets_only_include_active_media_appearances(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page()
    page.goto(f"{site_server}/archive/index.html")
    values = page.evaluate(
        """
        () => {
          const record = (media, appearance) => ({
            id: 1,
            media,
            appearance,
            source: "original",
            allowed_tags: [],
          });
          return {
            premiereOnly: window.BsbArchive.availableFilterValues(
              [record("TV", "premiere")], "tv"
            ).sections,
            tvBoth: window.BsbArchive.availableFilterValues(
              [record("TV", "continuing"), record("TV", "premiere")], "tv"
            ).sections,
            movie: window.BsbArchive.availableFilterValues(
              [record("MOVIE", "premiere")], "movie"
            ).sections,
          };
        }
        """
    )
    assert values == {
        "premiereOnly": ["premiere"],
        "tvBoth": ["premiere", "continuing"],
        "movie": ["premiere"],
    }


def test_filter_facets_keep_query_and_other_dimensions_for_counts(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page()
    page.goto(f"{site_server}/archive/index.html")
    values = page.evaluate(
        """
        () => {
          const make = (id, title, source, tag, appearance, media = "TV") =>
            window.BsbArchive.asRecord({
              id, preferred_title: title, original_title: title,
              source, allowed_tags: [tag], appearance, media,
            });
          const records = [
            make(1, "Alpha one", "source-a", "tag-x", "premiere"),
            make(2, "Alpha two", "source-b", "tag-y", "continuing"),
            make(3, "Beta three", "source-a", "tag-x", "premiere"),
            make(4, "Alpha movie", "source-c", "tag-x", "premiere", "MOVIE"),
          ];
          const state = window.BsbArchive.createState({
            media: "tv", query: "alpha", filters: { tags: ["tag-x"] },
          });
          return window.BsbArchive.filterOptionMetadata(records, state);
        }
        """
    )
    assert [(item["value"], item["count"]) for item in values["sources"]] == [
        ("source-a", 1),
        ("source-b", 0),
    ]
    assert [
        (item["value"], item["count"], item["selected"])
        for item in values["tags"]
    ] == [
        ("tag-x", 1, True),
        ("tag-y", 1, False),
    ]
    assert [(item["value"], item["count"]) for item in values["sections"]] == [
        ("premiere", 1),
        ("continuing", 0),
    ]


def test_shared_pagination_tokens_are_compact_and_deterministic(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    page = chromium.new_page()
    page.goto(f"{site_server}/archive/index.html")
    cases = page.evaluate(
        """
        () => [[1, 1], [1, 2], [4, 7], [1, 20], [2, 20], [10, 20],
          [19, 20], [20, 20], [50, 100], [501, 1001]].map(([current, count]) => ({
            current,
            count,
            tokens: window.BsbArchive.paginationTokens(current, count),
          }))
        """
    )
    for case in cases:
        pages = [token for token in case["tokens"] if token != "ellipsis"]
        assert pages == sorted(set(pages))
        assert pages.count(1) == 1
        assert pages.count(case["count"]) == 1
        assert pages.count(case["current"]) == 1
        assert all(1 <= value <= case["count"] for value in pages)
        assert len(case["tokens"]) <= 7
    assert cases[3]["tokens"] == [1, 2, 3, 4, "ellipsis", 20]
    assert cases[5]["tokens"] == [1, "ellipsis", 9, 10, 11, "ellipsis", 20]
    assert cases[7]["tokens"] == [1, "ellipsis", 17, 18, 19, 20]

    quarter = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    quarter_base = quarter["tv"]["continuing"][0]
    quarter["tv"]["continuing"] = [
        {**quarter_base, "subject_id": 3000 + index}
        for index in range(160)
    ]
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(quarter)
        ),
    )
    page.goto(f"{site_server}/2026-07/index.html")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('160 / 160')"
    )
    quarter_tokens = page.locator("[data-pager] > *").all_inner_texts()
    assert quarter_tokens == ["上一页", "01", "02", "03", "04", "…", "08", "下一页"]

    catalog = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    catalog_base = next(
        record for record in catalog["records"] if record["media"] == "TV"
    )
    catalog["records"] = [
        {**catalog_base, "id": 4000 + index, "subject_id": 4000 + index}
        for index in range(160)
    ]
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(catalog)
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('160 / 160')"
    )
    archive_tokens = page.locator("[data-pager] > *").all_inner_texts()
    assert archive_tokens == quarter_tokens
    assert page.locator("[data-pager] [data-ellipsis]").evaluate_all(
        "nodes => nodes.every((node) => node.getAttribute('aria-hidden') === 'true' "
        "&& node.tabIndex === -1)"
    )


def test_quarter_filters_are_media_local_and_normalized(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    tv = payload["tv"]["continuing"][0]
    movie = payload["movie"]["premiere"][0]
    payload["tv"]["continuing"] = [
        _facet_record(tv, 101, "shared-source", "shared-tag"),
        _facet_record(tv, 301, "tv-only-source", "tv-only-tag"),
    ]
    payload["movie"]["premiere"] = [
        _facet_record(movie, 202, "shared-source", "shared-tag"),
        _facet_record(movie, 401, "movie-only-source", "movie-only-tag"),
    ]
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (1440, 900))

    page.locator("[data-filter-toggle]").click()
    assert page.locator(
        '[data-filter-option="tv-only-source"] .filter-option__count'
    ).inner_text() == "1"
    assert page.locator(
        '[data-filter-option="shared-source"] .filter-option__count'
    ).inner_text() == "1"
    option_search = page.locator("[data-filter-option-search]")
    option_search.fill("tv-only")
    assert page.get_by_label("tv-only-source").is_visible()
    assert page.get_by_label("tv-only-tag").is_visible()
    assert page.get_by_label("shared-source").is_hidden()
    assert page.get_by_label("movie-only-source").count() == 0
    assert page.get_by_label("movie-only-tag").count() == 0
    page.get_by_label("tv-only-source").check()
    assert page.locator("[data-filter-option-search]").input_value() == "tv-only"
    assert page.get_by_label("shared-source").is_hidden()
    assert page.get_by_label("tv-only-source").is_checked()
    assert (
        page.evaluate("document.activeElement?.dataset.filterValue")
        == "tv-only-source"
    )
    assert "1 / 2" in page.locator("[data-results-summary]").inner_text()
    page.get_by_label("tv-only-tag").check()
    page.locator('[data-media-mode="movie"]').click()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator("[data-filter-count]").inner_text() == ""
    page.locator("[data-filter-toggle]").click()
    assert page.locator("[data-filter-option-search]").input_value() == "tv-only"
    page.locator("[data-filter-option-search]").fill("")
    assert page.get_by_label("movie-only-source").is_visible()
    assert page.get_by_label("movie-only-tag").is_visible()
    assert page.get_by_label("tv-only-source").count() == 0

    page.locator("[data-search]").fill("no matching title")
    page.locator("[data-clear-all]").click()
    page.locator("[data-filter-toggle]").click()
    assert page.locator("[data-filter-option-search]").input_value() == ""
    assert page.locator("[data-search]").input_value() == ""
    assert page.locator("[data-filter-count]").inner_text() == ""
    assert page.get_by_label("tv-only-tag").count() == 0
    page.get_by_label("shared-source").check()
    page.get_by_label("shared-tag").check()
    page.locator('[data-media-mode="tv"]').click()
    assert page.locator("[data-filter-count]").inner_text() == "(2)"

    page.locator("[data-search]").fill("no matching title")
    page.locator("[data-clear-all]").click()
    page.locator("[data-filter-toggle]").click()
    assert page.locator("[data-filter-option-search]").input_value() == ""
    assert page.locator("[data-search]").input_value() == ""
    assert page.locator("[data-filter-count]").inner_text() == ""


def test_archive_filters_are_media_local_and_normalized(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    payload = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    tv = next(record for record in payload["records"] if record["media"] == "TV")
    movie = next(
        record for record in payload["records"] if record["media"] == "MOVIE"
    )
    payload["records"] = [
        _facet_record(tv, 301, "tv-only-source", "tv-only-tag"),
        _facet_record(tv, 101, "shared-source", "shared-tag"),
        _facet_record(movie, 401, "movie-only-source", "movie-only-tag"),
        _facet_record(movie, 202, "shared-source", "shared-tag"),
    ]
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )

    page.locator("[data-filter-toggle]").click()
    page.locator("[data-filter-option-search]").fill("tv-only")
    assert page.get_by_label("tv-only-source").is_visible()
    assert page.get_by_label("shared-source").is_hidden()
    assert page.get_by_label("movie-only-source").count() == 0
    page.get_by_label("tv-only-source").check()
    assert page.locator("[data-filter-option-search]").input_value() == "tv-only"
    assert page.get_by_label("shared-source").is_hidden()
    assert (
        page.evaluate("document.activeElement?.dataset.filterValue")
        == "tv-only-source"
    )
    assert "1 / 2" in page.locator("[data-results-summary]").inner_text()
    page.get_by_label("tv-only-tag").check()
    page.locator('[data-media-mode="movie"]').click()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator("[data-filter-count]").inner_text() == ""
    page.locator("[data-filter-toggle]").click()
    assert page.locator("[data-filter-option-search]").input_value() == "tv-only"
    page.locator("[data-filter-option-search]").fill("")
    assert page.get_by_label("movie-only-source").is_visible()
    assert page.get_by_label("movie-only-tag").is_visible()
    assert page.get_by_label("tv-only-source").count() == 0


def test_filter_options_wrap_and_selected_chip_survives_option_search(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    payload = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    base = payload["tv"]["continuing"][0]
    payload["tv"]["continuing"] = [
        _facet_record(base, 101, "source-a", "tag-a"),
        _facet_record(base, 301, "source-b", "tag-b"),
    ]
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server, (390, 844))
    page.locator("[data-filter-toggle]").click()
    group = page.locator(".filter-group").first
    assert group.evaluate("node => getComputedStyle(node).flexWrap") == "wrap"
    option = group.locator(".filter-option").first
    option.locator("input").check()
    assert option.get_attribute("data-filter-option-count") is not None
    assert "is-selected" in (option.get_attribute("class") or "")
    page.locator("[data-filter-option-search]").fill("no-such-filter-option")
    assert option.is_visible()
    assert option.locator("input").is_checked()
    option.locator("input").uncheck()
    assert not option.locator("input").is_checked()


def test_large_archive_renders_only_the_current_page_and_restores_deep_link(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    catalog = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    tv_base = next(record for record in catalog["records"] if record["media"] == "TV")
    movie_base = next(
        record for record in catalog["records"] if record["media"] == "MOVIE"
    )
    tv_records = []
    for index in range(160):
        record = _facet_record(tv_base, 1000 + index, "original", "奇幻")
        record["preferred_title"] = f"Scale {1000 + index}"
        record["quarter"] = "2026-04" if index % 4 < 2 else "2026-07"
        record["appearance"] = "premiere" if index % 2 == 0 else "continuing"
        record["score"] = 8.0
        record["rating_count"] = 100
        record["air_date"] = "2026-04-01"
        tv_records.append(record)
    movie_records = []
    for index in range(20):
        record = _facet_record(movie_base, 2000 + index, "original", "奇幻")
        record["preferred_title"] = f"Scale Movie {2000 + index}"
        record["quarter"] = "2026-04" if index % 2 == 0 else "2026-07"
        record["appearance"] = "premiere"
        record["score"] = 8.0
        record["rating_count"] = 100
        record["air_date"] = "2026-04-01"
        movie_records.append(record)
    catalog["records"] = [*tv_records, *movie_records]

    detail = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    target = _facet_record(
        detail["tv"]["continuing"][0], 1150, "original", "奇幻"
    )
    target["preferred_title"] = "Scale 1150"
    target["premiere_quarter"] = "2026-07"
    detail["tv"]["premiere"] = [target]
    detail["tv"]["continuing"] = []
    detail["movie"]["premiere"] = []

    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.add_init_script("localStorage.setItem('bsb-archive-page-size', '20')")
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(catalog)
        ),
    )
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(detail)
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('160 / 160')"
    )
    assert page.locator(".subject-row").count() == 20

    page.locator("[data-page-size] .select-trigger").click()
    page.locator('[data-page-size] [role="option"]', has_text="50").click()
    assert page.locator(".subject-row").count() == 50
    page.locator("[data-page-size] .select-trigger").click()
    page.locator('[data-page-size] [role="option"]', has_text="20").click()
    page.locator("[data-pager] button", has_text="02").click()
    assert page.locator(".subject-row").count() == 20
    assert "021" in page.locator(".subject-row__sequence").all_inner_texts()
    page.locator("[data-search]").fill("Scale 1150")
    assert page.locator(".subject-row").count() == 1

    page.goto(f"{site_server}/settings/index.html")
    page.goto(f"{site_server}/archive/index.html?year=2026#bgm-1150")
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')"
        "?.textContent.includes('Scale 1150')"
    )
    assert page.locator(".subject-row").count() <= 20
    assert "8 / 8" in page.locator("[data-results-summary]").inner_text()
    assert "151" in page.locator(".subject-row__sequence").all_inner_texts()


@pytest.mark.release_smoke
@pytest.mark.parametrize(
    "viewport", [(393, 852), (430, 932), (1024, 768), (1440, 900)]
)
def test_archive_pagination_is_real_on_mobile_and_desktop(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
    viewport: tuple[int, int],
) -> None:
    catalog = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    base = next(record for record in catalog["records"] if record["media"] == "TV")
    catalog["records"] = [
        {
            **_facet_record(base, 5000 + index, "original", "奇幻"),
            "preferred_title": f"Archive page {index + 1}",
            "quarter": "2026-07",
            "appearance": "premiere",
            "air_date": "2026-07-01",
            "rating_count": 100,
            "score": 8.0,
        }
        for index in range(60)
    ]

    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    page.add_init_script("localStorage.setItem('bsb-archive-page-size', '40')")
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(catalog),
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('60 / 60')"
    )

    trigger = page.locator("[data-page-size] .select-trigger")
    assert trigger.inner_text() == "20"
    assert page.locator(".subject-row").count() == 20
    assert page.locator("[data-pager]").is_visible()

    trigger.click()
    page.locator('[data-page-size] [role="option"]', has_text="10").click()
    assert trigger.inner_text() == "10"
    assert page.locator(".subject-row").count() == 10
    assert "第 1 / 6 页" in page.locator("[data-results-summary]").inner_text()
    page.locator("[data-pager] button", has_text="下一页").click()
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('第 2 / 6 页')"
    )
    assert page.locator(".subject-row").count() == 10
    assert "011" in page.locator(".subject-row__sequence").all_inner_texts()

    trigger.click()
    page.locator('[data-page-size] [role="option"]', has_text="50").click()
    assert trigger.inner_text() == "50"
    assert page.locator(".subject-row").count() == 50
    assert "第 1 / 2 页" in page.locator("[data-results-summary]").inner_text()
    page.locator("[data-pager] button", has_text="下一页").click()
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('第 2 / 2 页')"
    )
    assert page.locator(".subject-row").count() == 10
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_custom_listboxes_keep_keyboard_and_outside_click_behavior(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.add_init_script("localStorage.setItem('bsb-archive-page-size', '40')")
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    assert page.locator("select").count() == 0

    trigger = page.locator("[data-page-size] .select-trigger")
    assert trigger.get_attribute("role") == "combobox"
    assert trigger.inner_text() == "20"
    assert trigger.get_attribute("aria-controls")
    trigger.click()
    listbox = page.locator('[data-page-size] [role="listbox"]')
    assert listbox.is_visible()
    assert listbox.get_attribute("aria-labelledby") == trigger.get_attribute("id")
    assert trigger.get_attribute("aria-activedescendant")
    assert (
        page.locator('[data-page-size] [role="option"][aria-selected="true"]').count()
        == 1
    )
    trigger.press("End")
    trigger.press("Enter")
    assert trigger.inner_text() == "50"
    assert page.evaluate("localStorage.getItem('bsb-archive-page-size')") == "50"
    assert page.locator('[data-page-size] [role="listbox"]').is_hidden()
    trigger.press("ArrowDown")
    assert page.locator('[data-page-size] [role="listbox"]').is_visible()
    trigger.press("Escape")
    assert page.locator('[data-page-size] [role="listbox"]').is_hidden()
    assert trigger.evaluate("node => node === document.activeElement")

    trigger.click()
    page.locator("[data-results-summary]").click(position={"x": 4, "y": 4})
    assert page.locator('[data-page-size] [role="listbox"]').is_hidden()


def test_sort_popover_has_menu_state_and_focus_return(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.goto(f"{site_server}/2026-07/index.html")
    page.wait_for_selector(".subject-row")
    trigger = page.locator("[data-sort-toggle]")
    trigger.click()
    menu = page.locator('[data-sort-popover][role="menu"]')
    assert menu.is_visible()
    assert menu.locator('[role="menuitemradio"]').count() == 4
    trigger.press("Escape")
    assert menu.is_hidden()
    assert trigger.evaluate("node => node === document.activeElement")
    trigger.click()
    menu.get_by_role("menuitemradio", name="评分：低到高").click()
    assert trigger.inner_text() == "评分：低到高"
    assert menu.is_hidden()
    assert trigger.evaluate("node => node === document.activeElement")

    trigger.press("Enter")
    current = menu.locator('[role="menuitemradio"][aria-checked="true"]')
    assert current.evaluate("node => node === document.activeElement")
    current.press("ArrowDown")
    assert menu.get_by_role("menuitemradio").nth(2).evaluate(
        "node => node === document.activeElement"
    )
    current.press("Home")
    assert menu.get_by_role("menuitemradio").first.evaluate(
        "node => node === document.activeElement"
    )
    menu.get_by_role("menuitemradio").first.press("Space")
    assert menu.is_hidden()
    assert trigger.evaluate("node => node === document.activeElement")


@pytest.mark.parametrize("viewport", [(360, 800), (393, 852), (430, 932)])
def test_sort_popover_is_fixed_to_trigger_without_layout_shift(
    chromium: BrowserContext,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    trigger = page.locator("[data-sort-toggle]")
    summary = page.locator("[data-results-summary]")
    first = page.locator('[data-list-section="tv"] .subject-row:not([hidden])').first
    before = page.evaluate(
        """() => ({
          scrollY: window.scrollY,
          summaryTop: document.querySelector(
            '[data-results-summary]'
          ).getBoundingClientRect().top,
          firstTop: document.querySelector(
            '[data-list-section="tv"] .subject-row:not([hidden])'
          ).getBoundingClientRect().top,
        })"""
    )
    trigger_box = trigger.bounding_box()
    assert trigger_box is not None
    trigger.click()
    menu = page.locator('[data-sort-popover][role="menu"]')
    assert menu.is_visible()
    menu_box = menu.bounding_box()
    assert menu_box is not None
    after = page.evaluate(
        """() => ({
          scrollY: window.scrollY,
          summaryTop: document.querySelector(
            '[data-results-summary]'
          ).getBoundingClientRect().top,
          firstTop: document.querySelector(
            '[data-list-section="tv"] .subject-row:not([hidden])'
          ).getBoundingClientRect().top,
        })"""
    )
    assert after["scrollY"] == before["scrollY"]
    assert abs(after["summaryTop"] - before["summaryTop"]) <= 1
    assert abs(after["firstTop"] - before["firstTop"]) <= 1
    assert menu_box["x"] + menu_box["width"] <= viewport[0] + 1
    assert menu_box["x"] >= -1
    assert (
        menu_box["y"] >= trigger_box["y"] + trigger_box["height"] - 1
        or menu_box["y"] + menu_box["height"] <= trigger_box["y"] + 1
    )
    assert summary.is_visible() and first.is_visible()
    page.mouse.click(2, 2)
    assert menu.is_hidden()


def test_tv_sort_reorders_one_dom_sequence_and_preserves_identity(
    chromium: BrowserContext,
    site_server_mixed: str,
    unified_site_mixed: Path,
) -> None:
    payload = json.loads(
        (unified_site_mixed / "data" / "quarters" / "2026-07.json").read_text(
            "utf-8"
        )
    )
    continuing = payload["tv"]["continuing"][0]
    premiere = payload["tv"]["premiere"][0]
    continuing["score"], continuing["rating_count"] = 7.9, 90
    premiere["score"], premiere["rating_count"] = 8.0, 20
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    _open_quarter(page, site_server_mixed, (390, 844))
    page.locator('[data-subject-id="101"]').evaluate(
        "node => { node.dataset.identity = 'stable'; }"
    )

    def visible_ids() -> list[str]:
        return page.locator(
            '[data-list-section="tv"] .subject-row:not([hidden])'
        ).evaluate_all("nodes => nodes.map(node => node.dataset.subjectId)")

    assert visible_ids() == ["303", "101"]
    assert page.locator('[data-subject-id="101"]').get_attribute(
        "data-identity"
    ) == "stable"
    expected = {
        "评分：高到低": ["303", "101"],
        "评分：低到高": ["101", "303"],
        "评分人数：多到少": ["101", "303"],
        "评分人数：少到多": ["303", "101"],
    }
    for label, order in expected.items():
        page.locator("[data-sort-toggle]").click()
        page.get_by_role("menuitemradio", name=label).click()
        assert visible_ids() == order
        assert page.locator('[data-subject-id="101"]').get_attribute(
            "data-identity"
        ) == "stable"

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("续播").check()
    page.get_by_role("button", name="返回结果").click()
    assert visible_ids() == ["101"]
    page.locator("[data-filter-toggle]").click()
    page.locator("[data-filter-workspace-clear]").click()
    page.get_by_role("button", name="返回结果").click()
    assert visible_ids() == ["303", "101"]


def test_archive_tv_sort_reuses_rows_without_duplicate_nodes(
    chromium: BrowserContext,
    site_server_mixed: str,
    unified_site_mixed: Path,
) -> None:
    catalog = json.loads(
        (unified_site_mixed / "data" / "catalog" / "2026.json").read_text(
            "utf-8"
        )
    )
    for record in catalog["records"]:
        if record["media"] != "TV":
            continue
        record["score"] = 8.0 if record["id"] == 303 else 7.9
        record["rating_count"] = 20 if record["id"] == 303 else 90
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(catalog),
        ),
    )
    page.goto(f"{site_server_mixed}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes("
        "'3 / 3')"
    )
    tv_rows = page.locator('[data-list-section="tv"] .subject-row')
    assert tv_rows.count() == 3
    page.locator('[data-subject-id="101"][data-quarter="2026-07"]').evaluate(
        "node => { node.dataset.identity = 'archive-stable'; }"
    )
    ids = tv_rows.evaluate_all("nodes => nodes.map(node => node.dataset.subjectId)")
    assert ids == ["303", "101", "101"]
    page.locator("[data-sort-toggle]").click()
    page.get_by_role("menuitemradio", name="评分：低到高").click()
    assert tv_rows.count() == 3
    ids = tv_rows.evaluate_all("nodes => nodes.map(node => node.dataset.subjectId)")
    assert ids == ["101", "101", "303"]
    assert page.locator(
        '[data-subject-id="101"][data-quarter="2026-07"]'
    ).get_attribute("data-identity") == "archive-stable"


def test_large_quarter_motion_candidates_stay_bounded_at_top_middle_and_end(
    chromium: BrowserContext,
    site_server_motion_large: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(15000)
    page.emulate_media(reduced_motion="no-preference")
    _open_quarter(page, site_server_motion_large, (390, 844))
    assert page.locator("[data-record-key]").count() == 503
    page.wait_for_timeout(350)
    page.evaluate("document.documentElement.style.scrollBehavior = 'auto'")
    page.evaluate("""() => {
      const probe = window.__boundedMotionProbe = {
        enabled: false,
        geometryReads: 0,
        animationScans: 0,
        selectorNodesScanned: 0,
        animationsStarted: 0,
        animationKeys: [],
        handlerMs: null,
        frameMs: null,
      };
      const rect = Element.prototype.getBoundingClientRect;
      Element.prototype.getBoundingClientRect = function() {
        if (probe.enabled) probe.geometryReads += 1;
        return rect.call(this);
      };
      const getAnimations = Element.prototype.getAnimations;
      Element.prototype.getAnimations = function(...args) {
        if (probe.enabled) probe.animationScans += 1;
        return getAnimations.apply(this, args);
      };
      const query = Element.prototype.querySelectorAll;
      Element.prototype.querySelectorAll = function(selector) {
        const result = query.call(this, selector);
        if (probe.enabled && /data-record-key/.test(String(selector))) {
          probe.selectorNodesScanned += result.length;
        }
        return result;
      };
      const animate = Element.prototype.animate;
      Element.prototype.animate = function(...args) {
        if (probe.enabled) {
          probe.animationsStarted += 1;
          const row = this.closest('[data-record-key]');
          if (row) probe.animationKeys.push(row.dataset.recordKey);
        }
        return animate.apply(this, args);
      };
      const selector = '[role="menuitemradio"], [data-view-mode], [data-media-mode]';
      document.addEventListener('click', event => {
        if (!probe.enabled || !event.target.closest?.(selector)) return;
        probe.startedAt = performance.now();
      }, true);
      document.addEventListener('click', event => {
        if (!probe.enabled || !event.target.closest?.(selector)) return;
        const current = probe;
        current.handlerMs = performance.now() - current.startedAt;
        requestAnimationFrame(() => {
          if (!current.enabled) return;
          current.frameMs = performance.now() - current.startedAt;
        });
      });
    }""")

    measurements: list[dict[str, object]] = []
    for name, fraction, sort_index in (
        ("top", 0.0, 1),
        ("middle", 0.5, 0),
        ("end", 1.0, 1),
    ):
        page.evaluate("""fraction => {
          const maxScroll = document.documentElement.scrollHeight - innerHeight;
          window.scrollTo(0, Math.round(maxScroll * fraction));
        }""", fraction)
        page.wait_for_timeout(80)
        page.locator("[data-sort-toggle]").evaluate("element => element.click()")
        page.evaluate("""() => {
          const probe = window.__boundedMotionProbe;
          probe.geometryReads = 0;
          probe.animationScans = 0;
          probe.selectorNodesScanned = 0;
          probe.animationsStarted = 0;
          probe.animationKeys = [];
          probe.handlerMs = null;
          probe.frameMs = null;
          probe.enabled = true;
        }""")
        page.locator('[data-sort-popover] [role="menuitemradio"]') \
            .nth(sort_index).evaluate("element => element.click()")
        page.wait_for_function(
            "window.__boundedMotionProbe.frameMs !== null",
            timeout=15000,
        )
        measurement = page.evaluate("""() => {
          const probe = window.__boundedMotionProbe;
          probe.enabled = false;
          const root = document.querySelector('[data-archive-app]');
          const visibleKeys = [...root.querySelectorAll('[data-record-key]')]
            .filter(node => {
              if (node.hidden) return false;
              const rect = node.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0
                && rect.bottom >= -innerHeight && rect.top <= innerHeight * 2;
            })
            .map(node => node.dataset.recordKey);
          return {
            geometryReads: probe.geometryReads,
            animationScans: probe.animationScans,
            selectorNodesScanned: probe.selectorNodesScanned,
            animationsStarted: probe.animationsStarted,
            animationKeys: probe.animationKeys,
            handlerMs: probe.handlerMs,
            frameMs: probe.frameMs,
            visibleKeys,
            scrollY,
          };
        }""")
        measurement["position"] = name
        assert measurement["animationScans"] == 0, measurement
        assert measurement["selectorNodesScanned"] == 0, measurement
        assert measurement["geometryReads"] < 240, measurement
        assert 0 < measurement["animationsStarted"] <= 32, measurement
        assert set(measurement["animationKeys"]) <= set(measurement["visibleKeys"]), (
            measurement
        )
        assert measurement["animationKeys"], measurement
        measurements.append(measurement)
        page.wait_for_timeout(350)

    assert [item["position"] for item in measurements] == ["top", "middle", "end"]

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(80)
    current_mode = page.locator(
        '[data-page="quarter"][data-archive-app]'
    ).get_attribute("data-view-mode")
    next_mode = "list" if current_mode == "grid" else "grid"
    page.evaluate("""() => {
      const probe = window.__boundedMotionProbe;
      probe.geometryReads = 0;
      probe.animationScans = 0;
      probe.selectorNodesScanned = 0;
      probe.animationsStarted = 0;
      probe.animationKeys = [];
      probe.handlerMs = null;
      probe.frameMs = null;
      probe.enabled = true;
    }""")
    page.locator(f'[data-view-mode="{next_mode}"]').evaluate(
        "element => element.click()"
    )
    page.wait_for_function(
        "window.__boundedMotionProbe.frameMs !== null",
        timeout=15000,
    )
    view_measurement = page.evaluate("""() => {
      const probe = window.__boundedMotionProbe;
      probe.enabled = false;
      const root = document.querySelector('[data-archive-app]');
      const visibleKeys = [...root.querySelectorAll('[data-record-key]')]
        .filter(node => {
          if (node.hidden) return false;
          const rect = node.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0
            && rect.bottom >= -innerHeight && rect.top <= innerHeight * 2;
        })
        .map(node => node.dataset.recordKey);
      return {
        geometryReads: probe.geometryReads,
        animationScans: probe.animationScans,
        selectorNodesScanned: probe.selectorNodesScanned,
        animationsStarted: probe.animationsStarted,
        animationKeys: probe.animationKeys,
        visibleKeys,
        mode: root.dataset.viewMode,
      };
    }""")
    assert view_measurement["mode"] == next_mode, view_measurement
    assert view_measurement["animationScans"] == 0, view_measurement
    assert view_measurement["selectorNodesScanned"] == 0, view_measurement
    assert view_measurement["geometryReads"] < 360, view_measurement
    assert 0 < view_measurement["animationsStarted"] <= 128, view_measurement
    view_animated_keys = set(view_measurement["animationKeys"])
    view_visible_keys = set(view_measurement["visibleKeys"])
    assert view_animated_keys <= view_visible_keys, view_measurement
    page.wait_for_timeout(350)

    page.evaluate("""() => {
      const probe = window.__boundedMotionProbe;
      probe.geometryReads = 0;
      probe.animationScans = 0;
      probe.selectorNodesScanned = 0;
      probe.animationsStarted = 0;
      probe.animationKeys = [];
      probe.handlerMs = null;
      probe.frameMs = null;
      probe.enabled = true;
    }""")
    page.locator('[data-media-mode="movie"]').evaluate("element => element.click()")
    page.wait_for_function(
        "window.__boundedMotionProbe.frameMs !== null",
        timeout=15000,
    )
    media_measurement = page.evaluate("""() => {
      const probe = window.__boundedMotionProbe;
      probe.enabled = false;
      const root = document.querySelector('[data-archive-app]');
      const visibleKeys = [...root.querySelectorAll('[data-record-key]')]
        .filter(node => {
          if (node.hidden) return false;
          const rect = node.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0
            && rect.bottom >= -innerHeight && rect.top <= innerHeight * 2;
        })
        .map(node => node.dataset.recordKey);
      return {
        animationScans: probe.animationScans,
        selectorNodesScanned: probe.selectorNodesScanned,
        animationsStarted: probe.animationsStarted,
        animationKeys: probe.animationKeys,
        visibleKeys,
      };
    }""")
    assert media_measurement["animationScans"] == 0, media_measurement
    assert media_measurement["selectorNodesScanned"] == 0, media_measurement
    media_animated_keys = set(media_measurement["animationKeys"])
    media_visible_keys = set(media_measurement["visibleKeys"])
    assert media_animated_keys <= media_visible_keys, media_measurement
    page.close()


def test_result_motion_is_local_and_stagger_remains_bounded(
    chromium: BrowserContext,
    site_server_many: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, (390, 844))
    page.emulate_media(reduced_motion="no-preference")
    identity = page.locator('[data-subject-id="101"]').first.evaluate(
        "node => node.style.viewTransitionName"
    )
    assert identity == ""
    assert page.locator("header.site-header").evaluate(
        "node => getComputedStyle(node).viewTransitionName"
    ) == "none"
    assert page.locator(".quarter-nav").evaluate(
        "node => getComputedStyle(node).viewTransitionName"
    ) == "none"

    motion = page.evaluate(
        """() => {
          const root = document.querySelector('[data-page="quarter"]');
          const list = root.querySelector('[data-list-section="tv"] [data-list]');
          const nodes = [...list.children];
          let mutated = 0;
          const result = window.BsbArchive.withResultMotion(
            'sort', root, () => {
              mutated += 1;
              list.append(...nodes.reverse());
              window.BsbArchive.setResultMotionGroups(root, [[...list.children]]);
            }
          );
          const animated = nodes.filter((node) => node.getAnimations().length).length;
          const original = document.startViewTransition;
          let called = 0;
          document.startViewTransition = () => { called += 1; };
          window.BsbArchive.withResultMotion('search', root, () => { mutated += 1; });
          document.startViewTransition = original;
          document.querySelectorAll('.subject-row').forEach(
            (row) => row.classList.remove('is-entering')
          );
          window.BsbArchive.playEntranceStagger(
            document.querySelector('[data-page="quarter"]'), 12
          );
          return {
            mutated,
            resultIsUndefined: result === undefined,
            animated,
            viewTransitionCalls: called,
            entering: document.querySelectorAll('.subject-row.is-entering').length,
          };
        }"""
    )
    assert motion["mutated"] == 2
    assert motion["resultIsUndefined"] is True
    assert 0 < motion["animated"] <= 32
    assert motion["viewTransitionCalls"] == 0
    assert motion["entering"] == 10

    page.emulate_media(reduced_motion="reduce")
    reduced = page.evaluate(
        """() => {
          let mutated = 0;
          const root = document.querySelector('[data-page="quarter"]');
          const result = window.BsbArchive.withResultMotion(
            'reduced', root, () => { mutated += 1; }
          );
          document.querySelectorAll('.subject-row').forEach(
            (row) => row.classList.remove('is-entering')
          );
          window.BsbArchive.playEntranceStagger(
            document.querySelector('[data-page="quarter"]')
          );
          return {
            mutated,
            resultIsUndefined: result === undefined,
            entering: document.querySelectorAll('.subject-row.is-entering').length,
          };
        }"""
    )
    assert reduced == {
        "mutated": 1,
        "resultIsUndefined": True,
        "entering": 0,
    }


def test_result_motion_cancels_rapid_reentry_without_view_transition(
    chromium: BrowserContext,
    site_server_many: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    _open_quarter(page, site_server_many, (390, 844))
    page.emulate_media(reduced_motion="no-preference")
    result = page.evaluate(
        """() => {
          const original = document.startViewTransition;
          let viewTransitionCalls = 0;
          document.startViewTransition = () => { viewTransitionCalls += 1; };
          const root = document.querySelector('[data-page="quarter"]');
          const list = root.querySelector('[data-list-section="tv"] [data-list]');
          const nodes = [...list.children];
          let mutations = 0;
          const first = window.BsbArchive.withResultMotion(
            'normal', root, () => {
              mutations += 1;
              list.append(...nodes.reverse());
              window.BsbArchive.setResultMotionGroups(root, [[...list.children]]);
            }
          );
          const firstAnimations = nodes.reduce(
            (count, node) => count + node.getAnimations().length, 0
          );
          const second = window.BsbArchive.withResultMotion(
            'rapid-reentry', root, () => {
              mutations += 1;
              list.append(...nodes.reverse());
              window.BsbArchive.setResultMotionGroups(root, [[...list.children]]);
            }
          );
          document.startViewTransition = original;
          return {
            mutations,
            firstUndefined: first === undefined,
            secondUndefined: second === undefined,
            viewTransitionCalls,
            firstAnimations,
            activeAnimations: nodes.reduce(
              (count, node) => count + node.getAnimations().length, 0
            ),
          };
        }"""
    )
    assert result["mutations"] == 2
    assert result["firstUndefined"] is True
    assert result["secondUndefined"] is True
    assert result["viewTransitionCalls"] == 0
    assert 0 < result["firstAnimations"] <= 32
    assert 0 < result["activeAnimations"] <= 32


def test_view_mode_motion_carries_shared_geometry_without_root_animation(
    chromium: BrowserContext,
    site_server_many: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, (390, 844))
    page.emulate_media(reduced_motion="no-preference")
    page.locator('[data-view-mode="list"]').click()
    motion = page.evaluate(
        """() => {
          const root = document.querySelector('[data-page="quarter"]');
          const list = root.querySelector('[data-list-section="tv"] .result-list');
          const row = list.querySelector('.subject-row');
          return {
            columns: getComputedStyle(list).gridTemplateColumns.split(' ').length,
            coverWidth: row.querySelector(
              '.subject-row__cover'
            ).offsetWidth,
            rowAnimations: row.getAnimations().length,
            coverAnimations: row.querySelector(
              '.subject-row__cover'
            ).getAnimations().length,
            contentAnimations: row.querySelector(
              '.subject-row__content'
            ).getAnimations().length,
            scoreAnimations: row.querySelector(
              '.subject-row__score'
            ).getAnimations().length,
            rootAnimations: document.documentElement.getAnimations().length,
          };
        }"""
    )
    assert motion["columns"] == 1
    assert motion["coverWidth"] < 80
    assert motion["rowAnimations"] > 0, motion
    assert motion["coverAnimations"] > 0
    assert motion["contentAnimations"] > 0
    assert motion["scoreAnimations"] > 0
    assert motion["rootAnimations"] == 0


def test_collection_motion_stays_inside_results_and_arrival_consumes_token(
    chromium: BrowserContext,
    site_server_many: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, (390, 844))
    page.emulate_media(reduced_motion="no-preference")
    page.locator('[data-media-mode="movie"]').click()
    collection = page.evaluate(
        """() => ({
          regionAnimations: document.querySelector(
            '[data-list-sections]'
          ).getAnimations().length,
          headerAnimations: document.querySelector('header').getAnimations().length,
          controlAnimations: document.querySelector(
            '.browser-controls'
          ).getAnimations().length,
        })"""
    )
    assert collection["regionAnimations"] > 0
    assert collection["headerAnimations"] == 0
    assert collection["controlAnimations"] == 0

    arrival = page.evaluate(
        """() => {
          const root = document.querySelector('[data-page="quarter"]');
          sessionStorage.setItem('bsb-quarter-motion', JSON.stringify({
            from: '2026-04',
            to: root.dataset.quarter,
            direction: 'next',
            timestamp: Date.now()
          }));
          const played = window.BsbArchive.playQuarterArrival(root);
          return {
            played,
            token: sessionStorage.getItem('bsb-quarter-motion'),
            resultAnimations: root.querySelector(
              '.subject-row:not([hidden])'
            ).getAnimations().length,
          };
        }"""
    )
    assert arrival["played"] is True
    assert arrival["token"] is None
    assert arrival["resultAnimations"] > 0


def test_browse_operations_keep_light_background_without_root_transition(
    chromium: BrowserContext,
    site_server_many: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server_many, (390, 844))
    page.emulate_media(reduced_motion="no-preference")
    page.evaluate(
        """() => {
          window.__viewTransitionCalls = 0;
          document.startViewTransition = () => { window.__viewTransitionCalls += 1; };
        }"""
    )

    def assert_stable() -> None:
        state = page.evaluate(
            """() => {
              const coversViewport = (node) => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return style.position === 'fixed'
                  && rect.width >= innerWidth * .95
                  && rect.height >= innerHeight * .95
                  && (style.backgroundColor === 'rgb(0, 0, 0)'
                    || style.backgroundColor === 'rgba(0, 0, 0, 1)');
              };
              return {
                calls: window.__viewTransitionCalls,
                htmlBackground: getComputedStyle(
                  document.documentElement
                ).backgroundColor,
                bodyBackground: getComputedStyle(document.body).backgroundColor,
                blackOverlay: [...document.body.querySelectorAll('*')].some(
                  coversViewport
                ),
              };
            }"""
        )
        assert state["calls"] == 0
        assert state["htmlBackground"] != "rgb(0, 0, 0)"
        assert state["bodyBackground"] != "rgb(0, 0, 0)"
        assert not state["blackOverlay"]

    for selector in ('[data-media-mode="movie"]', '[data-media-mode="tv"]'):
        page.locator(selector).click()
        assert_stable()
    for selector in ('[data-view-mode="list"]', '[data-view-mode="grid"]'):
        page.locator(selector).click()
        assert_stable()
    page.locator("[data-sort-toggle]").click()
    page.get_by_role("menuitemradio", name="评分：低到高").click()
    assert_stable()
    page.locator("[data-filter-toggle]").click()
    assert page.locator("[data-filter-panel]").is_visible()
    assert_stable()
    page.locator("[data-filter-close]").first.click()
    assert_stable()
    page.locator("[data-search]").fill("subject")
    assert_stable()
    page.locator("[data-search]").fill("")
    assert_stable()


def test_archive_lazy_loads_and_reuses_selected_quarter_details(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )

    def detail_requests() -> list[str]:
        return [url for url in requests if "/data/quarters/" in url]

    assert detail_requests() == []
    list_cover = page.locator(
        '[data-subject-id="101"] .subject-row__cover img'
    ).first
    page.wait_for_function(
        """() => {
          const image = document.querySelector(
            '[data-subject-id="101"] .subject-row__cover img'
          );
          return image?.complete && image.naturalWidth > 0;
        }"""
    )
    preview_url = list_cover.evaluate("node => node.currentSrc")
    assert "/covers/previews/" in preview_url
    assert preview_url in requests
    assert not any("/covers/101.webp?v=" in url for url in requests)

    occurrences = page.locator('[data-subject-id="101"] [data-open-subject]')
    occurrences.nth(0).click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.textContent.includes('Summary')"
    )
    assert len(detail_requests()) == 1
    assert detail_requests()[0].endswith("/data/quarters/2026-04.json")
    detail_cover = page.locator(
        '[data-detail-panel] .detail-cover-button img'
    )
    page.wait_for_function(
        """() => {
          const image = document.querySelector(
            '[data-detail-panel] .detail-cover-button img'
          );
          return image?.complete && image.naturalWidth > 0;
        }"""
    )
    detail_cover_url = detail_cover.evaluate("node => node.currentSrc")
    assert "/covers/101.webp?v=" in detail_cover_url
    assert detail_cover_url in requests

    occurrences.nth(0).click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.textContent.includes('Summary')"
    )
    assert len(detail_requests()) == 1

    occurrences.nth(1).click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.textContent.includes('2026-07')"
    )
    assert len(detail_requests()) == 2
    assert detail_requests()[1].endswith("/data/quarters/2026-07.json")

    page.locator('[data-media-mode="movie"]').click()
    page.locator('[data-subject-id="202"] [data-open-subject]').click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')"
        "?.textContent.includes('中文 202')"
    )
    assert len(detail_requests()) == 2


def test_quarter_and_archive_details_show_all_aliases_and_search_them(
    chromium: BrowserContext,
    site_server: str,
    unified_site: Path,
) -> None:
    aliases = [
        "Alias One",
        "Alias Two",
        "Alias Three",
        "Alias Four",
        "Final Alias",
    ]
    quarter = json.loads(
        (unified_site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    quarter["tv"]["continuing"][0]["aliases"] = aliases
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/2026-07.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(quarter)
        ),
    )
    _open_quarter(page, site_server, (390, 844))
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    detail = page.locator("[data-detail-panel]")
    assert all(alias in detail.inner_text() for alias in aliases)
    assert "另外" not in detail.inner_text()
    assert detail.evaluate("node => node.scrollWidth <= node.clientWidth")
    detail.locator("[data-detail-close]").click()
    page.locator("[data-search]").fill("Final Alias")
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()

    catalog = json.loads(
        (unified_site / "data" / "catalog" / "2026.json").read_text("utf-8")
    )
    for archive_record in catalog["records"]:
        if archive_record["id"] == 101:
            archive_record["aliases"] = aliases
    premiere = json.loads(
        (unified_site / "data" / "quarters" / "2026-04.json").read_text("utf-8")
    )
    premiere["tv"]["premiere"][0]["aliases"] = aliases
    page.route(
        "**/data/catalog/2026.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(catalog)
        ),
    )
    page.route(
        "**/data/quarters/2026-04.json",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(premiere)
        ),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    page.locator('[data-subject-id="101"] [data-open-subject]').first.click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')"
        "?.textContent.includes('Final Alias')"
    )
    detail = page.locator("[data-detail-panel]")
    assert all(alias in detail.inner_text() for alias in aliases)
    assert "另外" not in detail.inner_text()
    assert detail.evaluate("node => node.scrollWidth <= node.clientWidth")
    detail.locator("[data-detail-close]").click()
    page.locator("[data-search]").fill("Final Alias")
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()


@pytest.mark.parametrize("page_kind", ["quarter", "archive"])
def test_search_input_paste_rapid_changes_and_ime_commit(
    chromium: BrowserContext,
    site_server_mixed: str,
    page_kind: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)
    if page_kind == "quarter":
        _open_quarter(page, site_server_mixed, (390, 844))
    else:
        page.goto(f"{site_server_mixed}/archive/index.html?year=2026")
        page.wait_for_function(
            "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
        )

    initial_ids = page.locator(
        ".subject-row:not([hidden]) [data-open-subject]"
    ).evaluate_all(
        "nodes => nodes.map(node => node.closest('.subject-row').dataset.subjectId)"
    )
    assert "303" in initial_ids
    stable_row = page.locator('[data-subject-id="303"]').first
    stable_row.evaluate("node => { node.dataset.searchIdentity = 'preserved'; }")
    search = page.locator("[data-search]")

    search.fill("中文 303")
    assert page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    ) == ["303"]
    assert page.locator('[data-subject-id="303"]').first.get_attribute(
        "data-search-identity"
    ) == "preserved"

    search.fill("")
    search.focus()
    page.evaluate("""() => {
      const input = document.querySelector('[data-search]');
      const text = 'Original 303';
      const clipboard = new DataTransfer();
      clipboard.setData('text/plain', text);
      input.dispatchEvent(new ClipboardEvent('paste', {
        bubbles: true, cancelable: true, clipboardData: clipboard,
      }));
      input.setRangeText(text, 0, input.value.length, 'end');
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true, inputType: 'insertFromPaste', data: text,
      }));
    }""")
    assert search.input_value() == "Original 303"
    assert page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    ) == ["303"]
    assert page.evaluate(
        "document.activeElement === document.querySelector('[data-search]')"
    )

    page.locator('[data-subject-id="303"] [data-open-subject]').first.click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    search.fill("999999")
    search.fill("101")
    assert page.locator("[data-detail-panel]").is_hidden()
    assert set(page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    )) == {"101"}

    interim = page.evaluate("""() => {
      const input = document.querySelector('[data-search]');
      input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      input.value = '303';
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        inputType: 'insertCompositionText',
        data: '303',
        isComposing: true,
      }));
      return {
        chip: document.querySelector('[data-active-filter-type="query"]')
          ?.textContent || '',
        ids: [...document.querySelectorAll('.subject-row:not([hidden])')]
          .map(node => node.dataset.subjectId),
      };
    }""")
    assert interim["chip"] == "搜索：101 ×"
    assert set(interim["ids"]) == {"101"}
    page.evaluate("""() => {
      const input = document.querySelector('[data-search]');
      input.dispatchEvent(new CompositionEvent('compositionend', {
        bubbles: true, data: '303',
      }));
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true, inputType: 'insertText', data: '303', isComposing: false,
      }));
    }""")
    assert page.locator("[data-search]").input_value() == "303"
    assert page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    ) == ["303"]
    assert page.locator(
        '[data-active-filter-type="query"]'
    ).inner_text() == "搜索：303 ×"

    search.fill("no matching fixture title")
    assert page.locator("[data-no-results]").is_visible()
    search.fill("")
    assert page.locator("[data-no-results]").is_hidden()
    assert page.locator(".subject-row:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.subjectId)"
    ) == initial_ids
    assert page.locator('[data-active-filter-type="query"]').count() == 0


def test_archive_detail_failure_stays_same_origin_and_reports_rebuild(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    page.route(
        "**/data/quarters/*.json",
        lambda route: route.fulfill(status=404, body="missing"),
    )
    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes('appearance')"
    )
    page.locator('[data-subject-id="101"] [data-open-subject]').first.click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')"
        "?.textContent.includes('DATA UNAVAILABLE')"
    )
    detail = page.locator("[data-detail-panel]").inner_text()
    assert "当前资料详情未完整生成" in detail
    assert "重新 build" in detail


def test_quarter_and_archive_close_restore_keyboard_focus(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 390, "height": 844})
    page.set_default_timeout(8000)

    _open_quarter(page, site_server, (390, 844))
    quarter_subject = page.locator(
        '[data-subject-id="101"] [data-open-subject]'
    )
    quarter_subject.focus()
    quarter_subject.press("Enter")
    quarter_close = page.locator("[data-detail-panel] [data-detail-close]")
    quarter_close.focus()
    quarter_close.press("Enter")
    assert quarter_subject.evaluate("node => node === document.activeElement")

    quarter_filter = page.locator("[data-filter-toggle]")
    quarter_filter.focus()
    quarter_filter.press("Enter")
    quarter_filter_close = page.locator(
        "[data-filter-panel] .filter-panel__head [data-filter-close]"
    )
    quarter_filter_close.focus()
    quarter_filter_close.press("Enter")
    assert quarter_filter.evaluate("node => node === document.activeElement")

    page.goto(f"{site_server}/archive/index.html?year=2026")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')"
        "?.textContent.includes('appearance')"
    )
    archive_subject = page.locator(
        '[data-subject-id="101"] [data-open-subject]'
    ).first
    archive_subject.focus()
    archive_subject.press("Enter")
    archive_close = page.locator("[data-detail-panel] [data-detail-close]")
    archive_close.focus()
    archive_close.press("Enter")
    assert archive_subject.evaluate("node => node === document.activeElement")

    archive_filter = page.locator("[data-filter-toggle]")
    archive_filter.focus()
    archive_filter.press("Enter")
    archive_filter_close = page.locator(
        "[data-filter-panel] .filter-panel__head [data-filter-close]"
    )
    archive_filter_close.focus()
    archive_filter_close.press("Enter")
    assert archive_filter.evaluate("node => node === document.activeElement")
