"""Run repeatable Archive measurements on isolated, generated v2 sites."""

from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import shutil
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from statistics import median
from time import perf_counter
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from playwright.sync_api import (
    Browser,
    BrowserType,
    Page,
    TimeoutError,
    sync_playwright,
)

from bgm_side_b import __version__
from bgm_side_b.build.site_builder import UnifiedSiteBuilder
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database
from bgm_side_b.domain import MediaFormat, Quarter
from bgm_side_b.release.site_candidate import validate_site
from bgm_side_b.repository import CoverRecord, QuarterSyncState, SubjectRepository
from tests.test_pwa_browser import _wait_for_queue
from tests.test_site_builder import ROOT, _subject
from tests.test_unified_site_browser import _choose_archive_year, _server

BASE_PATH = "/bangumi-side-b"
ARCHIVE = f"{BASE_PATH}/archive/index.html"
PROBE = Path(__file__).with_name("browse_probe.js")
WEAK = (180, 220, 350, 450)
FAST = (0, 0, 0, 0)


@dataclass(frozen=True)
class Scenario:
    name: str
    viewport: tuple[int, int] = (393, 852)
    query: str = ""
    delays: tuple[int, int, int, int] = FAST
    pressure: bool = False
    cpu: int = 1
    warm: bool = False
    cold_process: bool = False
    interactions: bool = False


SCENARIOS = (
    Scenario("mobile-default", interactions=True),
    Scenario("desktop-default-weak", (1440, 900), delays=WEAK, interactions=True),
    Scenario("mobile-explicit-year", (360, 800), "?year=2025", WEAK),
    Scenario("mobile-range", (430, 932), "?from=2025&to=2026", WEAK),
    Scenario("index-delay-only", delays=(180, 0, 0, 0)),
    Scenario("catalog-delay-only", delays=(0, 220, 0, 0)),
    Scenario("warm-reload", (1440, 900), "?year=2026", warm=True),
    Scenario("cold-browser", (1440, 900), cold_process=True),
    Scenario(
        "long-range", query="?from=2017&to=2026",
        pressure=True, cpu=4, interactions=True,
    ),
)


def synthetic_covers() -> list[bytes]:
    palette = ("#566b71", "#804d4a", "#435d63", "#68563d", "#444d70", "#676c50")
    rendered = []
    for index, color in enumerate(palette, start=1):
        image = Image.new("RGB", (96, 144), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 88, 135), outline="#faf0d6", width=2)
        draw.ellipse((18, 40, 78, 100), outline="#faf0d6", width=2)
        draw.text((16, 16), "SIDE B", fill="#faf0d6")
        draw.text((18, 110), f"STUDY {index}", fill="#faf0d6")
        stream = io.BytesIO()
        image.save(stream, format="WEBP", quality=80)
        rendered.append(stream.getvalue())
    return rendered


def build_sample(
    directory: Path, app_scripts: dict[str, Path], *, pressure: bool,
) -> tuple[dict[str, Path], dict]:
    facts = directory / "facts"
    database = Database(facts / "data" / "archive.sqlite3")
    database.initialize()
    covers_dir = facts / "covers"
    covers_dir.mkdir()
    covers = synthetic_covers()
    cover_meta = [
        CoverRecord(
            f"https://example.invalid/{i}", "large",
            hashlib.sha256(payload).hexdigest(), 96, 144, len(payload),
        )
        for i, payload in enumerate(covers)
    ]
    first = Quarter(2017 if pressure else 2025, 1)
    last = Quarter(2026, 7)
    quarters = []
    current = first
    while current <= last:
        quarters.append(current)
        current = current.next()
    records_per_quarter = 80 if pressure else 73
    repository = SubjectRepository(database)
    subject_id = 10000
    appearances = 0
    timestamp = "2026-07-25T00:00:00Z"
    with repository.transaction() as connection:
        for position, quarter in enumerate(quarters):
            count = records_per_quarter + int(not pressure and position < 2)
            repository.write_sync_state(
                connection,
                QuarterSyncState(
                    quarter, "complete", "complete", count, count, timestamp, timestamp,
                ),
            )
            for row in range(count):
                media = MediaFormat.MOVIE if row % 11 == 0 else MediaFormat.TV
                continuing = (
                    quarter.next()
                    if media == MediaFormat.TV and row % 5 == 1 and quarter < last
                    else None
                )
                image_index = row % len(covers)
                cover = cover_meta[image_index] if row % 3 else None
                if cover:
                    (covers_dir / f"{subject_id}.webp").write_bytes(covers[image_index])
                subject = _subject(subject_id, media, quarter, cover=cover,
                                   continuing=continuing)
                repository.replace_subject_snapshot(
                    connection,
                    replace(
                        subject,
                        tags=("奇幻", "日常") if row % 2 else ("科幻",),
                        subject=replace(
                            subject.subject, rating_score=5 + (row % 43) / 10,
                            rating_count=10 + (subject_id % 3300),
                        ),
                    ),
                )
                subject_id += 1
                appearances += 1 + int(continuing is not None)
    sites = {}
    for name, script in app_scripts.items():
        run = directory / name
        workspace = run / "workspace"
        shutil.copytree(covers_dir, workspace / "covers")
        frontend = run / "frontend"
        shutil.copytree(ROOT / "static", frontend / "static")
        shutil.copyfile(script, frontend / "static/js/app.js")
        site = run / "bangumi-side-b"
        UnifiedSiteBuilder(
            frontend, database, load_tag_rules(ROOT / "config/allowed-tags.toml"),
            workspace_directory=workspace, site_directory=site,
            changelog_path=ROOT / "CHANGELOG.md",
        ).build()
        validate_site(site, workspace=workspace)
        sites[name] = site
    metadata = {
        "subjects": subject_id - 10000, "appearances": appearances,
        "quarters": len(quarters), "years": len({item.year for item in quarters}),
        "page_size": 20, "application_version": __version__,
    }
    return sites, metadata


