"""Integration checks for the unique offline site builder."""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from bgm_side_b.build import site_builder
from bgm_side_b.build.site_builder import (
    BuildError,
    UnifiedSiteBuilder,
    _quarter_html,
    _subject_row,
)
from bgm_side_b.build.site_projection import QuarterProjection, SubjectProjection
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


def _build_fixture(
    tmp_path: Path,
    *,
    primary_subject_id: int = 101,
    include_same_quarter_tv: bool = False,
    extra_same_quarter_tv: int = 0,
) -> tuple[UnifiedSiteBuilder, Database]:
    workspace = tmp_path / "workspace"
    database = Database(workspace / "data" / "archive.sqlite3")
    database.initialize()
    covers = workspace / "covers"
    covers.mkdir(parents=True)
    cover_bytes = _valid_cover_bytes()
    (covers / f"{primary_subject_id}.webp").write_bytes(cover_bytes)
    cover = CoverRecord(
        f"https://example.invalid/{primary_subject_id}",
        "large",
        hashlib.sha256(cover_bytes).hexdigest(),
        9,
        1,
        len(cover_bytes),
    )
    repository = SubjectRepository(database)
    april = _subject(
        primary_subject_id,
        MediaFormat.TV,
        Quarter(2026, 4),
        cover=cover,
        continuing=Quarter(2026, 7),
    )
    july_tv = _subject(303, MediaFormat.TV, Quarter(2026, 7))
    movie = _subject(202, MediaFormat.MOVIE, Quarter(2026, 7))
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, april)
        if include_same_quarter_tv:
            repository.replace_subject_snapshot(connection, july_tv)
        for subject_id in range(304, 304 + max(0, extra_same_quarter_tv)):
            repository.replace_subject_snapshot(
                connection,
                _subject(subject_id, MediaFormat.TV, Quarter(2026, 7)),
            )
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


def _set_preview_fixture_cover(
    tmp_path: Path, database: Database, subject_id: int, *, large: bool
) -> bytes:
    size = (800, 1200) if large else (200, 300)
    image = Image.new("RGB", size, (56, 75, 120))
    if large:
        noise = Image.frombytes(
            "RGB", size, random.Random(subject_id).randbytes(size[0] * size[1] * 3)
        )
        image = Image.blend(image, noise, 0.28)
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 40, 770, 170), fill="white")
        draw.text((50, 70), "SYNTHETIC ARCHIVE", fill="black")
    content = io.BytesIO()
    image.save(content, format="WEBP", quality=82, method=6)
    value = content.getvalue()
    (tmp_path / "workspace" / "covers" / f"{subject_id}.webp").write_bytes(value)
    repository = SubjectRepository(database)
    snapshot = repository.get_subject_facts(subject_id)
    assert snapshot is not None
    cover = CoverRecord(
        f"https://example.invalid/{subject_id}", "large",
        hashlib.sha256(value).hexdigest(), *size, len(value),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, replace(snapshot, cover=cover))
    return value


def _build_preview_fixture(tmp_path: Path) -> tuple[UnifiedSiteBuilder, Database]:
    builder, database = _build_fixture(
        tmp_path, include_same_quarter_tv=True, extra_same_quarter_tv=1
    )
    _set_preview_fixture_cover(tmp_path, database, 101, large=True)
    _set_preview_fixture_cover(tmp_path, database, 303, large=False)
    return builder, database


def test_preview_preserves_alpha_and_exif_orientation(tmp_path: Path) -> None:
    size = (800, 1200)
    image = Image.frombytes(
        "RGB", size, random.Random(52).randbytes(size[0] * size[1] * 3)
    ).convert("RGBA")
    image.putalpha(160)
    orientation = Image.Exif()
    orientation[274] = 6
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=82, exif=orientation)
    body = output.getvalue()
    source = tmp_path / "portrait.webp"
    source.write_bytes(body)
    preview = site_builder._cover_preview_bytes(
        source, hashlib.sha256(body).hexdigest(), len(body)
    )
    assert len(preview) < len(body)
    with Image.open(io.BytesIO(preview)) as decoded:
        assert decoded.size == (640, 427)
        assert decoded.getexif().get(274) is None
        assert decoded.getchannel("A").getextrema() == (160, 160)
    assert source.read_bytes() == body


