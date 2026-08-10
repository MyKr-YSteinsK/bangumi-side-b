"""Atomic clean-schema discovery, fact sync, cover, and recovery coverage."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from bgm_side_b.admission import QuarterOverride
from bgm_side_b.api import BangumiApiError, ImageResponse, SubjectDetail
from bgm_side_b.archive_config import (
    ArchiveSyncSettings,
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
from bgm_side_b.repository import (
    QuarterAppearance,
    QuarterSyncState,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)
from bgm_side_b.sync import (
    FACTS_COMPLETE,
    FACTS_INCOMPLETE,
    ArchiveSynchronizer,
    SyncError,
    SyncScope,
    parse_sync_scope,
)

ROOT = Path(__file__).resolve().parents[1]
QUARTER = Quarter(2026, 4)


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
    air_date: str = "2026-04-02",
    cover: str | None = "https://images.example/cover.png",
    platform: str | None = None,
    meta_tags: tuple[str, ...] = (),
) -> SubjectDetail:
    infobox = [] if country is None else [{"key": "国家/地区", "value": country}]
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
            "rating": {"score": 7.5, "total": 100},
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
) -> tuple[ArchiveSynchronizer, SubjectRepository]:
    database = Database(tmp_path / "data" / "facts.sqlite3")
    repository = SubjectRepository(database)
    synchronizer = ArchiveSynchronizer(
        repository,
        api,  # type: ignore[arg-type]
        settings or load_archive_sync_settings(ROOT / "config" / "bangumi.toml"),
        load_archive_source_rules(ROOT / "config" / "source-rules.toml"),
        overrides_path=tmp_path / "quarter-overrides.toml",
        workspace_directory=tmp_path,
        reports_directory=tmp_path / "reports",
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
    premiere_quarter: Quarter = QUARTER,
    continuing: tuple[QuarterAppearance, ...] = (),
    tags: tuple[str, ...] = (),
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
        premiere=QuarterAppearance(
            premiere_quarter,
            QuarterAppearanceKind.PREMIERE,
            QuarterAssignmentSource.AUTOMATIC,
            "air_date",
            air_date.isoformat(),
        ),
        continuing=continuing,
    )
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)


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


def test_full_browse_snapshot_uses_public_region_without_subject_get(
    tmp_path: Path,
) -> None:
    detail = _detail(101, country=None, meta_tags=("日本", "TV"))
    candidate = DiscoveredSubject(
        101,
        frozenset({MediaFormat.TV}),
        frozenset({date(2026, 4, 2)}),
        frozenset({2}),
        ("browse:TV:2026-04",),
        detail,
    )
    api = FakeApi({101: detail})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch((candidate,)))

    assert sync.run(SyncScope(QUARTER, QUARTER)).exit_code == 0
    assert api.subject_calls == []
    facts = repository.get_subject_facts(101)
    assert facts is not None
    assert facts.subject.japanese.evidence_type == "bangumi_public_region_tag"


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


def test_search_only_media_does_not_enter_normal_premiere_sync(
    tmp_path: Path,
) -> None:
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
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    result = run.quarters[0]
    assert result.facts_status == FACTS_COMPLETE
    assert result.external_reviews == ()
    assert repository.get_subject_facts(202) is None
    report = json.loads(run.report_path.read_text(encoding="utf-8"))
    assert report["review_count"] == 0
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


def test_cover_failure_does_not_rollback_complete_facts(tmp_path: Path) -> None:
    api = FakeApi({101: _detail(101)}, image_failure=True)
    sync, repository = _sync(
        tmp_path, api, DiscoveryBatch((_candidate(101, MediaFormat.TV),))
    )

    run = sync.run(SyncScope(QUARTER, QUARTER))

    assert run.exit_code == 0
    assert run.quarters[0].covers_status == "incomplete"
    assert repository.get_subject_facts(101) is not None


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


def test_end_date_crossing_target_creates_continuing_without_episode_probe(
    tmp_path: Path,
) -> None:
    target = Quarter(2026, 7)
    api = FakeApi({})
    sync, repository = _sync(tmp_path, api, DiscoveryBatch(()))
    _store_existing(repository, 101, end_date=date(2026, 7, 1))

    result = sync.run(SyncScope(target, target)).quarters[0]

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
