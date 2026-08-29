"""Atomic clean-schema discovery, fact sync, cover, and recovery coverage."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import bgm_side_b.sync as sync_module
from bgm_side_b.admission import (
    MOVIE_DATE_UNRESOLVED,
    TV_QUARTER_DATE_UNRESOLVED,
    QuarterOverride,
)
from bgm_side_b.api import ApiTag, BangumiApiError, ImageResponse, SubjectDetail
from bgm_side_b.archive_config import (
    ArchiveSyncSettings,
    add_auto_excluded_subject,
    load_archive_source_rules,
    load_archive_sync_settings,
)
from bgm_side_b.database import Database
from bgm_side_b.discovery import (
    DiscoveredSubject,
    DiscoveryBatch,
    DiscoveryFailure,
    DiscoverySource,
)
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
    QuarterAppearanceKind,
    QuarterAssignmentSource,
)
from bgm_side_b.japanese_overrides import (
    JapaneseOverride,
    save_japanese_overrides,
)
from bgm_side_b.media import MAX_COVER_CONCURRENCY, CoverResult
from bgm_side_b.repository import (
    QuarterAppearance,
    QuarterSyncState,
    ReviewIssue,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)
from bgm_side_b.sync import (
    COVERS_COMPLETE,
    FACTS_COMPLETE,
    FACTS_INCOMPLETE,
    ArchiveSynchronizer,
    SyncError,
    SyncScope,
    parse_sync_scope,
)

ROOT = Path(__file__).resolve().parents[1]
QUARTER = Quarter(2026, 4)
AUTO_EXCLUSION_CORPUS = json.loads(
    (ROOT / "tests" / "fixtures" / "auto_exclusion_lifecycle.json").read_text(
        encoding="utf-8"
    )
)


class FakeDiscovery:
    def __init__(self, batch: DiscoveryBatch) -> None:
        self.batch = batch
        self.calls: list[Quarter] = []

    def discover(self, quarter: Quarter) -> DiscoveryBatch:
        self.calls.append(quarter)
        return self.batch


class FakeApi:
    def __init__(
        self,
        details: dict[int, SubjectDetail],
        *,
        failures: frozenset[int] = frozenset(),
        interrupts: frozenset[int] = frozenset(),
        episode_airdates: dict[int, tuple[date, ...]] | None = None,
        episode_failures: frozenset[int] = frozenset(),
        image_failure: bool = False,
    ) -> None:
        self.details = details
        self.failures = failures
        self.interrupts = interrupts
        self.episode_airdates = episode_airdates or {}
        self.episode_failures = episode_failures
        self.image_failure = image_failure
        self.subject_calls: list[int] = []
        self.episode_calls: list[int] = []
        self.image_calls: list[str] = []

    def get_subject(self, subject_id: int) -> SubjectDetail:
        self.subject_calls.append(subject_id)
        if subject_id in self.interrupts:
            raise KeyboardInterrupt
        if subject_id in self.failures:
            raise BangumiApiError("timeout", "request timed out")
        return self.details[subject_id]

    def get_main_episode_airdates(self, subject_id: int) -> tuple[date, ...]:
        self.episode_calls.append(subject_id)
        if subject_id in self.episode_failures:
            raise BangumiApiError("timeout", "request timed out")
        return self.episode_airdates.get(subject_id, ())

    def fetch_image(self, url: str, *, max_bytes: int) -> ImageResponse:
        self.image_calls.append(url)
        if self.image_failure:
            raise BangumiApiError("image_network", "network request failed")
        content = _png(1500, 600)
        assert len(content) <= max_bytes
        return ImageResponse(content, "image/png", url)


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(1, 2, 3)).save(output, format="PNG")
    return output.getvalue()


def _detail(
    subject_id: int,
    *,
    media: MediaFormat = MediaFormat.TV,
    country: str | None = "日本",
    air_date: str | None = "2026-04-02",
    cover: str | None = "https://images.example/cover.png",
    platform: str | None = None,
    meta_tags: tuple[str, ...] = (),
    rating_count: int | None = 100,
    other: str | None = None,
) -> SubjectDetail:
    infobox = [] if country is None else [{"key": "国家/地区", "value": country}]
    if other is not None:
        infobox.append({"key": "其他", "value": other})
    return SubjectDetail.from_payload(
        {
            "id": subject_id,
            "type": 2,
            "name": f"Original {subject_id}",
            "name_cn": f"中文 {subject_id}",
            "summary": "raw summary",
            "date": air_date,
            "platform": (
                platform
                if platform is not None
                else "TV" if media is MediaFormat.TV else "剧场版"
            ),
            "eps": 12,
            "total_episodes": 12,
            "rating": {"score": 7.5, "total": rating_count},
            "meta_tags": list(meta_tags),
            "tags": [{"name": "漫画改编", "count": 3}],
            "infobox": infobox,
            "images": {"large": cover} if cover else {},
        }
    )


def _candidate(
    subject_id: int,
    media: MediaFormat,
    value: date = date(2026, 4, 2),
) -> DiscoveredSubject:
    return DiscoveredSubject(
        subject_id,
        frozenset({media}),
        frozenset({value}),
        frozenset({2}),
        (f"browse:{media.value}:2026-04",),
    )


def _sync(
    tmp_path: Path,
    api: FakeApi,
    browse: DiscoveryBatch,
    search: DiscoveryBatch | None = None,
    settings: ArchiveSyncSettings | None = None,
    settings_path: Path | None = None,
    evaluation_date: date | None = None,
) -> tuple[ArchiveSynchronizer, SubjectRepository]:
    database = Database(tmp_path / "data" / "facts.sqlite3")
    repository = SubjectRepository(database)
    effective_settings = (
        settings
        if settings is not None
        else replace(
            load_archive_sync_settings(ROOT / "config" / "bangumi.toml"),
            auto_excluded_subject_ids=frozenset(),
        )
    )
    synchronizer = ArchiveSynchronizer(
        repository,
        api,  # type: ignore[arg-type]
        effective_settings,
        load_archive_source_rules(ROOT / "config" / "source-rules.toml"),
        overrides_path=tmp_path / "quarter-overrides.toml",
        workspace_directory=tmp_path,
        reports_directory=tmp_path / "reports",
        settings_path=settings_path,
        evaluation_date=evaluation_date,
        browse=FakeDiscovery(browse),
        search=FakeDiscovery(search or DiscoveryBatch(())),
    )
    return synchronizer, repository


def _store_existing(
    repository: SubjectRepository,
    subject_id: int,
    *,
    media: MediaFormat = MediaFormat.TV,
    air_date: date = date(2026, 4, 2),
    end_date: date | None = None,
    episode_count: int | None = None,
    premiere_quarter: Quarter | None = QUARTER,
    continuing: tuple[QuarterAppearance, ...] = (),
    tags: tuple[str, ...] = (),
    review_issues: tuple[ReviewIssue, ...] = (),
) -> None:
    repository.database.initialize()
    snapshot = SubjectSnapshot(
        SubjectRecord(
            subject_id,
            "Existing",
            None,
            None,
            media,
            air_date,
            end_date,
            episode_count,
            None,
            None,
            JapaneseDecision(
                JapaneseClassification.ACCEPTED_JAPANESE,
                "infobox_country",
                '["日本"]',
            ),
        ),
        tags=tags,
        premiere=(
            None
            if premiere_quarter is None
            else QuarterAppearance(
                premiere_quarter,
                QuarterAppearanceKind.PREMIERE,
                QuarterAssignmentSource.AUTOMATIC,
                "air_date",
                air_date.isoformat(),
            )
        ),
        continuing=continuing,
        review_issues=review_issues,
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)


def _auto_settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "bangumi.toml"
    source = (ROOT / "config" / "bangumi.toml").read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    manual_start = next(
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("excluded_subject_ids")
    )
    manual_end = manual_start + 1
    if "[" in lines[manual_start] and "]" not in lines[manual_start]:
        while "]" not in lines[manual_end - 1]:
            manual_end += 1
    lines[manual_start:manual_end] = ["excluded_subject_ids = []\n"]
    start = next(
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("auto_excluded_subject_ids")
    )
    end = start
    depth = 0
    while end < len(lines):
        depth += lines[end].split("#", 1)[0].count("[")
        depth -= lines[end].split("#", 1)[0].count("]")
        end += 1
        if depth <= 0:
            break
    lines[start:end] = ["auto_excluded_subject_ids = []\n"]
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_blacklist_source_counts_manual_hit_explicitly(
    tmp_path: Path,
) -> None:
    settings = replace(
        load_archive_sync_settings(ROOT / "config" / "bangumi.toml"),
        excluded_subject_ids=frozenset({650000}),
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi({}),
        DiscoveryBatch((_candidate(650000, MediaFormat.TV),)),
        settings=settings,
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert result.blacklisted == 1
    assert result.manual_blacklisted == 1
    assert result.existing_auto_blacklisted == 0
    assert result.auto_blacklisted == ()
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["manual_blacklisted"] == 1
    assert report["existing_auto_blacklisted"] == 0
    assert report["auto_blacklisted_count"] == 0
    assert repository.get_subject_facts(650000) is None


def test_auto_blacklist_stops_before_review_and_cover_download(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {
            650001: _detail(
                650001,
                air_date="2026-04-02",
                rating_count=29,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650001, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert result.blacklisted == 1
    assert result.manual_blacklisted == 0
    assert result.existing_auto_blacklisted == 0
    assert len(result.auto_blacklisted) == 1
    assert result.reviews == ()
    assert api.image_calls == []
    assert repository.get_subject_facts(650001) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({650001})
    )
    event = result.auto_blacklisted[0]
    assert event["days_since_air_date"] == 9
    assert event["rating_count"] == 29
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["auto_blacklisted_count"] == 1
    assert report["manual_blacklisted"] == 0
    assert report["quarters"][0]["auto_blacklisted"][0]["reason"] == (
        "low_rating_count"
    )


@pytest.mark.parametrize(
    ("days", "rating_count"),
    ((7, 0), (8, 30)),
)
def test_auto_blacklist_protection_and_rating_threshold_keep_subject(
    tmp_path: Path, days: int, rating_count: int
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    evaluation_date = date(2026, 4, 11)
    air_date = evaluation_date.fromordinal(evaluation_date.toordinal() - days)
    api = FakeApi(
        {
            650002: _detail(
                650002,
                air_date=air_date.isoformat(),
                rating_count=rating_count,
                cover=None,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650002, MediaFormat.TV, air_date),)),
        settings_path=settings_path,
        evaluation_date=evaluation_date,
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].auto_blacklisted == ()
    assert repository.get_subject_facts(650002) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_auto_blacklist_keeps_rule_a_strict_and_rule_b_immediate(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    details = {
        650003: _detail(650003, air_date=None, rating_count=0, cover=None),
        650004: _detail(650004, air_date="2026-04-02", rating_count=None, cover=None),
    }
    sync, repository = _sync(
        tmp_path,
        FakeApi(details),
        DiscoveryBatch(
            (
                _candidate(650003, MediaFormat.TV),
                _candidate(650004, MediaFormat.TV),
            )
        ),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    result = run.quarters[0]
    assert [item["subject_id"] for item in result.auto_blacklisted] == [650003]
    assert result.auto_blacklisted[0]["reason"] == (
        "insufficient_airing_information"
    )
    assert "rating_count" not in result.auto_blacklisted[0]
    assert repository.get_subject_facts(650003) is None
    assert repository.get_subject_facts(650004) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({650003})
    )


@pytest.mark.parametrize(
    ("subject_id", "air_date", "episode_count"),
    (
        (565802, date(2025, 12, 31), 2),
        (506120, date(2025, 12, 25), 1),
    ),
)
def test_short_boundary_tv_is_kept_in_natural_quarter_without_blacklisting(
    tmp_path: Path,
    subject_id: int,
    air_date: date,
    episode_count: int,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    detail = replace(
        _detail(subject_id, air_date=air_date.isoformat(), cover=None),
        eps=episode_count,
    )
    api = FakeApi({subject_id: detail})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV, air_date),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 1, 12),
    )

    result = sync.run(
        SyncScope(Quarter(2026, 1), Quarter(2026, 1))
    ).quarters[0]

    facts = repository.get_subject_facts(subject_id)
    assert result.auto_blacklisted == ()
    assert result.reviews == ()
    assert facts is not None and facts.premiere is not None
    assert facts.premiere.quarter == Quarter(2025, 10)
    assert facts.premiere.evidence_type == "air_date"
    assert api.episode_calls == []
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_boundary_tag_alone_is_reviewed_and_not_auto_blacklisted(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650040
    stable_id = 650042
    detail = replace(
        _detail(subject_id, air_date="2025-12-28", cover=None),
        tags=(ApiTag("2026年1月", 448), ApiTag("TV", 500)),
    )
    stable_date = date(2026, 1, 2)
    api = FakeApi(
        {
            subject_id: detail,
            stable_id: _detail(
                stable_id, air_date=stable_date.isoformat(), cover=None
            ),
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (
                _candidate(subject_id, MediaFormat.TV, date(2025, 12, 28)),
                _candidate(stable_id, MediaFormat.TV, stable_date),
            )
        ),
        settings_path=settings_path,
        evaluation_date=date(2026, 1, 12),
    )

    result = sync.run(
        SyncScope(Quarter(2026, 1), Quarter(2026, 1))
    ).quarters[0]

    facts = repository.get_subject_facts(subject_id)
    assert result.auto_blacklisted == ()
    assert result.reviews[0].issue_code == "TV_QUARTER_BOUNDARY"
    assert facts is not None and facts.premiere is None
    assert api.episode_calls == [subject_id]
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_proven_multi_week_boundary_tv_can_use_narrow_next_quarter_exception(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650041
    detail = replace(
        _detail(subject_id, air_date="2025-12-28", cover=None),
        tags=(ApiTag("2026年1月", 448), ApiTag("TV", 500)),
    )
    api = FakeApi(
        {subject_id: detail},
        episode_airdates={
            subject_id: (
                date(2025, 12, 28),
                date(2026, 1, 4),
                date(2026, 1, 11),
            )
        },
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (_candidate(subject_id, MediaFormat.TV, date(2025, 12, 28)),)
        ),
        settings_path=settings_path,
        evaluation_date=date(2026, 1, 12),
    )

    result = sync.run(
        SyncScope(Quarter(2026, 1), Quarter(2026, 1))
    ).quarters[0]

    facts = repository.get_subject_facts(subject_id)
    assert result.auto_blacklisted == ()
    assert result.reviews == ()
    assert facts is not None and facts.premiere is not None
    assert facts.premiere.quarter == Quarter(2026, 1)
    assert (
        facts.premiere.evidence_type
        == "community_quarter_tag_and_main_episode_airdates"
    )
    assert result.early_premieres[0]["subject_id"] == subject_id
    assert api.episode_calls == [subject_id]


def test_unresolved_movie_review_is_auto_blacklisted_immediately_and_cleaned(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {
            659091: _detail(
                659091,
                media=MediaFormat.MOVIE,
                air_date=None,
                rating_count=None,
                cover=None,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(659091, MediaFormat.MOVIE),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 1),
    )
    _store_existing(
        repository,
        659091,
        media=MediaFormat.MOVIE,
        review_issues=(
            ReviewIssue(
                MOVIE_DATE_UNRESOLVED,
                QUARTER,
                None,
                {"subject_id": 659091},
                "old",
            ),
        ),
    )
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    (covers / "659091.webp").write_bytes(b"old cover")

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert result.reviews == ()
    assert result.external_reviews == ()
    assert result.auto_blacklisted[0]["subject_id"] == 659091
    assert result.auto_blacklisted[0]["reason"] == (
        "insufficient_airing_information"
    )
    assert result.auto_blacklisted[0]["issue_code"] == MOVIE_DATE_UNRESOLVED
    assert result.auto_blacklisted[0]["target_quarter"] == "2026-04"
    assert "rating_count" not in result.auto_blacklisted[0]
    assert "rating_threshold" not in result.auto_blacklisted[0]
    assert "rating_missing" not in result.auto_blacklisted[0]
    assert "quarter_end" not in result.auto_blacklisted[0]
    assert "days_after_quarter_end" not in result.auto_blacklisted[0]
    assert "protection_days" not in result.auto_blacklisted[0]
    assert repository.get_subject_facts(659091) is None
    assert not (covers / "659091.webp").exists()
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({659091})
    )


def test_movie_search_spillover_is_irrelevant_without_review_or_assignment(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    target = Quarter(2026, 1)
    subject_id = 650050
    detail = _detail(
        subject_id,
        media=MediaFormat.MOVIE,
        air_date="2026-06-28",
        country=None,
        cover=None,
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi({subject_id: detail}),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.MOVIE, date(2026, 6, 28)),)),
        settings_path=settings_path,
    )
    _store_existing(
        repository,
        subject_id,
        media=MediaFormat.MOVIE,
        air_date=date(2026, 6, 28),
        premiere_quarter=target,
    )
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    (covers / f"{subject_id}.webp").write_bytes(b"old cover")

    run = sync.run(SyncScope(target, target))
    result = run.quarters[0]

    assert result.irrelevant_candidates == 1
    assert result.reviews == ()
    assert result.external_reviews == ()
    assert result.rejected_non_japanese == 0
    assert repository.get_subject_facts(subject_id) is None
    assert not (covers / f"{subject_id}.webp").exists()
    report = json.loads(run.report_path.read_text("utf-8"))
    assert report["quarters"][0]["irrelevant_candidates"] == 1


def test_stale_out_of_scope_movie_review_is_cleared_but_natural_premiere_survives(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    subject_id = 650053
    review = ReviewIssue(
        "DISCOVERY_DATE_MISMATCH",
        target,
        "2026-06-28",
        {"provenance": ["search:air_date:2026-06-24..2026-10-01"]},
        "old",
    )
    sync, repository = _sync(tmp_path, FakeApi({}), DiscoveryBatch(()))
    _store_existing(
        repository,
        subject_id,
        media=MediaFormat.MOVIE,
        air_date=date(2026, 6, 28),
        premiere_quarter=Quarter(2026, 4),
        review_issues=(review,),
    )

    run = sync.run(SyncScope(target, target))

    facts = repository.get_subject_facts(subject_id)
    assert run.exit_code == 0
    assert facts is not None
    assert facts.premiere is not None
    assert facts.premiere.quarter == Quarter(2026, 4)
    assert facts.review_issues == ()


def test_stale_out_of_scope_review_only_movie_is_removed(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 1)
    subject_id = 650054
    review = ReviewIssue(
        "DISCOVERY_DATE_MISMATCH",
        target,
        "2025-12-28",
        {"provenance": ["search:air_date:2025-12-25..2026-04-01"]},
        "old",
    )
    sync, repository = _sync(tmp_path, FakeApi({}), DiscoveryBatch(()))
    _store_existing(
        repository,
        subject_id,
        media=MediaFormat.MOVIE,
        air_date=date(2025, 12, 28),
        premiere_quarter=None,
        review_issues=(review,),
    )

    run = sync.run(SyncScope(target, target))

    assert run.exit_code == 0
    assert repository.get_subject_facts(subject_id) is None


def test_stale_target_japanese_review_is_dominated_from_stored_facts(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    target = Quarter(2026, 4)
    subject_id = 650055
    review = ReviewIssue(
        "JAPANESE_CLASSIFICATION_UNRESOLVED",
        None,
        "2026-04-02",
        {"provenance": ["browse:MOVIE:2026-05"]},
        "old",
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi({}),
        DiscoveryBatch(()),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 20),
    )
    _store_existing(
        repository,
        subject_id,
        media=MediaFormat.MOVIE,
        review_issues=(review,),
    )
    snapshot = repository.get_subject_facts(subject_id)
    assert snapshot is not None
    unresolved = replace(
        snapshot,
        subject=replace(
            snapshot.subject,
            rating_count=5,
            japanese=JapaneseDecision(
                JapaneseClassification.UNRESOLVED,
                "unresolved_missing_japanese_region",
                "[]",
            ),
        ),
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, unresolved)

    run = sync.run(SyncScope(target, target))
    result = run.quarters[0]

    assert result.outcome_dominated == 1
    assert result.auto_blacklisted[0]["reason"] == (
        "outcome_dominated_low_rating"
    )
    assert repository.get_subject_facts(subject_id) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({subject_id})
    )


def test_current_non_japanese_decision_removes_stale_japanese_review(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 4)
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650056
    review = ReviewIssue(
        "JAPANESE_CLASSIFICATION_UNRESOLVED",
        None,
        "2026-04-02",
        {"provenance": ["browse:MOVIE:2026-05"]},
        "old",
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi(
            {
                subject_id: _detail(
                    subject_id,
                    media=MediaFormat.MOVIE,
                    country="中国",
                    cover=None,
                )
            }
        ),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.MOVIE),)),
        settings_path=settings_path,
    )
    _store_existing(
        repository,
        subject_id,
        media=MediaFormat.MOVIE,
        premiere_quarter=None,
        review_issues=(review,),
    )

    result = sync.run(SyncScope(target, target)).quarters[0]

    assert result.rejected_non_japanese == 1
    assert repository.get_subject_facts(subject_id) is None


def test_low_rating_unresolved_japanese_is_auto_blacklisted_with_auditable_dominance(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650051
    detail = _detail(
        subject_id,
        country=None,
        rating_count=29,
        cover=None,
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi({subject_id: detail}),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 20),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))
    result = run.quarters[0]

    assert result.outcome_dominated == 1
    assert result.reviews == ()
    assert result.auto_blacklisted[0]["reason"] == "outcome_dominated_low_rating"
    assert result.auto_blacklisted[0]["issue_code"] == (
        "JAPANESE_CLASSIFICATION_UNRESOLVED"
    )
    assert result.auto_blacklisted[0]["japanese_evidence"]["classification"] == (
        "UNRESOLVED"
    )
    assert repository.get_subject_facts(subject_id) is None
    report = json.loads(run.report_path.read_text("utf-8"))
    assert report["new_auto_by_reason"] == {"outcome_dominated_low_rating": 1}


def test_manual_japanese_override_survives_repeated_sync(
    tmp_path: Path,
) -> None:
    subject_id = 650052
    automatic = JapaneseDecision(
        JapaneseClassification.UNRESOLVED,
        "unresolved_missing_japanese_region",
        "[]",
    )
    save_japanese_overrides(
        tmp_path / "japanese-overrides.toml",
        {
            subject_id: JapaneseOverride.from_decision(
                subject_id,
                JapaneseClassification.ACCEPTED_JAPANESE,
                automatic,
            )
        },
    )
    detail = _detail(subject_id, country=None, cover=None)
    sync, repository = _sync(
        tmp_path,
        FakeApi({subject_id: detail}),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV),)),
    )

    first = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]
    second = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]

    assert first.accepted_tv == second.accepted_tv == 1
    facts = repository.get_subject_facts(subject_id)
    assert facts is not None
    assert facts.subject.japanese.evidence_type == "manual_japanese_override"
    assert facts.review_issues == ()


def test_search_only_cold_reviews_are_blacklisted_immediately_without_storage(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_ids = (650020, 650021, 650022, 650023, 650024)
    details = {
        subject_id: _detail(
            subject_id,
            platform="",
            rating_count=rating_count,
            cover=None,
        )
        for subject_id, rating_count in zip(
            subject_ids, (None, 0, 29, 30, 500), strict=True
        )
    }
    candidates = tuple(
        DiscoveredSubject(
            subject_id,
            frozenset(),
            frozenset(),
            frozenset({2}),
            ("search:2026-04",),
        )
        for subject_id in subject_ids
    )
    api = FakeApi(details)
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(candidates),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 1),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert [item["subject_id"] for item in result.auto_blacklisted] == list(subject_ids)
    assert result.external_reviews == ()
    assert result.persisted_review_count == 0
    assert all(
        repository.get_subject_facts(subject_id) is None for subject_id in subject_ids
    )
    assert api.image_calls == []
    assert api.subject_calls == list(subject_ids)
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset(subject_ids)
    )
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["new_auto_by_reason"] == {
        "insufficient_airing_information": 5,
    }
    assert report["quarters"][0]["new_auto_by_reason"] == {
        "insufficient_airing_information": 5,
    }


def test_search_only_cold_rule_blacklists_before_quarter_end(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650025
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                platform="",
                rating_count=None,
                cover=None,
            )
        }
    )
    candidate = DiscoveredSubject(
        subject_id,
        frozenset(),
        frozenset(),
        frozenset({2}),
        ("search:2026-04",),
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((candidate,)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 1),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert result.auto_blacklisted[0]["subject_id"] == subject_id
    assert result.auto_blacklisted[0]["reason"] == (
        "insufficient_airing_information"
    )
    assert result.external_reviews == ()
    assert repository.get_subject_facts(subject_id) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({subject_id})
    )


@pytest.mark.parametrize("rating_count", (0, 500))
def test_conflict_review_is_never_cold_blacklisted(
    tmp_path: Path, rating_count: int
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650026
    stable_id = 650029
    candidate = DiscoveredSubject(
        subject_id,
        frozenset({MediaFormat.TV, MediaFormat.MOVIE}),
        frozenset(),
        frozenset({2}),
        ("browse:TV:2026-04", "browse:MOVIE:2026-04"),
    )
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                air_date=None,
                rating_count=rating_count,
                cover=None,
            ),
            stable_id: _detail(stable_id, rating_count=100, cover=None),
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((candidate, _candidate(stable_id, MediaFormat.TV))),
        settings_path=settings_path,
        evaluation_date=date(2026, 8, 18),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert result.auto_blacklisted == ()
    assert result.reviews[0].issue_code == "DISCOVERY_MEDIA_CONFLICT"
    assert repository.get_subject_facts(subject_id) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )

@pytest.mark.parametrize("rating_count", (None, 0, 500))
def test_tv_quarter_date_unresolved_616616_blacklists_immediately(
    tmp_path: Path, rating_count: int | None
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    target_quarter = Quarter(2026, 7)
    subject_id = 616616
    candidate = DiscoveredSubject(
        subject_id,
        frozenset({MediaFormat.TV}),
        frozenset(),
        frozenset({2}),
        ("browse:TV:2026-07",),
    )
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                air_date=None,
                rating_count=rating_count,
                cover=None,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((candidate,)),
        settings_path=settings_path,
        evaluation_date=date(2026, 7, 1),
    )
    _store_existing(
        repository,
        subject_id,
        review_issues=(
            ReviewIssue(
                TV_QUARTER_DATE_UNRESOLVED,
                target_quarter,
                None,
                {"subject_id": subject_id},
                "old",
            ),
        ),
        premiere_quarter=target_quarter,
    )

    result = sync.run(
        SyncScope(target_quarter, target_quarter, refresh_existing=True)
    ).quarters[0]

    assert result.auto_blacklisted[0]["subject_id"] == subject_id
    assert result.reviews == ()
    assert repository.list_review_issues(target_quarter) == ()


def test_date_conflict_is_not_blacklisted_by_existing_low_rating_rule(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650030
    stable_id = 650031
    candidate = DiscoveredSubject(
        subject_id,
        frozenset({MediaFormat.TV}),
        frozenset({date(2026, 4, 2), date(2026, 4, 3)}),
        frozenset({2}),
        ("browse:TV:2026-04", "search:2026-04"),
    )
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                air_date="2026-04-02",
                rating_count=0,
                cover=None,
            ),
            stable_id: _detail(stable_id, rating_count=100, cover=None),
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((candidate, _candidate(stable_id, MediaFormat.TV))),
        settings_path=settings_path,
        evaluation_date=date(2026, 8, 18),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].auto_blacklisted == ()
    assert run.quarters[0].reviews[0].issue_code == "DISCOVERY_DATE_MISMATCH"
    assert repository.get_subject_facts(subject_id) is not None


def test_manual_quarter_override_wins_over_unresolved_cold_rule(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650027
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                media=MediaFormat.MOVIE,
                air_date=None,
                rating_count=None,
                cover=None,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(subject_id, MediaFormat.MOVIE),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 12, 8),
    )

    sync.overrides_path.write_text(
        "[[assignments]]\n"
        f"subject_id = {subject_id}\n"
        "year = 2026\n"
        "quarter_month = 4\n"
        'reason = "manual"\n',
        encoding="utf-8",
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert result.auto_blacklisted == ()
    assert result.accepted_movie == 1
    snapshot = repository.get_subject_facts(subject_id)
    assert snapshot is not None
    assert snapshot.premiere is not None
    assert snapshot.premiere.assignment_source is QuarterAssignmentSource.MANUAL


def test_range_sync_deduplicates_new_cold_blacklist_event(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650028
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                media=MediaFormat.MOVIE,
                air_date=None,
                rating_count=None,
                cover=None,
            )
        }
    )
    candidate = _candidate(subject_id, MediaFormat.MOVIE)
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((candidate,)),
        settings_path=settings_path,
        evaluation_date=date(2026, 12, 8),
    )

    run = sync.run(SyncScope(Quarter(2026, 4), Quarter(2026, 7)))

    assert run.exit_code == 0
    assert [len(item.auto_blacklisted) for item in run.quarters] == [1, 0]
    assert [item.existing_auto_blacklisted for item in run.quarters] == [0, 1]
    assert sum(item.blacklisted for item in run.quarters) == 2
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["auto_blacklisted_count"] == 1
    assert report["new_auto_by_reason"] == {
        "insufficient_airing_information": 1,
    }
    assert repository.get_subject_facts(subject_id) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({subject_id})
    )


def test_auto_blacklist_removes_existing_facts_reviews_and_cover_safely(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {
            650005: _detail(
                650005,
                air_date="2026-04-02",
                rating_count=29,
                cover=None,
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650005, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )
    _store_existing(repository, 650005)
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    (covers / "650005.webp").write_bytes(b"old cover")
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                QUARTER,
                FACTS_COMPLETE,
                COVERS_COMPLETE,
                1,
                0,
                "attempt-1",
                "success-1",
            ),
        )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert repository.get_subject_facts(650005) is None
    assert not (covers / "650005.webp").exists()
    assert not list(covers.glob(".blacklist-*"))
    state = repository.get_sync_state(QUARTER)
    assert state is not None
    assert state.facts_status == FACTS_COMPLETE
    assert state.subject_count == 0
    assert state.last_success_at is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({650005})
    )


def test_auto_blacklist_config_failure_restores_existing_data_and_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {650006: _detail(650006, air_date="2026-04-02", rating_count=29, cover=None)}
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650006, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )
    _store_existing(repository, 650006)
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    (covers / "650006.webp").write_bytes(b"old cover")
    original = settings_path.read_bytes()

    def fail_replace(*_: object, **__: object) -> None:
        raise PermissionError("config replace denied")

    monkeypatch.setattr(
        "bgm_side_b.archive_config._atomic_replace_bytes", fail_replace
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 1
    assert run.quarters[0].facts_status == FACTS_INCOMPLETE
    assert repository.get_subject_facts(650006) is not None
    assert (covers / "650006.webp").read_bytes() == b"old cover"
    assert settings_path.read_bytes() == original
    assert not list(covers.glob(".blacklist-*"))


def test_auto_blacklist_database_failure_restores_cover_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {650007: _detail(650007, air_date="2026-04-02", rating_count=29, cover=None)}
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650007, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )
    _store_existing(repository, 650007)
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    (covers / "650007.webp").write_bytes(b"old cover")
    original = settings_path.read_bytes()

    def fail_delete(*_: object, **__: object) -> int:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(repository, "delete_subjects", fail_delete)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 1
    assert repository.get_subject_facts(650007) is not None
    assert (covers / "650007.webp").read_bytes() == b"old cover"
    assert settings_path.read_bytes() == original
    assert not list(covers.glob(".blacklist-*"))


def test_existing_auto_blacklist_is_retained_when_low_rating_still_holds(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {650008: _detail(650008, air_date="2026-04-02", rating_count=29, cover=None)}
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650008, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    first = sync.run(SyncScope(QUARTER, QUARTER))
    second = sync.run(SyncScope(QUARTER, QUARTER))

    assert first.quarters[0].auto_blacklisted[0]["subject_id"] == 650008
    assert second.quarters[0].auto_blacklisted == ()
    assert second.quarters[0].blacklisted == 1
    assert second.quarters[0].manual_blacklisted == 0
    assert second.quarters[0].existing_auto_blacklisted == 1
    second_report = json.loads(second.report_path.read_text(encoding="utf-8"))
    assert second_report["manual_blacklisted"] == 0
    assert second_report["existing_auto_blacklisted"] == 1
    assert second_report["auto_blacklisted_count"] == 0
    assert repository.get_subject_facts(650008) is None


def test_existing_auto_blacklist_is_reconsidered_when_rating_recovers(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650015
    add_auto_excluded_subject(settings_path, subject_id, name_cn="旧自动排除")
    sync, repository = _sync(
        tmp_path,
        FakeApi(
            {
                subject_id: _detail(
                    subject_id,
                    air_date="2026-04-02",
                    rating_count=30,
                    cover=None,
                )
            }
        ),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))
    result = run.quarters[0]

    assert run.exit_code == 0
    assert result.auto_reconsidered == 1
    assert result.auto_restored == (subject_id,)
    assert result.existing_auto_blacklisted == 0
    assert repository.get_subject_facts(subject_id) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )

    second = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]
    assert second.auto_reconsidered == 0
    assert second.auto_restored == ()
    assert second.accepted_tv == 1
    snapshot = repository.get_subject_facts(subject_id)
    assert snapshot is not None
    assert len(snapshot.appearances) == 1


def test_existing_auto_blacklist_is_reconsidered_when_evidence_becomes_accepted(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650016
    add_auto_excluded_subject(settings_path, subject_id, name_cn="旧信息不足排除")
    sync, repository = _sync(
        tmp_path,
        FakeApi(
            {
                subject_id: _detail(
                    subject_id,
                    air_date="2026-04-02",
                    rating_count=100,
                    country="日本",
                    cover=None,
                )
            }
        ),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    result = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]

    assert result.auto_restored == (subject_id,)
    assert result.accepted_tv == 1
    assert result.reviews == ()
    assert repository.get_subject_facts(subject_id) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_existing_auto_blacklist_becomes_high_impact_review_after_reassessment(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650017
    stable_id = 650018
    add_auto_excluded_subject(settings_path, subject_id, name_cn="旧低信息排除")
    sync, repository = _sync(
        tmp_path,
        FakeApi(
            {
                subject_id: _detail(
                    subject_id,
                    country=None,
                    rating_count=100,
                    cover=None,
                ),
                stable_id: _detail(stable_id, cover=None),
            }
        ),
        DiscoveryBatch(
            (
                _candidate(subject_id, MediaFormat.TV),
                _candidate(stable_id, MediaFormat.TV),
            )
        ),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    result = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]

    assert result.auto_restored == (subject_id,)
    assert result.accepted_tv == 1
    assert [issue.issue_code for issue in result.reviews] == [
        "JAPANESE_CLASSIFICATION_UNRESOLVED"
    ]
    assert repository.get_subject_facts(subject_id) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_auto_reconciliation_restores_config_when_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = 650019
    add_auto_excluded_subject(settings_path, subject_id, name_cn="事务回滚")
    original_config = settings_path.read_bytes()
    sync, repository = _sync(
        tmp_path,
        FakeApi({subject_id: _detail(subject_id, cover=None)}),
        DiscoveryBatch((_candidate(subject_id, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )
    real_remove = sync_module.remove_auto_excluded_subjects

    def remove_then_fail(path: Path, subject_ids: object) -> tuple[int, ...]:
        real_remove(path, subject_ids)  # type: ignore[arg-type]
        raise RuntimeError("commit failed after config update")

    monkeypatch.setattr(sync_module, "remove_auto_excluded_subjects", remove_then_fail)

    with pytest.raises(RuntimeError, match="commit failed after config update"):
        sync.run(SyncScope(QUARTER, QUARTER))

    assert settings_path.read_bytes() == original_config
    assert repository.get_subject_facts(subject_id) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({subject_id})
    )


@pytest.mark.parametrize(
    "case", AUTO_EXCLUSION_CORPUS, ids=lambda item: item["name"]
)
def test_auto_exclusion_lifecycle_corpus_replay(
    tmp_path: Path, case: dict[str, object]
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    subject_id = int(case["subject_id"])
    media = MediaFormat[str(case["media"])]
    if case.get("old_auto"):
        add_auto_excluded_subject(
            settings_path,
            subject_id,
            name_cn=f"corpus {subject_id}",
        )
    if case.get("manual_excluded"):
        content = settings_path.read_text(encoding="utf-8").replace(
            "excluded_subject_ids = []",
            f"excluded_subject_ids = [{subject_id}]",
        )
        settings_path.write_text(content, encoding="utf-8")
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                media=media,
                country=case["country"],  # type: ignore[arg-type]
                rating_count=case["rating_count"],  # type: ignore[arg-type]
                cover=None,
                other=(str(case["other"]) if case.get("other") else None),
            )
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(subject_id, media),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 20),
    )
    if case.get("old_review"):
        _store_existing(
            repository,
            subject_id,
            media=media,
            review_issues=(
                ReviewIssue(
                    "JAPANESE_CLASSIFICATION_UNRESOLVED",
                    None,
                    "2026-04-02",
                    {"subject_id": subject_id},
                    "old",
                ),
            ),
        )

    result = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]
    expected = case["expected"]
    assert isinstance(expected, dict)
    if media is MediaFormat.TV:
        assert result.accepted_tv == expected["accepted"]
    else:
        assert result.accepted_movie == expected["accepted"]
    assert result.rejected_non_japanese == expected["rejected_non_japanese"]
    assert len(result.auto_blacklisted) == expected["auto_blacklisted"]
    assert result.auto_reconsidered == (1 if case.get("old_auto") else 0)
    assert result.auto_restored == (
        (subject_id,) if expected["restored"] else ()
    )
    assert result.auto_reconciled == (
        (subject_id,) if expected.get("reconciled") else ()
    )
    review_codes = [issue.issue_code for issue in result.reviews]
    assert review_codes == (
        [expected["review_code"]] if expected["review_code"] else []
    )
    assert (repository.get_subject_facts(subject_id) is not None) is expected["stored"]
    settings = load_archive_sync_settings(settings_path)
    if case.get("manual_excluded"):
        assert subject_id in settings.excluded_subject_ids
    elif (
        case.get("old_auto")
        and not expected["restored"]
        and not expected.get("reconciled")
    ):
        assert subject_id in settings.auto_excluded_subject_ids
    elif expected["auto_blacklisted"]:
        assert subject_id in settings.auto_excluded_subject_ids
    else:
        assert subject_id not in settings.auto_excluded_subject_ids
    if case.get("manual_excluded"):
        assert api.subject_calls == []
    if expected.get("reconciled"):
        second = sync.run(SyncScope(QUARTER, QUARTER)).quarters[0]
        assert second.auto_reconsidered == 0
        assert second.auto_reconciled == ()


def test_blacklist_source_counts_mixed_manual_existing_and_new_auto(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    add_auto_excluded_subject(settings_path, 650012, name_cn="历史自动")
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            "excluded_subject_ids = []", "excluded_subject_ids = [650013]"
        ),
        encoding="utf-8",
    )
    api = FakeApi(
        {
            650012: _detail(
                650012,
                air_date="2026-04-02",
                rating_count=29,
                cover=None,
            ),
            650014: _detail(
                650014,
                air_date="2026-04-02",
                rating_count=29,
                cover=None,
            )
        }
    )
    sync, _ = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (
                _candidate(650012, MediaFormat.TV),
                _candidate(650013, MediaFormat.TV),
                _candidate(650014, MediaFormat.TV),
            )
        ),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert result.blacklisted == 3
    assert result.manual_blacklisted == 1
    assert result.existing_auto_blacklisted == 1
    assert len(result.auto_blacklisted) == 1
    assert (
        result.manual_blacklisted
        + result.existing_auto_blacklisted
        + len(result.auto_blacklisted)
        == result.blacklisted
    )
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["blacklisted"] == 3
    assert report["manual_blacklisted"] == 1
    assert report["existing_auto_blacklisted"] == 1
    assert report["auto_blacklisted_count"] == 1


def test_auto_blacklist_requires_unambiguous_media_scope(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    detail = _detail(650009, air_date="2026-04-02", rating_count=1, cover=None)
    stable_detail = _detail(650011, rating_count=100, cover=None)
    conflict = DiscoveredSubject(
        650009,
        frozenset({MediaFormat.TV, MediaFormat.MOVIE}),
        frozenset({date(2026, 4, 2)}),
        frozenset({2}),
        ("browse:TV:2026-04", "browse:MOVIE:2026-04"),
    )
    sync, repository = _sync(
        tmp_path,
        FakeApi({650009: detail, 650011: stable_detail}),
        DiscoveryBatch((conflict, _candidate(650011, MediaFormat.TV))),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].auto_blacklisted == ()
    assert run.quarters[0].reviews[0].issue_code == "DISCOVERY_MEDIA_CONFLICT"
    assert repository.get_subject_facts(650009) is not None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset()
    )


def test_manual_removal_allows_later_reassessment(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi(
        {650010: _detail(650010, air_date="2026-04-02", rating_count=29, cover=None)}
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(650010, MediaFormat.TV),)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 11),
    )
    sync.run(SyncScope(QUARTER, QUARTER))
    settings_path.write_text(
        (ROOT / "config" / "bangumi.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    api.details[650010] = _detail(
        650010, air_date="2026-04-02", rating_count=100, cover=None
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].auto_blacklisted == ()
    assert run.quarters[0].manual_blacklisted == 0
    assert run.quarters[0].existing_auto_blacklisted == 0
    assert repository.get_subject_facts(650010) is not None


@pytest.mark.parametrize("candidate_count", (100, 1200))
def test_quarter_sync_preloads_existing_facts_with_bounded_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_count: int
) -> None:
    subject_ids = tuple(range(1000, 1000 + candidate_count))
    api = FakeApi(
        {subject_id: _detail(subject_id, cover=None) for subject_id in subject_ids}
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            tuple(_candidate(subject_id, MediaFormat.TV) for subject_id in subject_ids)
        ),
    )
    repository.database.initialize()
    with repository.transaction() as connection:
        for subject_id in subject_ids:
            repository.upsert_subject(
                connection,
                SubjectRecord(
                    subject_id,
                    f"Existing {subject_id}",
                    None,
                    None,
                    MediaFormat.TV,
                    date(2026, 4, 2),
                    None,
                    None,
                    None,
                    None,
                    JapaneseDecision(
                        JapaneseClassification.ACCEPTED_JAPANESE,
                        "infobox_country",
                        '["日本"]',
                    ),
                ),
            )
    connect_calls = 0
    selects: list[str] = []
    native_connect = repository.database.connect

    def counted_connect() -> sqlite3.Connection:
        nonlocal connect_calls
        connect_calls += 1
        connection = native_connect()
        connection.set_trace_callback(
            lambda statement: selects.append(statement)
            if statement.lstrip().upper().startswith("SELECT")
            else None
        )
        return connection

    monkeypatch.setattr(repository.database, "connect", counted_connect)
    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].accepted_tv == candidate_count
    assert connect_calls < 20
    assert len(selects) < 100


def _continuing(quarter: Quarter, evidence_value: str) -> QuarterAppearance:
    return QuarterAppearance(
        quarter,
        QuarterAppearanceKind.CONTINUING,
        QuarterAssignmentSource.AUTOMATIC,
        "main_episode_airdate",
        evidence_value,
    )


def test_detail_failure_marks_incomplete_without_committing_partial_quarter(
    tmp_path: Path,
) -> None:
    api = FakeApi({101: _detail(101), 102: _detail(102)}, failures=frozenset({102}))
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (_candidate(101, MediaFormat.TV), _candidate(102, MediaFormat.TV))
        ),
    )
    _store_existing(repository, 99)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 1
    assert run.quarters[0].facts_status == FACTS_INCOMPLETE
    assert repository.get_subject_facts(99) is not None
    assert repository.get_subject_facts(101) is None
    assert repository.get_sync_state(QUARTER).facts_status == FACTS_INCOMPLETE  # type: ignore[union-attr]


def test_zero_japanese_tv_admission_preserves_old_facts_and_marks_incomplete(
    tmp_path: Path,
) -> None:
    api = FakeApi(
        {
            101: _detail(101, country="中国", cover=None),
            202: _detail(
                202,
                media=MediaFormat.MOVIE,
                air_date="2026-05-01",
                cover=None,
            ),
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (
                _candidate(101, MediaFormat.TV),
                _candidate(202, MediaFormat.MOVIE, date(2026, 5, 1)),
            )
        ),
    )
    _store_existing(repository, 99)
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                QUARTER,
                FACTS_COMPLETE,
                "complete",
                1,
                0,
                "attempt-1",
                "success-1",
            ),
        )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 1
    assert result.facts_status == FACTS_INCOMPLETE
    assert result.errors[0]["code"] == "empty_included_result"
    assert repository.get_subject_facts(99) is not None
    assert repository.get_subject_facts(101) is None
    assert repository.get_subject_facts(202) is None
    state = repository.get_sync_state(QUARTER)
    assert state is not None
    assert state.facts_status == FACTS_INCOMPLETE
    assert state.subject_count == 1
    assert state.last_success_at == "success-1"


def test_low_japanese_tv_inclusion_rate_warns_without_blocking(
    tmp_path: Path,
) -> None:
    subject_ids = tuple(range(100, 120))
    api = FakeApi(
        {
            subject_id: _detail(
                subject_id,
                country="日本" if subject_id == 100 else "中国",
                cover=None,
            )
            for subject_id in subject_ids
        }
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            tuple(_candidate(subject_id, MediaFormat.TV) for subject_id in subject_ids)
        ),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert result.facts_status == FACTS_COMPLETE
    assert result.accepted_tv == 1
    assert any(
        warning["code"] == "low_japan_tv_inclusion_rate"
        for warning in result.warnings
    )
    assert repository.get_subject_facts(100) is not None


def test_complete_facts_store_tv_movie_review_and_final_webp_cover(
    tmp_path: Path,
) -> None:
    details = {
        101: _detail(101),
        102: _detail(102, media=MediaFormat.MOVIE, air_date="2026-05-01"),
        103: _detail(103, country="中国"),
        104: _detail(104, country=None),
    }
    api = FakeApi(details)
    candidates = DiscoveryBatch(
        (
            _candidate(101, MediaFormat.TV),
            _candidate(102, MediaFormat.MOVIE, date(2026, 5, 1)),
            _candidate(103, MediaFormat.TV),
            _candidate(104, MediaFormat.TV),
        )
    )
    sync, repository = _sync(tmp_path, api, candidates)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    assert run.exit_code == 0
    assert (
        result.accepted_tv,
        result.accepted_movie,
        result.rejected_non_japanese,
    ) == (1, 1, 1)
    assert result.facts_status == FACTS_COMPLETE
    assert result.covers_status == FACTS_COMPLETE
    assert (
        repository.list_subjects_appearing_in_quarter(QUARTER)[0].subject.subject_id
        == 101
    )
    assert [
        item.subject.subject_id
        for item in repository.list_subjects_appearing_in_quarter(QUARTER)
    ] == [101, 102]
    review = repository.get_subject_facts(104)
    assert review is not None
    assert review.premiere is None
    assert review.review_issues[0].issue_code == "JAPANESE_CLASSIFICATION_UNRESOLVED"
    with Image.open(tmp_path / "covers" / "101.webp") as cover:
        assert (cover.format, cover.size) == ("WEBP", (1200, 480))
    assert repository.get_subject_facts(101).cover.width == 1200  # type: ignore[union-attr]
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["accepted_tv"] == 1
    assert report["quarters"][0]["reviews"][0]["subject_id"] == 104
    assert report["source_counts"] == {"漫画改": 2}
    assert report["episode_count"] == {
        "known": 2,
        "unknown": 0,
        "legacy_zero_written": 0,
    }
    assert report["canonical_detail_requests"] == 4


def test_browse_candidate_always_uses_canonical_subject_detail(
    tmp_path: Path,
) -> None:
    detail = _detail(101, country=None, meta_tags=("日本", "TV"))
    candidate = DiscoveredSubject(
        101,
        frozenset({MediaFormat.TV}),
        frozenset({date(2026, 4, 2)}),
        frozenset({2}),
        ("browse:TV:2026-04",),
    )
    api = FakeApi({101: detail})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch((candidate,)))

    assert sync.run(SyncScope(QUARTER, QUARTER)).exit_code == 0
    assert api.subject_calls == [101]
    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.subject.japanese.evidence_type == "bangumi_public_region_tag"


def test_canonical_detail_episode_count_is_persisted_after_partial_discovery(
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "api" / "subject-547888.json").read_text(
            encoding="utf-8"
        )
    )
    detail = SubjectDetail.from_payload(
        {
            **fixture,
            "infobox": [*fixture["infobox"], {"key": "国家/地区", "value": "日本"}],
        }
    )
    api = FakeApi({547888: detail})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(547888, MediaFormat.TV, date(2026, 4, 8)),)),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    facts = repository.get_subject_facts(547888)
    assert run.exit_code == 0
    assert facts is not None
    assert facts.subject.episode_count == 11
    assert run.quarters[0].canonical_detail_requests == 1


def test_bgm_571784_episode_count_survives_sync_and_repository(tmp_path: Path) -> None:
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "api" / "subject-571784.json").read_text(
            encoding="utf-8"
        )
    )
    api = FakeApi({571784: SubjectDetail.from_payload(fixture)})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch(
            (_candidate(571784, MediaFormat.TV, date(2026, 7, 9)),)
        ),
    )

    quarter = Quarter(2026, 7)
    run = sync.run(SyncScope(quarter, quarter))

    facts = repository.get_subject_facts(571784)
    assert run.exit_code == 0
    assert facts is not None and facts.subject.episode_count == 12
    assert run.quarters[0].episode_source_counts == (("subject_structured", 1),)


def test_sync_persists_eps_when_episode_registry_total_is_lower(
    tmp_path: Path,
) -> None:
    detail = replace(_detail(990001), total_episodes=8)
    api = FakeApi({990001: detail})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(990001, MediaFormat.TV),)),
    )

    quarter = Quarter(2026, 4)
    run = sync.run(SyncScope(quarter, quarter))

    facts = repository.get_subject_facts(990001)
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert run.exit_code == 0
    assert facts is not None and facts.subject.episode_count == 12
    assert run.quarters[0].episode_source_counts == (("subject_structured", 1),)
    assert report["episode_count_sources"] == {"subject_structured": 1}


def test_unknown_canonical_episode_count_uses_bounded_registry_fallback(
    tmp_path: Path,
) -> None:
    class FallbackApi(FakeApi):
        def __init__(self, details: dict[int, SubjectDetail]) -> None:
            super().__init__(details)
            self.episode_count_calls: list[int] = []

        def get_main_episode_count(self, subject_id: int) -> int | None:
            self.episode_count_calls.append(subject_id)
            return 6

    detail = replace(_detail(547889), eps=0, total_episodes=0)
    api = FallbackApi({547889: detail})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(547889, MediaFormat.TV),)),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    facts = repository.get_subject_facts(547889)
    assert run.exit_code == 0
    assert facts is not None and facts.subject.episode_count == 6
    assert api.episode_count_calls == [547889]
    assert run.quarters[0].episode_source_counts == (("episode_registry", 1),)
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["episode_count_sources"] == {"episode_registry": 1}


def test_unscoped_persisted_review_findings_are_not_reported_as_quarter_queue(
    tmp_path: Path,
) -> None:
    unresolved_ids = tuple(range(547900, 547911))
    details = {
        547899: _detail(547899, cover=None),
        **{
            subject_id: _detail(subject_id, country=None, cover=None)
            for subject_id in unresolved_ids
        },
    }
    candidates = DiscoveryBatch(
        (
            _candidate(547899, MediaFormat.TV),
            *(_candidate(subject_id, MediaFormat.TV) for subject_id in unresolved_ids),
        )
    )
    sync, repository = _sync(tmp_path, FakeApi(details), candidates)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    result = run.quarters[0]
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert run.exit_code == 0
    assert len(result.reviews) == 11
    assert result.persisted_review_count == 0
    assert repository.list_review_issues(QUARTER) == ()
    assert report["review_count"] == 0
    assert report["external_review_count"] == 0


def test_search_failure_does_not_block_stable_browse_premiere_sync(
    tmp_path: Path,
) -> None:
    api = FakeApi({101: _detail(101)})
    failure = DiscoveryFailure(
        DiscoverySource.SEARCH,
        "search:air_date:2026-03-25..2026-07-01",
        "http_500",
        "HTTP 500",
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(101, MediaFormat.TV),)),
        DiscoveryBatch((), (failure,)),
    )
    _store_existing(repository, 99)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert api.subject_calls == [101]
    assert repository.get_subject_facts(99) is None
    assert repository.get_subject_facts(101) is not None
    assert repository.get_sync_state(QUARTER).facts_status == FACTS_COMPLETE  # type: ignore[union-attr]


def test_search_only_media_is_immediately_blacklisted_without_normal_sync(
    tmp_path: Path,
) -> None:
    settings_path = _auto_settings_path(tmp_path)
    api = FakeApi({101: _detail(101), 202: _detail(202, platform="")})
    search_only = DiscoveredSubject(
        202,
        frozenset(),
        frozenset({date(2026, 4, 2)}),
        frozenset({2}),
        ("search:air_date:2026-03-25..2026-07-01",),
    )
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(101, MediaFormat.TV),)),
        DiscoveryBatch((search_only,)),
        settings_path=settings_path,
        evaluation_date=date(2026, 4, 1),
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    result = run.quarters[0]
    assert result.facts_status == FACTS_COMPLETE
    assert result.auto_blacklisted[0]["subject_id"] == 202
    assert result.auto_blacklisted[0]["reason"] == (
        "insufficient_airing_information"
    )
    assert result.external_reviews == ()
    assert repository.get_subject_facts(202) is None
    assert load_archive_sync_settings(settings_path).auto_excluded_subject_ids == (
        frozenset({202})
    )
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["review_count"] == 0
    assert report["external_review_count"] == 0
    assert report["error_count"] == 0


def test_range_skips_complete_quarters_and_parser_only_refreshes_explicitly(
    tmp_path: Path,
) -> None:
    api = FakeApi({})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    repository.database.initialize()
    with repository.transaction() as connection:
        for quarter in (Quarter(2026, 4), Quarter(2026, 7)):
            repository.write_sync_state(
                connection,
                QuarterSyncState(
                    quarter,
                    FACTS_COMPLETE,
                    FACTS_COMPLETE,
                    0,
                    0,
                    "2026-08-10T00:00:00Z",
                    "2026-08-10T00:00:00Z",
                ),
            )

    run = sync.run(SyncScope(Quarter(2026, 4), Quarter(2026, 7)))

    assert [item.skipped for item in run.quarters] == [True, True]
    assert parse_sync_scope(["2026", "4"]).is_single_quarter
    assert parse_sync_scope(
        [], range_start=["2026", "4"], range_end=["2026", "7"], refresh_existing=True
    ).refresh_existing


def test_blacklist_purges_database_and_exact_final_cover_before_sync(
    tmp_path: Path,
) -> None:
    settings = replace(
        load_archive_sync_settings(ROOT / "config" / "bangumi.toml"),
        excluded_subject_ids=frozenset({101}),
    )
    sync, repository = _sync(
        tmp_path, FakeApi({}), DiscoveryBatch(()), settings=settings
    )
    _store_existing(repository, 101)
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "101.webp").write_bytes(b"stale")

    sync.run(SyncScope(QUARTER, QUARTER))

    assert repository.get_subject_facts(101) is None
    assert not (covers / "101.webp").exists()


def test_blacklist_transaction_failure_restores_quarantined_covers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = replace(
        load_archive_sync_settings(ROOT / "config" / "bangumi.toml"),
        excluded_subject_ids=frozenset({101, 102}),
    )
    sync, repository = _sync(
        tmp_path, FakeApi({}), DiscoveryBatch(()), settings=settings
    )
    _store_existing(repository, 101)
    _store_existing(repository, 102)
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "101.webp").write_bytes(b"cover-101")
    (covers / "102.webp").write_bytes(b"cover-102")

    def fail_delete(*_: object, **__: object) -> int:
        raise RuntimeError("database write failed")

    monkeypatch.setattr(repository, "delete_subjects", fail_delete)

    with pytest.raises(RuntimeError, match="database write failed"):
        sync._purge_blacklist()

    assert repository.get_subject_facts(101) is not None
    assert repository.get_subject_facts(102) is not None
    assert (covers / "101.webp").read_bytes() == b"cover-101"
    assert (covers / "102.webp").read_bytes() == b"cover-102"
    assert not list(covers.glob(".blacklist-*"))


def test_cover_failure_does_not_rollback_complete_facts(tmp_path: Path) -> None:
    api = FakeApi({101: _detail(101)}, image_failure=True)
    sync, repository = _sync(
        tmp_path, api, DiscoveryBatch((_candidate(101, MediaFormat.TV),))
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].covers_status == "incomplete"
    assert repository.get_subject_facts(101) is not None


def test_cover_interrupt_does_not_start_unbounded_pending_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync, _ = _sync(tmp_path, FakeApi({}), DiscoveryBatch(()))
    started: list[int] = []
    lock = threading.Lock()

    prepared = tuple(
        SimpleNamespace(
            snapshot=SimpleNamespace(subject=SimpleNamespace(subject_id=subject_id))
        )
        for subject_id in range(MAX_COVER_CONCURRENCY * 2)
    )

    def interrupting_cover(item: object) -> CoverResult:
        subject_id = item.snapshot.subject.subject_id  # type: ignore[attr-defined]
        with lock:
            started.append(subject_id)
        if subject_id == 0:
            raise KeyboardInterrupt
        return CoverResult("missing", None)

    monkeypatch.setattr(sync, "_sync_cover", interrupting_cover)
    with pytest.raises(KeyboardInterrupt):
        sync._sync_covers(prepared)  # type: ignore[arg-type]

    assert len(started) <= MAX_COVER_CONCURRENCY


def test_manual_missing_subject_import_refuses_non_japanese_then_stores_manual_fact(
    tmp_path: Path,
) -> None:
    api = FakeApi({101: _detail(101), 102: _detail(102, country="中国")})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))

    with pytest.raises(SyncError, match="confirmed"):
        sync.import_single_subject(102, QuarterOverride(QUARTER))
    imported = sync.import_single_subject(101, QuarterOverride(QUARTER))

    assert repository.get_subject_facts(102) is None
    assert imported.snapshot.premiere == QuarterAppearance(
        QUARTER,
        QuarterAppearanceKind.PREMIERE,
        QuarterAssignmentSource.MANUAL,
        "manual_override",
        "quarter_override",
    )
    assert imported.report_path.exists()
    assert (tmp_path / "covers" / "101.webp").exists()


def test_interrupt_marks_current_quarter_incomplete_without_partial_facts(
    tmp_path: Path,
) -> None:
    api = FakeApi({101: _detail(101)}, interrupts=frozenset({101}))
    sync, repository = _sync(
        tmp_path, api, DiscoveryBatch((_candidate(101, MediaFormat.TV),))
    )
    _store_existing(repository, 99)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 130
    assert repository.get_subject_facts(99) is not None
    assert repository.get_subject_facts(101) is None
    assert repository.get_sync_state(QUARTER).facts_status == FACTS_INCOMPLETE  # type: ignore[union-attr]


def test_discovery_interrupt_invalidates_prior_complete_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync, repository = _sync(tmp_path, FakeApi({}), DiscoveryBatch(()))
    repository.database.initialize()
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                QUARTER,
                FACTS_COMPLETE,
                FACTS_COMPLETE,
                0,
                0,
                "attempt-1",
                "success-1",
            ),
        )

    def interrupt_discovery(_quarter: Quarter) -> DiscoveryBatch:
        raise KeyboardInterrupt

    monkeypatch.setattr(sync.browse, "discover", interrupt_discovery)

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 130
    state = repository.get_sync_state(QUARTER)
    assert state is not None
    assert state.facts_status == FACTS_INCOMPLETE
    assert state.last_success_at == "success-1"


def test_continuing_reconciliation_interrupt_invalidates_target_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101)
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                target,
                FACTS_COMPLETE,
                FACTS_COMPLETE,
                0,
                0,
                "attempt-1",
                "success-1",
            ),
        )

    def interrupt_episode(_subject_id: int) -> tuple[date, ...]:
        raise KeyboardInterrupt

    monkeypatch.setattr(api, "get_main_episode_airdates", interrupt_episode)

    run = sync.run(SyncScope(target, target))

    assert run.exit_code == 130
    state = repository.get_sync_state(target)
    assert state is not None
    assert state.facts_status == FACTS_INCOMPLETE
    assert state.last_success_at == "success-1"


def test_end_date_crossing_target_creates_continuing_without_episode_probe(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, end_date=date(2026, 7, 1))

    run = sync.run(SyncScope(target, target))
    result = run.quarters[0]

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == (
        QuarterAppearance(
            target,
            QuarterAppearanceKind.CONTINUING,
            QuarterAssignmentSource.AUTOMATIC,
            "structured_end_date",
            "2026-07-01",
        ),
    )
    assert api.episode_calls == []
    assert result.continuing_end_date == 1
    assert result.continuing_episode == 0
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["quarters"][0]["continuing"] == {
        "confirmed_by_end_date": 1,
        "confirmed_by_main_episode": 0,
        "unresolved": 0,
    }


def test_main_episode_in_target_creates_continuing(tmp_path: Path) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({}, episode_airdates={101: (date(2026, 7, 4),)})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101)

    result = sync.run(SyncScope(target, target)).quarters[0]

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == (_continuing(target, "2026-07-04"),)
    assert result.continuing_episode == 1


def test_later_discovery_cannot_overwrite_an_existing_premiere(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    detail = _detail(101, air_date="2026-07-04")
    api = FakeApi({101: detail}, episode_airdates={101: (date(2026, 7, 4),)})
    sync, repository = _sync(
        tmp_path,
        api,
        DiscoveryBatch((_candidate(101, MediaFormat.TV, date(2026, 7, 4)),)),
    )
    _store_existing(repository, 101)

    result = sync.run(SyncScope(target, target)).quarters[0]

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.premiere is not None and facts.premiere.quarter == QUARTER
    assert facts.continuing == (_continuing(target, "2026-07-04"),)
    assert api.episode_calls == [101]
    assert result.accepted_tv == 0
    assert result.continuing_episode == 1
    assert result.warnings[0]["code"] == "premiere_retained"


def test_early_end_date_and_target_episode_create_a_conflict_review(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({}, episode_airdates={101: (date(2026, 7, 4),)})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, end_date=date(2026, 6, 30))

    result = sync.run(SyncScope(target, target)).quarters[0]

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == ()
    assert result.continuing_unresolved == 1
    assert facts.review_issues[0].issue_code == "CONTINUING_EVIDENCE_CONFLICT"


@pytest.mark.parametrize(
    ("end_date", "episode_airdates"),
    (
        (date(2026, 6, 30), (date(2026, 6, 30),)),
        (None, (date(2026, 6, 30), date(2026, 10, 1))),
        (None, ()),
    ),
)
def test_no_target_main_episode_never_uses_episode_count_heuristics(
    tmp_path: Path,
    end_date: date | None,
    episode_airdates: tuple[date, ...],
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({}, episode_airdates={101: episode_airdates})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, end_date=end_date, episode_count=24)

    sync.run(SyncScope(target, target))

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == ()
    assert api.episode_calls == [101]


def test_target_season_tag_without_main_episode_does_not_auto_continue(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({}, episode_airdates={101: ()})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, tags=("2026年7月",))

    result = sync.run(SyncScope(target, target)).quarters[0]

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == ()
    assert result.continuing_unresolved == 1
    assert facts.review_issues[0].issue_code == "CONTINUING_EVIDENCE_UNRESOLVED"


def test_refresh_preserves_continuing_review_for_another_quarter(
    tmp_path: Path,
) -> None:
    future = Quarter(2026, 7)
    review = ReviewIssue(
        "CONTINUING_EVIDENCE_UNRESOLVED",
        future,
        "2026-07",
        {"season_tag": "2026-07"},
        "detected-1",
    )
    detail = _detail(101, cover=None)
    sync, repository = _sync(
        tmp_path,
        FakeApi({101: detail}),
        DiscoveryBatch((_candidate(101, MediaFormat.TV),)),
    )
    _store_existing(repository, 101, review_issues=(review,))

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.review_issues == (review,)


def test_refresh_clears_stale_target_continuing_review_outside_examined_set(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    review = ReviewIssue(
        "CONTINUING_EVIDENCE_UNRESOLVED",
        target,
        "2026-07",
        {"season_tag": "2026-07"},
        "detected-1",
    )
    sync, repository = _sync(tmp_path, FakeApi({}), DiscoveryBatch(()))
    _store_existing(
        repository,
        101,
        air_date=date(2026, 1, 2),
        premiere_quarter=Quarter(2026, 1),
        review_issues=(review,),
    )

    run = sync.run(SyncScope(target, target))

    assert run.exit_code == 0
    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.review_issues == ()


def test_previous_continuing_is_carried_forward_for_arbitrarily_long_runs(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 10)
    api = FakeApi({}, episode_airdates={101: (date(2026, 10, 4),)})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(
        repository,
        101,
        continuing=(_continuing(Quarter(2026, 7), "2026-07-04"),),
    )

    sync.run(SyncScope(target, target))

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == (
        _continuing(Quarter(2026, 7), "2026-07-04"),
        _continuing(target, "2026-10-04"),
    )
    assert api.episode_calls == [101]


def test_movie_is_never_a_continuing_probe_candidate(tmp_path: Path) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({}, episode_airdates={101: (date(2026, 7, 4),)})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, media=MediaFormat.MOVIE)

    sync.run(SyncScope(target, target))

    assert repository.list_subjects_appearing_in_quarter(target) == ()
    assert api.episode_calls == []


def test_episode_failure_preserves_existing_continuing_appearance(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    existing = _continuing(target, "2026-07-04")
    api = FakeApi({}, episode_failures=frozenset({101}))
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, continuing=(existing,))

    run = sync.run(SyncScope(target, target))

    assert run.exit_code == 1
    assert run.quarters[0].facts_status == FACTS_INCOMPLETE
    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == (existing,)
    assert run.quarters[0].errors[0]["code"] == "continuing_timeout"


def test_syncing_prior_quarter_backfills_an_already_managed_next_quarter(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    detail = _detail(101)
    api = FakeApi({101: detail}, episode_airdates={101: (date(2026, 7, 5),)})
    sync, repository = _sync(
        tmp_path, api, DiscoveryBatch((_candidate(101, MediaFormat.TV),))
    )
    repository.database.initialize()
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                target,
                FACTS_COMPLETE,
                FACTS_COMPLETE,
                0,
                0,
                "2026-08-10T00:00:00Z",
                "2026-08-10T00:00:00Z",
            ),
        )

    sync.run(SyncScope(QUARTER, QUARTER))

    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.continuing == (_continuing(target, "2026-07-05"),)
    state = repository.get_sync_state(target)
    assert state is not None and state.subject_count == 1


def test_failed_next_quarter_backfill_invalidates_stale_complete_state(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    detail = _detail(101, cover=None)
    api = FakeApi({101: detail}, episode_failures=frozenset({101}))
    sync, repository = _sync(
        tmp_path, api, DiscoveryBatch((_candidate(101, MediaFormat.TV),))
    )
    repository.database.initialize()
    with repository.transaction() as connection:
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                target,
                FACTS_COMPLETE,
                FACTS_COMPLETE,
                0,
                0,
                "attempt-1",
                "success-1",
            ),
        )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].facts_status == FACTS_COMPLETE
    assert any(
        warning["code"] == "continuing_backfill_failed"
        for warning in run.quarters[0].warnings
    )
    target_state = repository.get_sync_state(target)
    assert target_state is not None
    assert target_state.facts_status == FACTS_INCOMPLETE
    assert target_state.last_success_at == "success-1"