@pytest.mark.parametrize("format_name", ["PNG", "WEBP"])
def test_preview_rejects_unverified_format_and_oversized_dimensions(
    tmp_path: Path, format_name: str,
) -> None:
    output = io.BytesIO()
    Image.new("RGB", (1201, 1)).save(output, format=format_name)
    body = output.getvalue()
    source = tmp_path / "invalid.webp"
    source.write_bytes(body)
    with pytest.raises(BuildError):
        site_builder._cover_preview_bytes(
            source, hashlib.sha256(body).hexdigest(), len(body)
        )


def test_cover_previews_include_originals_repair_and_reuse_without_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder, _ = _build_preview_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist/site"
    manifest = json.loads((site / "data/offline/2026-07.json").read_text("utf-8"))
    originals = [r for r in manifest["resources"] if re.fullmatch(
        r"covers/[0-9]+\.webp", r["url"]
    )]
    previews = [r for r in manifest["resources"] if "/previews/" in r["url"]]
    assert len(originals) == len(previews) == 2
    for resource in (*originals, *previews):
        body = (site / resource["url"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == resource["content_hash"]
        assert len(body) == resource["size_bytes"]
        with Image.open(io.BytesIO(body)) as image:
            image.load()
            assert image.format == "WEBP"
    quarter = json.loads((site / "data/quarters/2026-07.json").read_text("utf-8"))
    urls = {item["subject_id"]: item["cover_url"]
            for item in (*quarter["tv"]["premiere"], *quarter["tv"]["continuing"])}
    large = next(r for r in previews
                 if r["url"] == site_builder._cover_preview_path(urls[101]))
    small = next(r for r in previews
                 if r["url"] == site_builder._cover_preview_path(urls[303]))
    with Image.open(site / large["url"]) as image:
        assert image.size == (427, 640)
    assert (site / small["url"]).read_bytes() == (site / "covers/303.webp").read_bytes()
    page = (site / "2026-07/index.html").read_text("utf-8")
    assert f'../{large["url"]}' in page
    assert 'data-cover-fallback="../covers/101.webp?v=' in page
    assert 'aria-label="缺少封面"' in page
    (site / large["url"]).unlink()
    repaired = builder.build()
    assert repaired.patch.written == (large["url"],)
    assert hashlib.sha256((site / large["url"]).read_bytes()).hexdigest() == (
        large["content_hash"]
    )
    timestamps = {p.relative_to(site): p.stat().st_mtime_ns for p in site.rglob("*")
                  if p.is_file()}
    def forbidden(*args: object) -> bytes:
        raise AssertionError("unchanged cover was regenerated")
    monkeypatch.setattr(site_builder, "_cover_preview_bytes", forbidden)
    original_open = Path.open
    def checked_open(path: Path, *args: object, **kwargs: object):
        if path.parent == tmp_path / "workspace/covers":
            raise AssertionError("no-op read a canonical cover")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    unchanged = builder.build()
    assert unchanged.patch.written == unchanged.patch.deleted == ()
    assert timestamps == {p.relative_to(site): p.stat().st_mtime_ns
                          for p in site.rglob("*") if p.is_file()}


def test_preview_rule_changes_package_not_data_and_keeps_retained_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder, database = _build_preview_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist/site"
    old_package = json.loads((site / "data/offline/2026-07.json").read_text("utf-8"))
    old_april = (site / "2026-04/index.html").read_bytes()
    old_data = (site / "data/quarters/2026-07.json").read_bytes()
    old_quarter = json.loads(old_data)
    original_url = old_quarter["tv"]["continuing"][0]["cover_url"]
    old_preview = site_builder._cover_preview_path(original_url)
    repository = SubjectRepository(database)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        repository.write_sync_state(connection, QuarterSyncState(
            Quarter(2026, 4), "incomplete", "complete", 1, 0, now, now,
        ))
    monkeypatch.setitem(site_builder._COVER_PREVIEW_RULE, "quality", 79)
    run = builder.build()
    package = json.loads((site / "data/offline/2026-07.json").read_text("utf-8"))
    new_preview = site_builder._cover_preview_path(original_url)
    assert package["data_revision"] == old_package["data_revision"]
    assert package["revision"] != old_package["revision"]
    assert new_preview != old_preview
    assert (site / old_preview).is_file() and (site / new_preview).is_file()
    assert (site / "2026-04/index.html").read_bytes() == old_april
    assert (site / "data/quarters/2026-07.json").read_bytes() == old_data
    assert json.loads(run.report_path.read_text("utf-8"))["retained_quarters"] == [
        "2026-04"
    ]
    retained = json.loads((site / "data/offline/2026-04.json").read_text("utf-8"))
    for resource in retained["resources"]:
        body = (site / resource["url"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == resource["content_hash"]
        assert len(body) == resource["size_bytes"]


@pytest.mark.parametrize("failure", ["omitted-resource", "bad-preview", "bad-source"])
def test_preview_failure_never_replaces_last_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    builder, _ = _build_preview_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist/site"
    previous = {p.relative_to(site): p.read_bytes() for p in site.rglob("*")
                if p.is_file()}
    state = builder.state_path.read_bytes()
    monkeypatch.setitem(site_builder._COVER_PREVIEW_RULE, "quality", 79)
    if failure == "omitted-resource":
        original = site_builder._offline_manifest_bytes
        def omitted(*args: object) -> bytes:
            manifest = json.loads(original(*args))
            manifest["resources"] = [r for r in manifest["resources"]
                                     if "/previews/" not in r["url"]]
            return site_builder.json_bytes(manifest)
        monkeypatch.setattr(site_builder, "_offline_manifest_bytes", omitted)
    elif failure == "bad-preview":
        monkeypatch.setattr(site_builder, "_cover_preview_bytes", lambda *args: b"bad")
    else:
        cover = tmp_path / "workspace/covers/101.webp"
        cover.write_bytes(b"X" * cover.stat().st_size)
    with pytest.raises(BuildError):
        builder.build()
    assert previous == {p.relative_to(site): p.read_bytes()
                        for p in site.rglob("*") if p.is_file()}
    assert builder.state_path.read_bytes() == state


def test_retained_cover_collision_fails_before_site_mutation(tmp_path: Path) -> None:
    builder, database = _build_preview_fixture(tmp_path)
    builder.build()
    site = tmp_path / "dist/site"
    previous = {p.relative_to(site): p.read_bytes() for p in site.rglob("*")
                if p.is_file()}
    state = builder.state_path.read_bytes()
    cover = tmp_path / "workspace/covers/101.webp"
    image = Image.new("RGB", (800, 1200), "green")
    body = io.BytesIO()
    image.save(body, format="WEBP", quality=82)
    value = body.getvalue()
    cover.write_bytes(value)
    repository = SubjectRepository(database)
    snapshot = repository.get_subject_facts(101)
    assert snapshot is not None
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, replace(snapshot, cover=CoverRecord(
                "https://example.invalid/101", "large",
                hashlib.sha256(value).hexdigest(), 800, 1200, len(value),
            )),
        )
        repository.write_sync_state(connection, QuarterSyncState(
            Quarter(2026, 4), "incomplete", "complete", 1, 0, now, now,
        ))
    with pytest.raises(BuildError, match="retained cover conflicts"):
        builder.build()
    assert builder.state_path.read_bytes() == state
    assert previous == {p.relative_to(site): p.read_bytes()
                        for p in site.rglob("*") if p.is_file()}


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
    for page in (
        "index.html",
        "2026-07/index.html",
        "archive/index.html",
        "settings/index.html",
    ):
        page_html = (site / page).read_text("utf-8")
        assert '<a class="skip-link" href="#main-content">' in page_html
        assert '<main id="main-content" tabindex="-1"' in page_html
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
    july_html = (site / "2026-07" / "index.html").read_text("utf-8")
    settings_html = (site / "settings" / "index.html").read_text("utf-8")
    assert "Bangumi Side B · MyKr" in july_html
    assert "2026-07 · Bangumi Side B｜MyKr" in july_html
    bootstrap_index = july_html.index('localStorage.getItem("bsb-browse-view-mode")')
    assert bootstrap_index < july_html.index('<link rel="stylesheet"')
    assert "Bangumi Side B｜MyKr" in settings_html
    assert 'id="settings-app-title"' in settings_html
    assert "<dd>MyKr</dd>" in settings_html
    cover_url = july["tv"]["continuing"][0]["cover_url"]
    preview_path = site_builder._cover_preview_path(cover_url)
    assert preview_path is not None
    assert f'src="../{preview_path}"' in july_html
    assert f'data-cover-fallback="../{cover_url}"' in july_html
    assert july_html.count('data-list-section="tv"') == 1
    assert july_html.count('data-list-section="movie"') == 1
    assert 'data-appearance-section="continuing"' not in july_html
    assert 'data-appearance-badge="continuing">续播</span>' in july_html
    assert 'data-quarter-prev href="../2026-04/index.html"' in july_html
    assert 'data-quarter-next aria-disabled="true"' in july_html
    assert 'data-quarter-option="2026-04"' in july_html
    assert 'data-quarter-option="2026-07"' in july_html
    assert 'data-quarter-option="2026-01"' in july_html
    assert 'data-mobile-menu-toggle' in july_html
    assert 'data-mobile-menu ' in july_html
    assert 'data-menu-open="false"' in july_html
    assert 'popover="auto"' in july_html
    assert july_html.count(">档案</a>") == 2
    assert ">Archive</a>" not in july_html
    assert 'data-mobile-quarter-offline' in july_html
    assert 'data-quarter-offline' not in july_html
    assert 'viewport-fit=cover' in july_html
    assert 'data-view-mode="grid"' in july_html
    assert 'aria-label="结果视图"' in july_html
    assert 'data-view-mode="grid"' in archive_html
    assert 'aria-label="结果视图"' in archive_html
    css_files = list((site / "assets").glob("app.css"))
    assert css_files
    css = css_files[0].read_text("utf-8")
    assert "@view-transition" not in css
    assert "touch-action: manipulation" in css
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
    assert cover_resource["size_bytes"] == len(_valid_cover_bytes())
    preview_resource = next(
        item for item in offline["resources"] if item["url"] == preview_path
    )
    assert preview_resource["content_hash"] == cover_resource["content_hash"]
    assert preview_resource["size_bytes"] == cover_resource["size_bytes"]
    second = builder.build()
    assert second.patch.written == ()
    assert second.dirty.skipped_quarters == ("2026-04", "2026-07")
    assert first.report_path.is_file()


def test_subject_row_keeps_missing_score_missing_instead_of_zero() -> None:
    row = _subject_row(
        {
            "id": 999,
            "preferred_title": "No rating",
            "media": "TV",
            "appearance": "premiere",
            "score": None,
            "rating_count": None,
        },
        1,
    )

    assert 'data-score=""' in row
    assert "<b>—</b>" in row
    assert "0.0" not in row


@pytest.mark.parametrize(
    ("media", "source", "air_date", "grid_date"),
    [
        ("MOVIE", "视觉小说", "2026-07-02", "26-07-02"),
        ("TV", "轻小说", "2026-07-14", "26-07-14"),
    ],
)
def test_subject_row_exposes_compact_archive_grid_metadata(
    media: str, source: str, air_date: str, grid_date: str
) -> None:
    row = _subject_row(
        {
            "id": 999,
            "preferred_title": "Compact metadata",
            "media": media,
            "appearance": "premiere",
            "quarter": "2026-07",
            "air_date": air_date,
            "source": source,
        },
        1,
    )

    assert (
        f'<span class="subject-row__meta-full">{media} · {air_date} · '
        f'{source} · 2026-07</span>'
    ) in row
    assert (
        f'<span class="subject-row__meta-grid" aria-hidden="true">{media} · '
        f'{grid_date} · {source} · 2026-07</span>'
    ) in row


def test_subject_row_exposes_compact_quarter_grid_date() -> None:
    quarter = SubjectProjection(
        101,
        "中文 101",
        "Original 101",
        (),
        "TV",
        12,
        "2026-07-02",
        None,
        8.0,
        10,
        "原创",
        (),
        None,
        None,
        None,
        "premiere",
        "2026-07",
        None,
        "https://bgm.tv/subject/101",
    )
    row = _subject_row(quarter, 1)

    assert 'class="subject-row__meta-full">TV · 12话 · 2026-07-02 · 原创' in row
    assert (
        'class="subject-row__meta-grid" aria-hidden="true">'
        "TV · 12话 · 07-02 · 原创" in row
    )


def test_bgm_571784_episode_count_survives_projection_and_site_build(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path, primary_subject_id=571784)

    builder.build()

    quarter = json.loads(
        (tmp_path / "dist" / "site" / "data" / "quarters" / "2026-07.json").read_text(
            encoding="utf-8"
        )
    )
    records = quarter["tv"]["continuing"]
    record = next(item for item in records if item["subject_id"] == 571784)
    assert record["episode_count"] == 12


def test_build_rejects_malformed_changelog_instead_of_dropping_release_data(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 0.2 (bad)\n", encoding="utf-8")
    builder.changelog_path = changelog

    with pytest.raises(BuildError, match="malformed release heading"):
        builder.build()


def test_settings_embeds_escaped_changelog_with_release_defaults(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.changelog_path = tmp_path / "CHANGELOG.md"
    builder.changelog_path.write_text(
        """# Changelog

## 0.6.3 - 2026-08-23

### 修复

- <b>escaped</b>
- current release

## 0.6.2 - 2026-08-23

### 修复

- current patch

## 0.6.0 - 2026-08-22

- milestone

## 0.3.0 - 2026-08-19

### 新增

- current

## 0.1.0 - 2026-07-29

- history
""",
        encoding="utf-8",
    )
    builder.build()

    page = (tmp_path / "dist" / "site" / "settings" / "index.html").read_text(
        "utf-8"
    )
    assert "06 / CHANGELOG" in page
    assert "当前程序版本</dt><dd>0.8.5" in page
    assert 'data-changelog-release="0.6.3"' in page
    assert '<details class="settings-changelog__release"' not in page
    assert 'data-changelog-release="0.6.2"' in page
    assert 'data-changelog-milestone="0.6"' in page
    assert 'data-changelog-milestone="0.6" open' not in page
    assert '<time datetime="2026-08-22">2026-08-22</time>' in page
    assert 'data-changelog-release="0.3.0"' in page
    child = page.split('data-changelog-milestone="0.6"', 1)[1]
    assert 'data-changelog-release="0.6.0"' in child
    assert '<details class="settings-changelog__release"' not in child
    assert 'data-changelog-release="0.1.0"' in page
    assert 'data-changelog-release="0.1.0" open' not in page
    assert "&lt;b&gt;escaped&lt;/b&gt;" in page


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

    def package_state() -> tuple[str, str, str, bytes]:
        package = json.loads(
            (site / "data" / "offline" / "2026-07.json").read_text("utf-8")
        )
        shell = json.loads((site / "data" / "pwa-shell.json").read_text("utf-8"))
        data = (site / "data" / "quarters" / "2026-07.json").read_bytes()
        assert package["data_revision"] == hashlib.sha256(data).hexdigest()
        return (
            package["revision"],
            package["data_revision"],
            shell["revision"],
            (site / "sw.js").read_bytes(),
        )

    initial = package_state()
    css = isolated_root / "static" / "css" / "site.css"
    css.write_text(
        css.read_text("utf-8") + "\n/* package revision */\n", encoding="utf-8"
    )
    builder.build()
    after_css = package_state()
    assert after_css[0] != initial[0]
    assert after_css[1] == initial[1]

    js = isolated_root / "static" / "js" / "app.js"
    js.write_text(js.read_text("utf-8") + "\n// package revision\n", encoding="utf-8")
    builder.build()
    after_js = package_state()
    assert after_js[0] != after_css[0]
    assert after_js[1] == initial[1]

    sw = isolated_root / "static" / "pwa" / "sw.js"
    sw.write_text(
        sw.read_text("utf-8") + "\n// worker-only revision\n", encoding="utf-8"
    )
    builder.build()
    after_sw = package_state()
    assert after_sw[0] == after_js[0]
    assert after_sw[1] == after_js[1]
    assert after_sw[2] == after_js[2]
    assert after_sw[3] != after_js[3]

    icon = isolated_root / "static" / "icons" / "pwa-192.png"
    icon.write_bytes(icon.read_bytes() + b"icon-only")
    builder.build()
    after_icon = package_state()
    assert after_icon[0] == after_sw[0]
    assert after_icon[1] == after_sw[1]


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
    assert 'data-pwa-update-notice hidden aria-live="polite"' in page
    assert 'data-quarter-update-status role="status"' in page
    quarter_data = (
        tmp_path / "dist" / "site" / "data" / "quarters" / "2026-07.json"
    ).read_bytes()
    data_revision = hashlib.sha256(quarter_data).hexdigest()
    assert (
        f'data-data-url="../data/quarters/2026-07.json?v={data_revision}"'
        in page
    )
    offline_manifest = json.loads(
        (tmp_path / "dist" / "site" / "data" / "offline" / "2026-07.json")
        .read_text("utf-8")
    )
    manifest_data = next(
        item for item in offline_manifest["resources"]
        if item["url"] == "data/quarters/2026-07.json"
    )
    assert (
        offline_manifest["data_revision"]
        == manifest_data["content_hash"]
        == data_revision
    )
    assert 'class="subject-row"' in page
    assert 'class="workspace"' in page
    assert 'data-detail-panel' in page
    assert 'data-media="movie"' in page
    assert 'class="subject-card"' not in page
    assert 'id="subject-drawer"' not in page


def test_archive_output_starts_with_a_noninteractive_pending_browser_shell(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    page = (tmp_path / "dist" / "site" / "archive" / "index.html").read_text(
        "utf-8"
    )
    assert 'data-page="archive"' in page
    assert 'aria-busy="true"' in page
    assert 'data-scope-choice="year" aria-selected="true"' in page
    assert 'data-scope-choice="quarter" aria-selected="false" tabindex="-1"' in page
    assert 'data-archive-quarter-selector role="tabpanel" hidden' in page
    assert 'data-archive-year-selector role="tabpanel"' in page
    assert 'data-archive-browser aria-busy="true"' in page
    assert 'aria-label="Archive results" aria-busy="true" inert' in page
    assert 'data-archive-pending aria-hidden="true" inert' in page
    pending = page.split('data-archive-pending', 1)[1].split("</div>", 1)[0]
    assert "data-record-key" not in pending
    assert "data-subject-id" not in pending
    assert 'data-range-apply disabled' in page


def test_quarter_static_rows_match_default_runtime_order() -> None:
    def record(
        subject_id: int,
        score: float | None,
        rating_count: int | None,
        air_date: str | None,
        *,
        appearance: str = "premiere",
        media: str = "TV",
    ) -> SubjectProjection:
        return SubjectProjection(
            subject_id=subject_id,
            preferred_title=f"Subject {subject_id}",
            original_title=None,
            aliases=(),
            media_format=media,
            episode_count=None,
            air_date=air_date,
            end_date=None,
            rating_score=score,
            rating_count=rating_count,
            source="source",
            allowed_tags=(),
            display_summary=None,
            cover_url=None,
            cover_hash=None,
            appearance_kind=appearance,
            quarter="2026-07",
            premiere_quarter=None,
            bangumi_url=f"https://bgm.tv/subject/{subject_id}",
        )

    quarter = QuarterProjection(
        "2026-07",
        tv_premiere=(
            record(1, 8.0, 10, "2026-07-03"),
            record(3, 7.0, 5, "2026-07-05"),
            record(5, None, 500, None),
        ),
        tv_continuing=(
            record(2, 8.0, 20, "2026-07-04", appearance="continuing"),
            record(4, 7.0, 5, "2026-07-02", appearance="continuing"),
        ),
        movie_premiere=(
            record(7, None, None, None, media="MOVIE"),
            record(6, 7.5, 1, "2026-07-01", media="MOVIE"),
        ),
    )
    revisions = {
        "assets/app.css": "css",
        "assets/app.js": "js",
        "assets/pwa.js": "pwa",
        "manifest.webmanifest": "manifest",
        "icons/favicon.svg": "favicon",
    }
    page = _quarter_html(quarter, revisions).decode("utf-8")
    tv_section = page.split('data-list-section="tv"', 1)[1].split(
        'data-list-section="movie"', 1
    )[0]
    movie_section = page.split('data-list-section="movie"', 1)[1]
    tv_keys = re.findall(r'data-record-key="([^"]+)"', tv_section)
    movie_keys = re.findall(r'data-record-key="([^"]+)"', movie_section)
    assert tv_keys == [
        "2@2026-07@continuing",
        "1@2026-07@premiere",
        "4@2026-07@continuing",
        "3@2026-07@premiere",
        "5@2026-07@premiere",
    ]
    assert movie_keys == [
        "6@2026-07@premiere",
        "7@2026-07@premiere",
    ]


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
    content = io.BytesIO()
    Image.new("RGB", (9, 1), "green").save(content, format="WEBP", quality=82)
    cover_bytes = content.getvalue()
    cover_path = tmp_path / "workspace" / "covers" / "101.webp"
    cover_path.write_bytes(cover_bytes)
    updated_cover = CoverRecord(
        "https://example.invalid/101-v2",
        "large",
        hashlib.sha256(cover_bytes).hexdigest(),
        9,
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