@contextmanager
def serve(site: Path, delays: tuple[int, int, int, int]) -> Iterator[str]:
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            wait_ms = (
                delays[0] if path.endswith("/archive-index.json")
                else delays[1] if "/data/catalog/" in path
                else delays[2] if "/data/quarters/" in path
                else delays[3] if "/covers/" in path
                else 0
            )
            if wait_ms:
                threading.Event().wait(wait_ms / 1000)
            try:
                super().do_GET()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                # A measured context may close before a delayed image finishes.
                return

        def end_headers(self) -> None:
            path = urlsplit(self.path).path
            cache = "public, max-age=1800" if "/assets/" in path else "no-store"
            self.send_header("Cache-Control", cache)
            super().end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = _server(functools.partial(Handler, directory=str(site.parent)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def wait_stamp(page: Page, name: str) -> None:
    try:
        page.wait_for_function(
            "name => !!window.browseProbe?.stamps[name]", arg=name,
        )
    except TimeoutError as error:
        evidence = page.evaluate(
            "({ stamps: browseProbe.stamps, summary:"
            "document.querySelector('[data-results-summary]')?.textContent })"
        )
        raise AssertionError(f"{name} did not settle: {evidence}") from error


def observe_cover(page: Page) -> dict:
    # A narrow viewport can contain no visible cover. Keep the text-ready sample
    # and report a missing cover stamp if one has not appeared in this window.
    try:
        page.wait_for_function("() => !!browseProbe.stamps.cover", timeout=2500)
    except TimeoutError:
        pass
    return page.evaluate("browseProbe.snapshot()")


def arm(page: Page, name: str, selector: str, expected: str = "") -> None:
    page.evaluate(
        "args => window.browseProbe.arm(...args)", [name, selector, expected],
    )


def run_interactions(page: Page, scenario: Scenario) -> None:
    rows = page.locator(".subject-row:visible")
    first = rows.first
    subject_id = first.get_attribute("data-subject-id")
    assert subject_id is not None
    selection = first.locator("[data-open-subject]")
    title = first.locator(".subject-row__title").inner_text()
    first.evaluate("node => { window.benchOriginalRow = node; }")

    arm(page, "search", "[data-search]", subject_id)
    search = page.locator("[data-search]")
    search.fill(f"中文 {subject_id}")
    wait_stamp(page, "search")
    search.fill("")
    arm(page, "filter", "[data-filter-toggle]")
    page.locator("[data-filter-toggle]").click()
    wait_stamp(page, "filter")
    filter_option = page.locator('[data-filter-value="continuing"]').locator("..")
    mobile = scenario.viewport[0] < 768
    if mobile:
        filter_option.click()
        arm(page, "facet", ".filter-apply-mobile")
        page.locator(".filter-apply-mobile").click()
    else:
        arm(page, "facet", ".filter-option")
        filter_option.click()
        wait_stamp(page, "facet")
        page.locator("[data-filter-panel] [data-filter-close]").first.click()
    wait_stamp(page, "facet")
    page.locator('[data-active-filter-type="sections"]').click()

    arm(page, "detail", "[data-open-subject]", title)
    selection.click()
    wait_stamp(page, "detail")
    arm(page, "back", "[data-detail-close]", subject_id)
    page.locator("[data-detail-panel] [data-detail-close]").click()
    wait_stamp(page, "back")
    assert first.evaluate("node => node === window.benchOriginalRow")
    if not scenario.query:
        arm(page, "year", '[role="option"]', "YEAR / 2025")
        _choose_archive_year(page, 2025)
        wait_stamp(page, "year")


def sample_online(
    browser: Browser, base: str, scenario: Scenario, *, cpu: int,
) -> dict:
    width, height = scenario.viewport
    with browser.new_context(
        viewport={"width": width, "height": height}, service_workers="block",
        reduced_motion="reduce",
    ) as context:
        context.add_init_script(path=PROBE)
        page = context.new_page()
        # Keep timing slow samples through completion, including the CPU-limited
        # long history. The readiness predicate is independent of this ceiling.
        page.set_default_timeout(45000 if cpu > 1 else 12000)
        if cpu > 1:
            context.new_cdp_session(page).send(
                "Emulation.setCPUThrottlingRate", {"rate": cpu},
            )
        page.goto(f"{base}{ARCHIVE}{scenario.query}", wait_until="domcontentloaded")
        wait_stamp(page, "ready")
        if scenario.warm:
            observe_cover(page)
            page.reload(wait_until="domcontentloaded")
            wait_stamp(page, "ready")
        assert page.locator("[data-search]").is_enabled()
        assert 0 < page.locator(".subject-row:visible").count() <= 20
        first = page.evaluate("browseProbe.snapshot()")
        with_cover = None if scenario.pressure else observe_cover(page)
        actions = {}
        if scenario.interactions:
            run_interactions(page, scenario)
            actions = page.evaluate("browseProbe.snapshot()")
        return {"initial": first, "with_cover": with_cover, "after_actions": actions}


def online(
    engine: BrowserType, sites_by_size: dict[bool, dict[str, Path]],
    sample_count: int, scenarios: tuple[Scenario, ...], output: Path,
) -> dict:
    result = {}
    with engine.launch() as shared:
        for case in scenarios:
            cpu = case.cpu if engine.name == "chromium" else 1
            variants = sites_by_size[case.pressure]
            samples = {name: [] for name in variants}
            print(engine.name, case.name, flush=True)
            with serve_variants(variants, case.delays) as servers:
                for index in range(sample_count):
                    order = list(servers) if index % 2 == 0 else list(reversed(servers))
                    for name in order:
                        base = servers[name]
                        if case.cold_process:
                            start = perf_counter()
                            with engine.launch() as fresh:
                                launch = (perf_counter() - start) * 1000
                                value = sample_online(fresh, base, case, cpu=cpu)
                                value["launch_ms"] = launch
                        else:
                            value = sample_online(shared, base, case, cpu=cpu)
                        samples[name].append(value)
            initial = [item["initial"] for items in samples.values() for item in items]
            for key in ("scope", "summary", "rowIds"):
                expected = initial[0][key]
                assert all(item[key] == expected for item in initial), (case.name, key)
            result[case.name] = {
                "viewport": case.viewport, "endpoint_delays_ms": case.delays,
                "cpu": cpu, "warm": case.warm, "new_process": case.cold_process,
                "pressure": case.pressure, "samples": samples,
                "browser": shared.version,
            }
            (output / f"{engine.name}-{case.name}.json").write_text(
                json.dumps(result[case.name], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return result


@contextmanager
def serve_variants(
    variants: dict[str, Path], delays: tuple[int, int, int, int],
) -> Iterator[dict[str, str]]:
    with ExitStack() as stack:
        yield {
            key: stack.enter_context(serve(site, delays))
            for key, site in variants.items()
        }


def sample_local(browser: Browser, base: str, sample_count: int) -> dict:
    labels = ["2026-04", "2026-07"]
    results = {}
    with browser.new_context(
        viewport={"width": 393, "height": 852}, reduced_motion="reduce",
    ) as context:
        context.add_init_script(path=PROBE)
        page = context.new_page()
        page.goto(f"{base}{BASE_PATH}/settings/index.html")
        page.wait_for_function(
            "BsbPwa.capabilityState() === 'ready' "
            "&& navigator.serviceWorker.controller !== null"
        )
        page.evaluate("labels => BsbPwa.enqueue(labels)", labels)
        _wait_for_queue(page, 2)
        assert page.evaluate(
            "async () => (await BsbPwa.listQuarterStates())"
            ".filter(state => state.status === 'COMPLETE').length"
        ) == 2
        requests: list[str] = []
        page.on("request", lambda req: requests.append(urlsplit(req.url).path))
        for phase, expected in (
            ("complete", "本机覆盖 2 个季度"),
            ("failed-staging", "本机覆盖 2 个季度"),
            ("missing-data", "本机覆盖 1 个季度"),
            ("no-packages", "尚无已下载"),
        ):
            if phase == "failed-staging":
                page.evaluate("""async () => {
                  const cache = await caches.open(BsbPwa.META_CACHE);
                  const key = new URL(
                    '../__bsb_meta__/quarters/2026-07.json', location.href);
                  const current = await BsbPwa.getQuarterState('2026-07');
                  await cache.put(key, new Response(JSON.stringify({
                    ...current, status: 'UPDATE_INCOMPLETE', error: 'fixture failure',
                    staging: { ...current.active, verified_hashes: [] },
                  })));
                }""")
            elif phase == "missing-data":
                assert page.evaluate("""async () => {
                  const { active } = await BsbPwa.getQuarterState('2026-07');
                  const key = new URL(
                    '../__bsb_content__/' + active.data_revision, location.href);
                  return (await caches.open(BsbPwa.CONTENT_CACHE)).delete(key);
                }""")
            elif phase == "no-packages":
                page.evaluate("""async labels => {
                  for (const label of labels) await BsbPwa.removeQuarter(label);
                }""", labels)
            samples = []
            for _ in range(sample_count):
                page.goto(f"{base}{ARCHIVE}?year=2026", wait_until="domcontentloaded")
                wait_stamp(page, "ready")
                context.set_offline(True)
                page.wait_for_function("navigator.onLine === false")
                start_requests = len(requests)
                earlier = page.evaluate("browseProbe.snapshot().metrics")
                page.evaluate("performance.clearResourceTimings()")
                arm(page, "local", '[data-scope-choice="local"]', expected)
                page.locator('[data-scope-choice="local"]').click()
                wait_stamp(page, "local")
                status = page.locator("[data-local-status]").inner_text()
                if phase == "failed-staging":
                    assert "更新未完成" in status
                if phase == "missing-data":
                    assert "跳过未通过校验" in status
                new_requests = requests[start_requests:]
                assert not any(
                    path.endswith("/archive-index.json")
                    or "/data/catalog/" in path or "/data/quarters/" in path
                    for path in new_requests
                )
                samples.append({
                    "sample": page.evaluate("browseProbe.snapshot()"),
                    "prior_work": earlier, "status": status, "requests": new_requests,
                })
                context.set_offline(False)
                page.wait_for_function("navigator.onLine === true")
            results[phase] = samples
    return results


def offline(
    engine: BrowserType, variants: dict[str, Path], sample_count: int,
) -> dict:
    with engine.launch() as browser, serve_variants(variants, FAST) as servers:
        return {
            name: sample_local(browser, base, sample_count)
            for name, base in servers.items()
        }


def summarize(data: dict) -> None:
    for engine, cases in data.items():
        for name, case in cases.items():
            for variant, samples in case["samples"].items():
                first = [item["initial"] for item in samples]
                times = [item["stamps"]["ready"]["frame"] for item in first]
                work = first[0]["metrics"]
                print(
                    f"{engine} {name} {variant} | ready {median(times):.1f} ms "
                    f"[{min(times):.1f}, {max(times):.1f}] | "
                    f"rows created {work.get('createdArticles', {}).get('calls', 0)}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-script", type=Path)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--engine", choices=["chromium", "webkit"], action="append")
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    selected = tuple(
        case for case in SCENARIOS if not args.scenario or case.name in args.scenario
    )
    if not selected or (args.scenario and set(args.scenario) - {
        case.name for case in SCENARIOS
    }):
        parser.error("unknown scenario")
    if args.offline_only:
        selected = ()
    args.output.mkdir(parents=True, exist_ok=False)
    variants = {"current": ROOT / "static/js/app.js"}
    if args.baseline_script:
        variants = {"baseline": args.baseline_script, **variants}
    sites_by_size, fixtures = {}, {}
    sizes = {False} if args.offline_only else {case.pressure for case in selected}
    for pressure in sorted(sizes):
        key = "long" if pressure else "normal"
        sites_by_size[pressure], fixtures[key] = build_sample(
            args.output / key, variants, pressure=pressure,
        )
    engines = args.engine or ["chromium"]
    with sync_playwright() as playwright:
        if args.offline_only:
            result = {
                name: offline(
                    getattr(playwright, name), sites_by_size[False], args.samples
                )
                for name in engines
            }
        else:
            result = {
                name: online(
                    getattr(playwright, name), sites_by_size, args.samples, selected,
                    args.output,
                )
                for name in engines
            }
    hashes = {
        name: hashlib.sha256(script.read_bytes()).hexdigest()
        for name, script in variants.items()
    }
    category = "offline" if args.offline_only else "online"
    (args.output / "measurements.json").write_text(
        json.dumps({"fixtures": fixtures, "scripts": hashes, category: result},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not args.offline_only:
        summarize(result)


if __name__ == "__main__":
    main()
