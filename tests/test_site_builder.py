"""Integration checks for the unique offline site builder."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

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
    cover_bytes = b"cover-101"
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
    tags = load_tag_rules(
        ROOT / "config" / "allowed-tags.toml",
        ROOT / "config" / "tag-aliases.toml",
    )
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
    assert {"aliases", "display_summary", "premiere_quarter"} <= set(
        catalog["records"][0]
    )
    july = json.loads(
        (site / "data" / "quarters" / "2026-07.json").read_text("utf-8")
    )
    assert [item["subject_id"] for item in july["tv"]["continuing"]] == [101]
    assert [item["subject_id"] for item in july["movie"]["premiere"]] == [202]
    second = builder.build()
    assert second.patch.written == ()
    assert second.dirty.skipped_quarters == ("2026-04", "2026-07")
    assert first.report_path.is_file()


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
    assert not (site / "2026-07").exists()
    assert any("no last-known-good" in warning for warning in run.warnings)


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
