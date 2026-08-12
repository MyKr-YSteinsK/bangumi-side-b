"""Chromium smoke coverage for the Plan 16 unified static site."""

from __future__ import annotations

import functools
import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, sync_playwright

from tests.test_site_builder import _build_fixture

pytestmark = pytest.mark.browser

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


@pytest.fixture
def unified_site(tmp_path: Path) -> Iterator[Path]:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    yield tmp_path / "dist" / "site"


class BrowserHarness:
    def __init__(self, context: BrowserContext) -> None:
        self.context = context

    def new_page(self, *, viewport: dict[str, int] | None = None) -> Page:
        page = self.context.new_page()
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


def _open_quarter(page: Page, root: str, viewport: tuple[int, int]) -> None:
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto(f"{root}/2026-07/index.html")
    page.wait_for_selector(".subject-row")
    page.wait_for_function(
        "document.querySelector('[data-results-summary]')?.textContent.includes(' / ')"
    )


def test_quarter_detail_movie_history_and_lightbox(
    chromium: BrowserContext,
    site_server: str,
) -> None:
    page = chromium.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(8000)
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    _open_quarter(page, site_server, (1440, 900))
    assert any("/covers/101.webp?v=" in url for url in requests)

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
    assert "?v=" in page.locator(
        "[data-detail-panel] [data-lightbox] img"
    ).get_attribute("src")
    page.locator("[data-detail-panel] [data-lightbox]").click()
    page.wait_for_selector(".cover-lightbox", state="attached")
    assert "?v=" in page.locator(".cover-lightbox img").get_attribute("src")
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
    chromium: BrowserContext,
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


@pytest.mark.parametrize("viewport", [(390, 844), (360, 800)])
def test_mobile_scope_detail_and_filter_keep_the_context_rail(
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
    assert page.get_by_label("首播").count() == 0
    assert page.get_by_label("续播").count() == 0
    page.get_by_role("button", name="关闭筛选").click()
    page.get_by_role("button", name="关闭详情").click()
    assert root.get_attribute("data-workspace-mode") == "scope"
    assert page.url.endswith("/2026-07/index.html")


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
    assert page.get_by_label("tv-only-source").is_visible()
    assert page.get_by_label("tv-only-tag").is_visible()
    assert page.get_by_label("movie-only-source").count() == 0
    assert page.get_by_label("movie-only-tag").count() == 0
    page.get_by_label("tv-only-source").check()
    page.get_by_label("tv-only-tag").check()
    page.locator('[data-media-mode="movie"]').click()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator("[data-filter-count]").inner_text() == ""

    page.locator("[data-filter-toggle]").click()
    assert page.get_by_label("movie-only-source").is_visible()
    assert page.get_by_label("movie-only-tag").is_visible()
    assert page.get_by_label("tv-only-source").count() == 0
    assert page.get_by_label("tv-only-tag").count() == 0
    page.get_by_label("shared-source").check()
    page.get_by_label("shared-tag").check()
    page.locator('[data-media-mode="tv"]').click()
    assert page.locator("[data-filter-count]").inner_text() == "(2)"


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
    assert page.get_by_label("tv-only-source").is_visible()
    assert page.get_by_label("movie-only-source").count() == 0
    page.get_by_label("tv-only-source").check()
    page.get_by_label("tv-only-tag").check()
    page.locator('[data-media-mode="movie"]').click()
    assert "2 / 2" in page.locator("[data-results-summary]").inner_text()
    assert page.locator("[data-filter-count]").inner_text() == ""
    page.locator("[data-filter-toggle]").click()
    assert page.get_by_label("movie-only-source").is_visible()
    assert page.get_by_label("movie-only-tag").is_visible()
    assert page.get_by_label("tv-only-source").count() == 0


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

    page.locator("[data-page-size]").select_option("100")
    assert page.locator(".subject-row").count() == 100
    page.locator("[data-page-size]").select_option("20")
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
    assert any("/covers/101.webp?v=" in url for url in requests)

    occurrences = page.locator('[data-subject-id="101"] [data-open-subject]')
    occurrences.nth(0).click()
    page.wait_for_function(
        "document.querySelector('[data-detail-panel]')?.textContent.includes('Summary')"
    )
    assert len(detail_requests()) == 1
    assert detail_requests()[0].endswith("/data/quarters/2026-04.json")

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
