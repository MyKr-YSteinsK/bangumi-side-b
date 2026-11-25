"""Archive history and overlapping loads on a generated offline-v2 site."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from bgm_side_b.domain import MediaFormat, Quarter
from bgm_side_b.release.site_candidate import validate_site
from bgm_side_b.repository import QuarterSyncState, SubjectRepository
from tests.test_site_builder import _build_fixture, _subject
from tests.test_unified_site_browser import (
    _archive_catalog_server,
    _catalog_gate,
    _wait_archive_scope,
)

pytestmark = [pytest.mark.browser, pytest.mark.deep_regression]


@pytest.fixture(params=("chromium", "webkit"))
def route_page(request: pytest.FixtureRequest) -> Iterator[Page]:
    with (
        sync_playwright() as runner,
        getattr(runner, request.param).launch() as browser,
    ):
        mobile = request.param == "webkit"
        context = browser.new_context(
            viewport={"width": 393, "height": 852} if mobile else {
                "width": 1440, "height": 900
            },
            is_mobile=mobile,
            has_touch=mobile,
            reduced_motion="reduce",
        )
        try:
            yield context.new_page()
        finally:
            context.close()


@pytest.fixture
def routing_site(tmp_path: Path) -> Path:
    builder, database = _build_fixture(tmp_path, include_same_quarter_tv=True)
    repository = SubjectRepository(database)
    quarter = Quarter(2025, 4)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, _subject(505, MediaFormat.TV, quarter)
        )
        date = "2025-04-01T00:00:00Z"
        repository.write_sync_state(
            connection,
            QuarterSyncState(quarter, "complete", "complete", 1, 1, date, date),
        )
    builder.build()
    validate_site(builder.site_directory, workspace=builder.workspace_directory)
    return builder.site_directory


def test_archive_back_reloads_year_when_local_verification_cleared_its_rows(
    route_page: Page, routing_site: Path,
) -> None:
    page = route_page
    with _archive_catalog_server(routing_site, {}) as server:
        page.goto(f"{server}/archive/index.html?year=2026&q=101")
        _wait_archive_scope(page, "YEAR / 2026")
        page.wait_for_function("typeof window.BsbPwa?.listQuarterStates === 'function'")
        online_rows = page.locator('[data-subject-id="101"]')
        expect(online_rows).to_have_count(2)
        year_url = page.url
        page.evaluate("""() => {
          const pwa = window.BsbPwa;
          let resume;
          const hold = new Promise(resolve => { resume = resolve; });
          window.BsbPwa = {
            ...pwa,
            listQuarterStates() {
              window.testLocalRead = hold.then(() => pwa.listQuarterStates());
              return window.testLocalRead;
            },
          };
          window.releaseLocalRead = () => resume();
        }""")
        page.locator('[data-scope-choice="local"]').click()
        page.wait_for_function("Boolean(window.testLocalRead)")
        expect(page.locator("[data-local-status]")).to_contain_text("正在校验")
        assert parse_qs(urlsplit(page.url).query)["local"] == ["1"]
        expect(online_rows).to_have_count(0)
        page.go_back()
        _wait_archive_scope(page, "YEAR / 2026")
        expect(online_rows).to_have_count(2)
        page.evaluate(
            "async () => { window.releaseLocalRead(); await window.testLocalRead; }"
        )
        expect(page.locator("[data-search]")).to_have_value("101")
        expect(online_rows).to_have_count(2)
        assert page.url == year_url


def test_archive_local_choice_survives_index_failure_during_history_restore(
    route_page: Page, routing_site: Path,
) -> None:
    page = route_page
    gate = _catalog_gate(status=503)
    requested, release = gate["requested"], gate["release"]
    assert isinstance(requested, threading.Event)
    assert isinstance(release, threading.Event)
    page.add_init_script("""(() => {
      const request = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const response = await request(...args);
        if (response.status === 503
            && response.url.endsWith('/data/archive-index.json')) {
          setTimeout(() => { window.indexFailureDelivered = true; }, 0);
        }
        return response;
      };
    })();""")
    with _archive_catalog_server(routing_site, {"archive-index": [gate]}) as server:
        page.goto(
            f"{server}/archive/index.html?year=2026", wait_until="domcontentloaded"
        )
        assert requested.wait(timeout=10)
        page.evaluate("history.pushState({}, '', '?year=2025#bgm-505')")
        page.evaluate("history.back()")
        expect(page).to_have_url(f"{server}/archive/index.html?year=2026")
        page.locator('[data-scope-choice="local"]').click()
        expect(page.locator("[data-local-status]")).to_contain_text("尚无已下载")
        local_url = page.url
        with page.expect_response(
            lambda response: response.url.endswith("/data/archive-index.json")
        ) as failed_index:
            release.set()
        assert failed_index.value.status == 503
        page.wait_for_function("window.indexFailureDelivered === true")
        expect(page.locator('[data-scope-choice="local"]')).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.locator("[data-local-status]")).to_contain_text("尚无已下载")
        assert page.url == local_url


def test_archive_normalizes_sort_and_keeps_the_exact_shared_occurrence(
    route_page: Page, routing_site: Path,
) -> None:
    page = route_page
    with _archive_catalog_server(routing_site, {}) as server:
        page.goto(
            f"{server}/archive/index.html?year=2026&sort=constructor"
            "&media=movie&media=tv&occ=2026-07.continuing#bgm-101"
        )
        detail = page.locator("[data-detail-panel]")
        expect(detail).to_contain_text("续播")
        expect(detail).to_contain_text("2026-07")
        params = parse_qs(urlsplit(page.url).query)
        assert params == {"year": ["2026"], "occ": ["2026-07.continuing"]}
        page.reload()
        expect(detail).to_contain_text("2026-07")
        detail.get_by_role("button", name="返回结果").click()
        expect(page).to_have_url(f"{server}/archive/index.html?year=2026")
        page.goto(f"{server}/archive/index.html?year=2026#bgm-101")
        expect(detail).to_contain_text("2026-04")
        assert parse_qs(urlsplit(page.url).query)["occ"] == ["2026-04.premiere"]


def test_archive_late_quarter_detail_cannot_replace_a_newer_cached_payload(
    route_page: Page, routing_site: Path,
) -> None:
    page = route_page
    gate = _catalog_gate()
    requested, release = gate["requested"], gate["release"]
    assert isinstance(requested, threading.Event)
    assert isinstance(release, threading.Event)
    quarter_path = routing_site / "data/quarters/2026-07.json"
    original_bytes = quarter_path.read_bytes()
    later_payload = json.loads(original_bytes)
    for group in ("premiere", "continuing"):
        for item in later_payload["tv"][group]:
            item["display_summary"] = "Second response"
    page.add_init_script("""(() => {
      window.quarterBodiesRead = 0;
      let detailRequests = 0;
      const fetchResource = window.fetch.bind(window);
      window.fetch = async (input, options) => {
        const target = input instanceof Request ? input.url : input;
        const url = new URL(target, location.href);
        if (!url.pathname.endsWith('/data/quarters/2026-07.json')) {
          return fetchResource(input, options);
        }
        url.searchParams.set('test-response', String(++detailRequests));
        const response = await fetchResource(url, options);
        const read = response.json.bind(response);
        response.json = async () => {
          const payload = await read();
          setTimeout(() => { window.quarterBodiesRead += 1; }, 0);
          return payload;
        };
        return response;
      };
    })();""")
    with _archive_catalog_server(routing_site, {"quarter:2026-07": [gate]}) as server:
        year_url = f"{server}/archive/index.html?year=2026"
        page.goto(year_url)
        _wait_archive_scope(page, "YEAR / 2026")
        continuing = page.locator(
            '[data-subject-id="101"][data-quarter="2026-07"] [data-open-subject]'
        )
        continuing.click()
        assert requested.wait(timeout=10)
        detail = page.locator("[data-detail-panel]")
        expect(detail).to_contain_text("正在读取季度详情")
        page.evaluate("history.back()")
        expect(page).to_have_url(year_url)
        expect(detail).to_be_hidden()
        quarter_path.write_text(json.dumps(later_payload), encoding="utf-8")
        page.locator('[data-subject-id="303"] [data-open-subject]').click()
        expect(detail).to_contain_text("Second response")
        page.wait_for_function("window.quarterBodiesRead === 1")
        current_url = page.url

        quarter_path.write_bytes(original_bytes)
        release.set()
        page.wait_for_function("window.quarterBodiesRead === 2")
        expect(detail).to_contain_text("Second response")
        assert page.url == current_url
        detail.get_by_role("button", name="返回结果").click()
        expect(page).to_have_url(year_url)
        continuing.click()
        expect(detail).to_contain_text("Second response")
        assert page.evaluate("window.quarterBodiesRead") == 2


def test_archive_creates_rows_by_page_and_reuses_visited_nodes(
    route_page: Page, tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(
        tmp_path, include_same_quarter_tv=True, extra_same_quarter_tv=26,
    )
    builder.build()
    page = route_page
    page.add_init_script("""(() => {
      window.articlesCreated = 0;
      const create = Document.prototype.createElement;
      Document.prototype.createElement = function(tag, ...args) {
        if (tag === 'article') window.articlesCreated += 1;
        return create.call(this, tag, ...args);
      };
    })();""")
    with _archive_catalog_server(builder.site_directory, {}) as server:
        page.goto(f"{server}/archive/index.html?year=2026")
        _wait_archive_scope(page, "YEAR / 2026")
        rows = page.locator("[data-list-sections] .subject-row")
        expect(rows).to_have_count(20)
        assert page.evaluate("window.articlesCreated") == 20
        expect(page.locator("[data-filter-panel] fieldset")).to_have_count(0)
        row_keys = "nodes => nodes.map(node => node.dataset.recordKey)"
        first_page = rows.evaluate_all(row_keys)
        rows.first.evaluate("node => { window.firstPageRow = node; }")

        page.locator('[data-pager] [aria-label="第 2 页"]').last.click()
        expect(page.locator("[data-pager] [aria-current=page]")).to_have_text("02")
        second_count = rows.count()
        assert 0 < second_count < 20
        assert page.evaluate("window.articlesCreated") == 20 + second_count
        page.evaluate("history.back()")
        expect(page.locator("[data-pager] [aria-current=page]")).to_have_text("01")
        assert rows.evaluate_all(row_keys) == first_page
        assert rows.first.evaluate("node => node === window.firstPageRow")
        assert page.evaluate("window.articlesCreated") == 20 + second_count
        page.locator("[data-filter-toggle]").click()
        expect(page.locator('[data-filter-value="continuing"]')).to_be_attached()


def test_archive_refreshes_row_data_when_occurrence_keys_are_unchanged(
    route_page: Page, routing_site: Path,
) -> None:
    page = route_page
    catalog_path = routing_site / "data/catalog/2026.json"
    with _archive_catalog_server(routing_site, {}) as server:
        page.goto(f"{server}/archive/index.html?year=2026")
        _wait_archive_scope(page, "YEAR / 2026")
        title = page.locator(
            '[data-subject-id="101"][data-quarter="2026-04"] .subject-row__title'
        )
        expect(title).to_have_text("中文 101")
        catalog = json.loads(catalog_path.read_bytes())
        for record in catalog["records"]:
            if record["id"] == 101 and record["quarter"] == "2026-04":
                record["preferred_title"] = "Updated catalog title"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        page.locator('[data-scope-choice="range"]').click()
        page.locator("[data-archive-from]").fill("2026")
        page.locator("[data-archive-to]").fill("2026")
        page.locator("[data-range-apply]").click()
        _wait_archive_scope(page, "RANGE / 2026—2026")
        expect(title).to_have_text("Updated catalog title")
