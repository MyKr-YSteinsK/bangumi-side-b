"""Integration checks for the unique offline site builder."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from PIL import Image

from bgm_side_b.build.site_builder import BuildError, UnifiedSiteBuilder
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
    SourceDecision,
    SourceType,
)
from bgm_side_b.repository import (
    CoverRecord,
    QuarterAppearance,
    QuarterSyncState,
    ReviewIssue,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)

ROOT = Path(__file__).parents[1]


def _valid_cover_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (8, 12), "#8a3147").save(stream, format="WEBP", lossless=True)
    return stream.getvalue()


def _subject(
    subject_id: int,
    media: MediaFormat,
    quarter: Quarter,
    *,
    cover: CoverRecord | None = None,
    continuing: Quarter | None = None,
) -> SubjectSnapshot:
    return SubjectSnapshot(
        SubjectRecord(
            subject_id,
            f"Original {subject_id}",
            f"中文 {subject_id}",
            "Summary",
            media,
            date(quarter.year, quarter.month, 2),
            None,
            12,
            8.0,
            10,
            JapaneseDecision(
                JapaneseClassification.ACCEPTED_JAPANESE,
                "bangumi_public_region_tag",
                "日本",
            ),
        ),
        tags=("奇幻",),
        source=SourceDecision(SourceType.ORIGINAL_ANIME, "infobox", "原创"),
        premiere=QuarterAppearance(
            quarter,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            date(quarter.year, quarter.month, 2).isoformat(),
        ),
        continuing=(
            ()
            if continuing is None
            else (
                QuarterAppearance(
                    continuing,
                    QuarterAppearanceKind.CONTINUING,
                    QuarterAssignmentSource.AUTOMATIC,
                    "main_episode_airdate",
                    f"{continuing.year:04d}-{continuing.month:02d}-03",
                ),
            )
        ),
        cover=cover,
    )


def _build_fixture(tmp_path: Path) -> tuple[UnifiedSiteBuilder, Database]:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "archive.sqlite3")
    database.initialize()
    covers = workspace / "covers"
    covers.mkdir(parents=True)
    cover_bytes = _valid_cover_bytes()
    (covers / "101.webp").write_bytes(cover_bytes)
    cover = CoverRecord(
        "https://example.invalid/101",
        "large",
        hashlib.sha256(cover_bytes).hexdigest(),
        9,
        1,
        len(cover_bytes),
    )
    repository = SubjectRepository(database)
    april = _subject(
        101,
        MediaFormat.TV,
        Quarter(2026, 4),
        cover=cover,
        continuing=Quarter(2026, 7),
    )
    movie = _subject(202, MediaFormat.MOVIE, Quarter(2026, 7))
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, april)
        repository.replace_subject_snapshot(connection, movie)
        repository.write_sync_state(
            connection,
            QuarterSyncState(Quarter(2026, 4), "complete", "complete", 1, 0, now, now),
        )
        repository.write_sync_state(
            connection,
            QuarterSyncState(Quarter(2026, 7), "complete", "complete", 2, 1, now, now),
        )
    tags = load_tag_rules(ROOT / "config" / "allowed-tags.toml")
    builder = UnifiedSiteBuilder(
        ROOT,
        database,
        tags,
        workspace_directory=workspace,
        site_directory=tmp_path / "dist" / "site",
        reports_directory=workspace / "reports",
    )
    return builder, database


def test_build_all_writes_one_site_and_second_run_skips(tmp_path: Path) -> None:
    builder, _ = _build_fixture(tmp_path)
    first = builder.build()
    site = tmp_path / "dist" / "site"
    assert (site / "index.html").is_file()
    assert (site / "2026-04" / "index.html").is_file()
    assert (site / "2026-07" / "index.html").is_file()
    assert (site / "archive" / "index.html").is_file()
    assert (site / "settings" / "index.html").is_file()
    assert (site / "data" / "offline" / "2026-07.json").is_file()
    assert not (site / "subjects").exists()
    archive_html = (site / "archive" / "index.html").read_text("utf-8")
    assert 'data-archive-index-url="../data/archive-index.json"' in archive_html
    assert 'data-scope-choice="range"' in archive_html
    catalog = json.loads((site / "data" / "catalog" / "2026.json").read_text("utf-8"))
    assert {"aliases", "quarter", "appearance", "cover"} <= set(
        catalog["records"][0]
    )
    assert not {
        "display_summary",
        "end_date",
        "premiere_quarter",
        "bangumi_url",
    } & set(catalog["records"][0])
    july = json.loads(
        (site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    assert [item["subject_id"] for item in july["tv"]["continuing"]] == [101]
    assert [item["subject_id"] for item in july["movie"]["premiere"]] == [202]
    assert 'src="../covers/101.webp?v=' in (
        site / "2026-07" / "index.html"
    ).read_text("utf-8")
    assert [path.name for path in (site / "covers").glob("*.webp")] == [
        "101.webp"
    ]
    offline = json.loads(
        (site / "data" / "offline" / "2026-07.json").read_text("utf-8")
    )
    cover_resource = next(
        item for item in offline["resources"] if item["url"] == "covers/101.webp"
    )
    assert "?" not in cover_resource["url"]
    assert cover_resource["content_hash"] == hashlib.sha256(
        _valid_cover_bytes()
    ).hexdigest()
    second = builder.build()
    assert second.patch.written == ()
    assert second.dirty.skipped_quarters == ("2026-04", "2026-07")
    assert first.report_path.is_file()


def test_unified_pwa_shell_is_complete_stable_and_prefix_safe(tmp_path: Path) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    expected = {
        "sw.js",
        "manifest.webmanifest",
        "assets/pwa.js",
        "icons/pwa-192.png",
        "icons/pwa-512.png",
        "icons/pwa-maskable-512.png",
        "icons/favicon.svg",
        "data/pwa-shell.json",
    }
    state = json.loads((tmp_path / "workspace" / "build-state.json").read_text("utf-8"))
    assert expected <= set(state["artifacts"])

    manifest = json.loads((site / "manifest.webmanifest").read_text("utf-8"))
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "./"
    assert manifest["display"] == "standalone"
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    for relative, dimensions in (
        ("icons/pwa-192.png", (192, 192)),
        ("icons/pwa-512.png", (512, 512)),
        ("icons/pwa-maskable-512.png", (512, 512)),
    ):
        with Image.open(site / relative) as icon:
            assert icon.size == dimensions

    shell = json.loads((site / "data" / "pwa-shell.json").read_text("utf-8"))
    shell_urls = {item["url"] for item in shell["resources"]}
    assert shell["schema"] == 1
    assert expected - {"sw.js", "data/pwa-shell.json"} <= shell_urls
    assert "index.html" in shell_urls
    assert "archive/index.html" in shell_urls
    assert "settings/index.html" in shell_urls
    assert "data/archive-index.json" not in shell_urls
    assert not any(
        url.startswith(("2026-", "data/quarters/", "covers/"))
        for url in shell_urls
    )
    assert shell["revision"] in (site / "sw.js").read_text("utf-8")

    for relative in (
        "index.html",
        "archive/index.html",
        "settings/index.html",
        "2026-07/index.html",
    ):
        page = (site / relative).read_text("utf-8")
        assert 'rel="manifest"' in page
        assert 'name="theme-color"' in page
        assert 'rel="icon"' in page
        assert "assets/pwa.js?v=" in page
        assert "assets/app.css?v=" in page
        assert "assets/app.js?v=" in page
    root = (site / "index.html").read_text("utf-8")
    assert "http-equiv=\"refresh\"" not in root
    assert "2026-07" not in root
    assert 'data-archive-index-url="data/archive-index.json"' in root


def test_pwa_shell_revision_changes_only_with_shell_inputs(tmp_path: Path) -> None:
    builder, database = _build_fixture(tmp_path)
    isolated_root = tmp_path / "project"
    shutil.copytree(ROOT / "static", isolated_root / "static")
    builder.root = isolated_root.resolve()
    builder.build()
    site = tmp_path / "dist" / "site"

    def shell_state() -> tuple[str, bytes]:
        shell = json.loads((site / "data" / "pwa-shell.json").read_text("utf-8"))
        return shell["revision"], (site / "sw.js").read_bytes()

    initial = shell_state()
    repository = SubjectRepository(database)
    subject = repository.get_subject_facts(202)
    assert subject is not None
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            replace(subject, subject=replace(subject.subject, rating_score=9.2)),
        )
    builder.build()
    assert shell_state() == initial

    css = isolated_root / "static" / "css" / "site.css"
    css.write_text(
        css.read_text("utf-8") + "\n/* shell revision test */\n",
        encoding="utf-8",
    )
    builder.build()
    after_css = shell_state()
    assert after_css[0] != initial[0]
    assert after_css[1] != initial[1]

    manifest = isolated_root / "static" / "pwa" / "manifest.webmanifest"
    manifest.write_text(
        manifest.read_text("utf-8").replace("Side B", "BGM B"),
        encoding="utf-8",
    )
    builder.build()
    after_manifest = shell_state()
    assert after_manifest[0] != after_css[0]

    icon = isolated_root / "static" / "icons" / "pwa-192.png"
    icon.write_bytes(icon.read_bytes() + b"test")
    builder.build()
    assert shell_state()[0] != after_manifest[0]


def test_offline_package_revision_covers_resources_but_not_sw_or_icons(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    isolated_root = tmp_path / "project"
    shutil.copytree(ROOT / "static", isolated_root / "static")
    builder.root = isolated_root.resolve()
    builder.build()
    site = tmp_path / "dist" / "site"

    def package_state() -> tuple[str, str, bytes]:
        package = json.loads(
            (site / "data" / "offline" / "2026-07.json").read_text("utf-8")
        )
        shell = json.loads((site / "data" / "pwa-shell.json").read_text("utf-8"))
        return package["revision"], shell["revision"], (site / "sw.js").read_bytes()

    initial = package_state()
    css = isolated_root / "static" / "css" / "site.css"
    css.write_text(
        css.read_text("utf-8") + "\n/* package revision */\n", encoding="utf-8"
    )
    builder.build()
    after_css = package_state()
    assert after_css[0] != initial[0]

    sw = isolated_root / "static" / "pwa" / "sw.js"
    sw.write_text(
        sw.read_text("utf-8") + "\n// worker-only revision\n", encoding="utf-8"
    )
    builder.build()
    after_sw = package_state()
    assert after_sw[0] == after_css[0]
    assert after_sw[1] == after_css[1]
    assert after_sw[2] != after_css[2]

    icon = isolated_root / "static" / "icons" / "pwa-192.png"
    icon.write_bytes(icon.read_bytes() + b"icon-only")
    builder.build()
    after_icon = package_state()
    assert after_icon[0] == after_sw[0]


def test_quarter_output_uses_master_detail_shell_and_static_rows(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    page = (tmp_path / "dist" / "site" / "2026-07" / "index.html").read_text(
        "utf-8"
    )
    assert 'data-page="quarter"' in page
    assert 'data-archive-app' in page
    assert 'class="subject-row"' in page
    assert 'class="workspace"' in page
    assert 'data-detail-panel' in page
    assert 'data-media="movie"' in page
    assert 'class="subject-card"' not in page
    assert 'id="subject-drawer"' not in page


def test_rating_change_only_dirties_own_quarter_and_shared_indexes(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_subject_facts(101)
    assert current is not None
    changed = current.subject.__class__(
        current.subject.subject_id,
        current.subject.name_original,
        current.subject.name_cn,
        current.subject.summary_raw,
        current.subject.media_format,
        current.subject.air_date,
        current.subject.end_date,
        current.subject.episode_count,
        9.0,
        current.subject.rating_count,
        current.subject.japanese,
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, replace(current, subject=changed)
        )
    run = builder.build()
    assert run.dirty.dirty_quarters == ("2026-04", "2026-07")
    assert "2026" in run.dirty.dirty_years
    assert run.dirty.archive_dirty


def test_incomplete_facts_keep_last_good_site_untouched(tmp_path: Path) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_sync_state(Quarter(2026, 7))
    assert current is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            replace(current, facts_status="incomplete"),
        )
    before = (tmp_path / "dist" / "site" / "2026-07" / "index.html").read_bytes()
    run = builder.build()
    assert run.patch.written == ()
    assert "2026-07" in run.dirty.skipped_quarters
    assert (
        tmp_path / "dist" / "site" / "2026-07" / "index.html"
    ).read_bytes() == before


def test_blacklist_residue_fails_closed_before_writing_site(tmp_path: Path) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.excluded_subject_ids = frozenset({101})
    with pytest.raises(BuildError, match="blacklist residue"):
        builder.build()
    assert not (tmp_path / "dist" / "site").exists()


def test_blocked_quarter_retains_only_its_last_good_artifacts(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    before = (site / "2026-07" / "index.html").read_bytes()
    repository = SubjectRepository(database)
    state = repository.get_sync_state(Quarter(2026, 7))
    assert state is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection, replace(state, facts_status="incomplete")
        )
        current = repository.get_subject_facts(101)
        assert current is not None
        changed = replace(
            current,
            subject=replace(current.subject, rating_score=9.0),
        )
        repository.replace_subject_snapshot(connection, changed)

    run = builder.build()
    assert run.dirty.dirty_quarters == ("2026-04",)
    assert (site / "2026-07" / "index.html").read_bytes() == before
    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04", "2026-07"]
    assert any("2026-07" in warning for warning in run.warnings)


def test_unmanaged_appearance_and_new_blocked_quarter_are_not_public(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    repository = SubjectRepository(database)
    current = repository.get_subject_facts(101)
    assert current is not None
    october = _subject(303, MediaFormat.TV, Quarter(2026, 10))
    unmanaged = _subject(404, MediaFormat.TV, Quarter(2027, 1))
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, october)
        repository.replace_subject_snapshot(connection, unmanaged)
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                Quarter(2026, 10),
                "incomplete",
                "incomplete",
                1,
                0,
                "2026-10-01T00:00:00Z",
                None,
            ),
        )
    first = builder.build()
    site = tmp_path / "dist" / "site"
    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04", "2026-07"]
    assert not (site / "2026-10").exists()
    assert any("2026-10" in warning for warning in first.warnings)
    assert any("2027-01" in warning for warning in first.warnings)

    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                Quarter(2026, 10),
                "complete",
                "complete",
                1,
                0,
                "2026-10-01T00:00:00Z",
                "2026-10-01T00:00:00Z",
            ),
        )
        repository.replace_review_issues(
            connection,
            303,
            (
                ReviewIssue(
                    "TV_QUARTER_BOUNDARY",
                    Quarter(2026, 10),
                    "2026-09-30",
                    {"reason": "test"},
                    "2026-10-01T00:00:00Z",
                ),
            ),
        )
    second = builder.build()
    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04", "2026-07"]
    assert not (site / "2026-10").exists()
    assert any("2026-10" in warning for warning in second.warnings)


def test_blocked_last_good_without_site_is_omitted_with_warning(tmp_path: Path) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    shutil.rmtree(site / "2026-07")
    (site / "data" / "quarters" / "2026-07.json").unlink()
    (site / "data" / "offline" / "2026-07.json").unlink()
    repository = SubjectRepository(database)
    state = repository.get_sync_state(Quarter(2026, 7))
    assert state is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection, replace(state, facts_status="incomplete")
        )
    run = builder.build()
    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04"]
    assert not (site / "2026-07" / "index.html").exists()
    assert any("no last-known-good" in warning for warning in run.warnings)


def test_blocked_last_good_with_missing_cover_is_omitted_safely(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    (site / "covers" / "101.webp").unlink()
    repository = SubjectRepository(database)
    state = repository.get_sync_state(Quarter(2026, 7))
    assert state is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection, replace(state, facts_status="incomplete")
        )

    run = builder.build()

    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04"]
    assert not (site / "2026-07" / "index.html").exists()
    assert not (site / "data" / "quarters" / "2026-07.json").exists()
    assert "covers/101.webp" in run.patch.written
    assert (site / "2026-04" / "index.html").is_file()
    assert any("2026-07" in warning for warning in run.warnings)


def test_blocked_last_good_without_aggregate_metadata_is_omitted(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    (site / "data" / "catalog" / "2026.json").unlink()
    repository = SubjectRepository(database)
    state = repository.get_sync_state(Quarter(2026, 7))
    assert state is not None
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection, replace(state, facts_status="incomplete")
        )

    run = builder.build()

    archive = json.loads((site / "data" / "archive-index.json").read_text("utf-8"))
    catalog = json.loads((site / "data" / "catalog" / "2026.json").read_text("utf-8"))
    assert [item["quarter"] for item in archive["quarters"]] == ["2026-04"]
    assert {item["quarter"] for item in catalog["records"]} == {"2026-04"}
    assert not (site / "2026-07" / "index.html").exists()
    assert any("2026-07" in warning for warning in run.warnings)


def test_relevant_review_on_last_good_quarter_retains_previous_output(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist" / "site"
    before = (site / "2026-07" / "index.html").read_bytes()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.replace_review_issues(
            connection,
            101,
            (
                ReviewIssue(
                    "TV_QUARTER_BOUNDARY",
                    Quarter(2026, 7),
                    "2026-06-30",
                    {"reason": "test"},
                    "2026-07-01T00:00:00Z",
                ),
            ),
        )
    run = builder.build()
    assert (site / "2026-07" / "index.html").read_bytes() == before
    assert any("2026-07" in warning for warning in run.warnings)


def test_noop_build_does_not_read_cover_bytes_or_scan_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    cover = (tmp_path / "workspace" / "covers" / "101.webp").resolve()
    original_read_bytes = Path.read_bytes
    original_rglob = Path.rglob

    def fail_cover_read(path: Path) -> bytes:
        if path.resolve() == cover:
            raise AssertionError("no-op build read a cover")
        return original_read_bytes(path)

    def fail_site_scan(path: Path, pattern: str):
        if path.resolve() == (tmp_path / "dist" / "site").resolve():
            raise AssertionError("no-op build scanned the site tree")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "read_bytes", fail_cover_read)
    monkeypatch.setattr(Path, "rglob", fail_site_scan)
    run = builder.build()
    assert run.patch.written == ()
    assert run.patch.deleted == ()
    report = json.loads(run.report_path.read_text("utf-8"))
    assert report["written_artifacts_count"] == 0
    assert report["written_files_sample"] == []
    assert report["deleted_artifacts_count"] == 0
    assert report["deleted_files_sample"] == []
    assert report["reused_artifacts_count"] == len(run.patch.reused)
    assert report["reused_files_sample"] == list(run.patch.reused[:20])
    assert len(report["reused_files_sample"]) <= 20
    assert "written_files" not in report
    assert "deleted_files" not in report
    assert "reused_files" not in report
    assert report["generated_small_files"] == 0
    assert report["cover_files_read"] == 0
    assert report["cover_files_copied"] == 0


@pytest.mark.parametrize("relative", ["static/js/app.js", "static/css/site.css"])
def test_missing_frontend_asset_preserves_last_good_site(
    tmp_path: Path, relative: str
) -> None:
    builder, _ = _build_fixture(tmp_path)
    isolated_root = tmp_path / "project"
    shutil.copytree(ROOT / "static", isolated_root / "static")
    builder.root = isolated_root.resolve()
    builder.build()
    site = tmp_path / "dist" / "site"
    before = {
        path.relative_to(site).as_posix(): path.read_bytes()
        for path in site.rglob("*")
        if path.is_file()
    }
    (isolated_root / Path(relative)).unlink()

    with pytest.raises(BuildError, match="required frontend source asset"):
        builder.build()

    after = {
        path.relative_to(site).as_posix(): path.read_bytes()
        for path in site.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_unified_builder_has_no_frontend_fallback() -> None:
    source = (
        ROOT / "src" / "bgm_side_b" / "build" / "site_builder.py"
    ).read_text(encoding="utf-8")
    for stale in (
        "APP_JS",
        "APP_CSS_FALLBACK",
        "subject-card fallback",
        "detail-mount fallback",
        "#detail-mount",
    ):
        assert stale not in source


def test_rating_change_plans_only_the_subjects_owning_scopes(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_subject_facts(202)
    assert current is not None
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection,
            replace(current, subject=replace(current.subject, rating_score=9.1)),
        )
    run = builder.build()
    assert run.dirty.dirty_quarters == ("2026-07",)
    assert "2026-07/index.html" in run.patch.written
    assert "2026-04/index.html" not in run.patch.written
    assert run.patch.cover_files_read == 0


def test_missing_artifact_triggers_targeted_repair_without_cover_read(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    target = tmp_path / "dist" / "site" / "data" / "quarters" / "2026-07.json"
    target.unlink()
    run = builder.build()
    assert "data/quarters/2026-07.json" in run.patch.written
    assert run.patch.cover_files_read == 0


def test_corrupt_build_state_uses_full_safe_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    (tmp_path / "workspace" / "build-state.json").write_text("{bad", encoding="utf-8")
    original_rglob = Path.rglob
    scans: list[Path] = []

    def record_scan(path: Path, pattern: str):
        if path.resolve() == (tmp_path / "dist" / "site").resolve():
            scans.append(path)
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", record_scan)
    builder.build()
    assert scans


def test_cover_change_reads_and_copies_only_the_changed_cover(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    repository = SubjectRepository(database)
    current = repository.get_subject_facts(101)
    assert current is not None
    cover_bytes = b"new-cover-101"
    cover_path = tmp_path / "workspace" / "covers" / "101.webp"
    cover_path.write_bytes(cover_bytes)
    updated_cover = CoverRecord(
        "https://example.invalid/101-v2",
        "large",
        hashlib.sha256(cover_bytes).hexdigest(),
        1,
        1,
        len(cover_bytes),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, replace(current, cover=updated_cover)
        )
    run = builder.build()
    assert set(run.dirty.dirty_quarters) == {"2026-04", "2026-07"}
    assert "covers/101.webp" in run.patch.written
    assert run.patch.cover_files_read == 1
    assert run.patch.cover_files_copied == 1


def test_narrow_build_projects_only_requested_quarter_when_last_good_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    seen: list[tuple[str, ...]] = []
    original = builder._project_quarters

    def spy(facts: object, labels: tuple[str, ...]):
        seen.append(labels)
        return original(facts, labels)

    monkeypatch.setattr(builder, "_project_quarters", spy)
    builder.build(Quarter(2026, 7))
    assert seen == [("2026-07",)]
