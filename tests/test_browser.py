"""Small Chromium regressions for portable, fully static archive output."""

from __future__ import annotations

import functools
import http.server
import threading
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.repository import (
    EpisodeRecord,
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
            (101, 1, "很长的测试标题一"),
            (102, 4, "测试标题二"),
        ):
            repository.upsert_subject(
                connection,
                SubjectRecord(
                    subject_id,
                    "tv",
                    "用于 Chromium 回归的完整简介。" * 8,
                    date(2022, month, 1),
                    51,
                    7.5 if subject_id == 101 else None,
                    100 if subject_id == 101 else None,
                ),
            )
            repository.replace_titles(
                connection, subject_id, [SubjectTitle("preferred", title)]
            )
            repository.replace_quarters(
                connection, subject_id, [SubjectQuarter(2022, month, "new")]
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


def test_local_file_archive_restores_state_and_remains_offline(
    static_site: Path, browser: Browser
) -> None:
    page = browser.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto((static_site / "local" / "quarters" / "2022-01" / "index.html").as_uri())
    page.locator("[data-search-input]").fill("标题一")
    assert page.locator(".subject-card:not([hidden])").count() == 1
    page.locator("[data-open-drawer]").click()
    assert page.locator("#subject-drawer").evaluate("node => node.open")
    page.keyboard.press("Escape")
    page.locator(".subject-card__detail-link").click()
    assert page.locator("[data-subject-detail]").count() == 1
    page.go_back()
    assert page.locator("[data-search-input]").input_value() == "标题一"
    page.locator("[data-toggle-episodes]").click()
    assert page.locator("[data-extra-episode][hidden]").count() == 0
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
            "quarters/2022-01/index.html"
        )
        assert page.locator(".subject-card").count() == 1
        stylesheet = page.locator('link[rel="stylesheet"]').get_attribute("href")
        assert stylesheet is not None and not stylesheet.startswith("assets/")
        assert not (static_site / "pages" / "media" / "characters").exists()
    finally:
        server.shutdown()
        thread.join()
