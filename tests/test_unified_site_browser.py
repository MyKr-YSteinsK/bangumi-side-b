"""Chromium smoke coverage for the Plan 16 unified static site."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from tests.test_site_builder import _build_fixture


@pytest.fixture
def unified_site(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    yield tmp_path / "dist" / "site"


@pytest.fixture
def chromium() -> Iterator[Browser]:
    with sync_playwright() as runner:
        browser = runner.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def site_server(unified_site: Path) -> Iterator[str]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(unified_site),
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
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


def test_quarter_detail_movie_history_and_lightbox(
    chromium: Browser,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (1440, 900))

    page.goto(f"{site_server}/2026-07/index.html#bgm-101")
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    detail = page.locator("[data-detail-panel]").inner_text()
    assert "续播" in detail
    assert "2026-04" in detail
    assert "2026-07" in detail
    assert "结束日期" not in detail

    page.locator('[data-media-mode="movie"]').click()
    assert page.locator('[data-appearance-section="continuing"]').is_hidden()
    assert "1 / 1" in page.locator("[data-results-summary]").inner_text()
    page.locator('[data-subject-id="202"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    assert "MOVIE" in page.locator("[data-detail-panel]").inner_text()
    page.locator("[data-detail-panel] [data-detail-close]").click()
    assert page.locator("[data-detail-panel]").is_hidden()

    page.locator('[data-media-mode="tv"]').click()
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.locator("[data-detail-panel] [data-lightbox]").click()
    page.wait_for_selector(".cover-lightbox", state="attached")
    page.locator(".cover-lightbox .lightbox-close").click()
    assert page.locator(".cover-lightbox").count() == 0


@pytest.mark.parametrize(
    "viewport",
    [
        (1920, 1080),
        (1440, 900),
        (1366, 768),
        (1024, 768),
        (768, 1024),
        (430, 932),
        (390, 844),
        (360, 800),
    ],
)
def test_quarter_shell_is_usable_across_plan16_viewports(
    chromium: Browser,
    site_server: str,
    viewport: tuple[int, int],
) -> None:
    page = chromium.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, viewport)
    page.locator('[data-subject-id="101"] [data-open-subject]').click()
    page.wait_for_selector('[data-detail-panel]:not([hidden])')
    layout = page.locator("[data-quarter-layout]").evaluate(
        "node => getComputedStyle(node).gridTemplateColumns"
    )
    assert layout
    assert page.locator("[data-detail-panel]").is_visible()
    if viewport[0] < 768:
        assert page.locator('[data-subject-id="101"] .subject-row__cover').evaluate(
            "node => getComputedStyle(node).display"
        ) == "none"


def test_archive_year_range_hash_and_same_origin_network(
    chromium: Browser,
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


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_scope_detail_and_filter_keep_the_context_rail(
    chromium: Browser,
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
    rail_width = master.bounding_box()["width"]
    assert 90 <= rail_width <= 125

    page.locator("[data-filter-toggle]").click()
    page.wait_for_selector('[data-filter-panel]:not([hidden])')
    assert root.get_attribute("data-workspace-mode") == "filter"
    assert "#bgm-101" in page.url
    assert abs(master.bounding_box()["width"] - rail_width) <= 2
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "detail"

    page.locator("[data-filter-toggle]").click()
    page.get_by_label("首播").check()
    page.get_by_role("button", name="关闭筛选").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    assert page.url.endswith("/2026-07/index.html")


def test_filter_media_switch_and_archive_detail_history(
    chromium: Browser,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    _open_quarter(page, site_server, (1440, 900))
    page.locator("[data-filter-toggle]").click()
    page.get_by_label("续播").check()
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
