"""Risk-focused tests for the reduced 2026-04 Japan-TV sync path."""

import json
from io import StringIO
from pathlib import Path

import pytest

from bgm_side_b.api import (
    ApiEpisode,
    BangumiApiError,
    CandidateSubject,
    DiscoveryResult,
    DiscoveryStatistics,
    ImageResponse,
    SubjectDetail,
)
from bgm_side_b.config import ProjectSettings, load_rules
from bgm_side_b.database import Database
from bgm_side_b.progress import ConsoleProgressReporter, ProgressReporter
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import (
    SubjectSynchronizer,
    SyncScope,
    _normalise_summary,
    parse_sync_scope,
    validate_release_scope,
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0"
    b"\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeDiscovery:
    def __init__(self, candidates: tuple[CandidateSubject, ...]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[int, int]] = []

    def discover(self, year: int, month: int, _: frozenset[int]) -> DiscoveryResult:
        self.calls.append((year, month))
        return DiscoveryResult(
            self.candidates,
            DiscoveryStatistics(
                discovered=len(self.candidates), needs_detail=len(self.candidates)
            ),
            (),
        )


class FakeApi:
    def __init__(
        self,
        details: dict[int, SubjectDetail],
        *,
        episodes: dict[int, tuple[ApiEpisode, ...]] | None = None,
        image_failures: set[str] | None = None,
        interruptions: set[int] | None = None,
    ) -> None:
        self.details = details
        self.episodes = episodes or {}
        self.image_failures = image_failures or set()
        self.interruptions = interruptions or set()
        self.calls: list[int] = []
        self.episode_calls: list[int] = []
        self.image_calls: list[str] = []
        self.role_calls = 0
        self.character_calls = 0
        self.person_calls = 0

    def get_subject(self, subject_id: int) -> SubjectDetail:
        self.calls.append(subject_id)
        if subject_id in self.interruptions:
            raise KeyboardInterrupt
        return self.details[subject_id]

    def get_episodes(self, subject_id: int) -> tuple[ApiEpisode, ...]:
        self.episode_calls.append(subject_id)
        return self.episodes.get(subject_id, ())

    def fetch_image(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        request_label: str = "image",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> ImageResponse:
        self.image_calls.append(url)
        if url in self.image_failures:
            raise BangumiApiError("image_network", "image request failed")
        return ImageResponse(_PNG, "image/png", url)

    def get_related_characters(self, _: int) -> tuple[object, ...]:
        self.role_calls += 1
        raise AssertionError("reduced sync must not request subject characters")

    def get_character(self, _: int) -> object:
        self.character_calls += 1
        raise AssertionError("reduced sync must not request character details")

    def get_person(self, _: int) -> object:
        self.person_calls += 1
        raise AssertionError("reduced sync must not request person details")


@pytest.fixture
def rules() -> tuple[ProjectSettings, object, object]:
    return load_rules(Path(__file__).resolve().parents[1] / "config")


def _candidate(subject_id: int, platform: str = "TV") -> CandidateSubject:
    return CandidateSubject(
        subject_id, platform, f"subject {subject_id}", None, 1, 7.5, 100
    )


def _detail(
    subject_id: int,
    country: object = "日本",
    *,
    platform: str = "TV",
    date: str = "2026-04-02",
    cover: str | None = "https://images.example/cover.png",
    eps: int = 1,
    infobox_key: str = "制片国家/地区",
    finished: bool = False,
) -> SubjectDetail:
    infobox = [] if country is None else [{"key": infobox_key, "value": country}]
    if finished:
        infobox.append({"key": "播放结束", "value": "2026-06-30"})
    return SubjectDetail.from_payload(
        {
            "id": subject_id,
            "name": f"Original {subject_id}",
            "name_cn": f"中文 {subject_id}",
            "platform": platform,
            "date": date,
            "eps": eps,
            "total_episodes": eps,
            "rating": {"score": 7.5, "total": 100},
            "infobox": infobox,
            "tags": [{"name": "喜剧", "count": 1}],
            "images": {"large": cover} if cover else {},
        }
    )


def _episodes(subject_id: int) -> tuple[ApiEpisode, ...]:
    return (
        ApiEpisode.from_payload(
            {
                "id": subject_id * 10,
                "type": 0,
                "ep": 1,
                "sort": 1,
                "name": "episode one",
                "airdate": "2026-04-09",
            },
            0,
        ),
    )


def _synchronizer(
    tmp_path: Path,
    rules: tuple[ProjectSettings, object, object],
    api: FakeApi,
    discovery: FakeDiscovery,
    reporter: ProgressReporter | None = None,
) -> SubjectSynchronizer:
    settings, tag_rules, source_rules = rules
    return SubjectSynchronizer(
        SubjectRepository(Database(tmp_path / "data" / "facts.sqlite3")),
        api,
        settings,
        tag_rules,
        source_rules,
        discovery=discovery,
        reports_directory=tmp_path / "reports",
        reporter=reporter,
    )


def test_scope_is_reduced_to_the_single_configured_quarter(
    rules: tuple[ProjectSettings, object, object]
) -> None:
    settings, _, _ = rules
    assert parse_sync_scope(["2026", "4"]) == SyncScope((2026,), 4)
    validate_release_scope(SyncScope((2026,), 4), settings)
    for values in (["2022-2025"], ["2026"], ["2026", "1"], ["2026", "7"]):
        with pytest.raises(ValueError, match="2026-04"):
            validate_release_scope(parse_sync_scope(values), settings)


def test_sync_keeps_only_japan_tv_and_writes_country_audit(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    api = FakeApi(
        {
            101: _detail(101),
            102: _detail(102, "中国"),
            103: _detail(103, None),
            104: _detail(104, "日本风"),
            105: _detail(105, "日本", infobox_key="国家/地区"),
        },
        episodes={101: _episodes(101), 105: _episodes(105)},
    )
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery(tuple(_candidate(subject_id) for subject_id in range(101, 106))),
    )

    run = sync.run(SyncScope((2026,), 4))

    stats = run.quarter_stats[0]
    assert run.exit_code == 0
    assert (stats.country_included_japan, stats.country_excluded_not_japan) == (2, 1)
    assert (
        stats.country_excluded_missing,
        stats.country_excluded_unparseable,
    ) == (1, 1)
    assert api.episode_calls == [101, 105]
    assert len(api.image_calls) == 2
    assert (api.role_calls, api.character_calls, api.person_calls) == (0, 0, 0)
    assert not (tmp_path / "media" / "characters").exists()
    connection = sync.repository.database.connect()
    try:
        quarters = connection.execute(
            "SELECT year, month, appearance_kind FROM subject_quarters "
            "ORDER BY subject_id"
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in quarters] == [(2026, 4, "new"), (2026, 4, "new")]
    report = json.loads(run.sync_report.read_text(encoding="utf-8"))
    assert report["scope"]["release_quarters"] == ["2026-04"]
    assert report["scope"]["formats"] == ["tv"]
    assert report["scope"]["country_filter"] == "structured_contains_japan"
    assert report["scope"]["continuations"] is False
    assert report["scope"]["roles"] is False
    audit = json.loads(run.country_audit_report.read_text(encoding="utf-8"))
    assert [row["decision"] for row in audit["subjects"]] == [
        "included_japan",
        "excluded_not_japan",
        "excluded_missing_country",
        "excluded_unparseable_country",
        "included_japan",
    ]


def test_conflicting_country_movie_and_wrong_quarter_never_request_media(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    api = FakeApi(
        {
            101: _detail(101, "日本"),
            102: _detail(102, "中国", infobox_key="国家/地区"),
            103: _detail(103, "日本", platform="剧场版"),
            104: _detail(104, "日本", date="2026-07-01"),
        },
        episodes={101: _episodes(101)},
    )
    conflict = SubjectDetail.from_payload(
        {
            **{
                "id": 102,
                "name": "Conflict",
                "platform": "TV",
                "date": "2026-04-02",
                "infobox": [
                    {"key": "制片国家/地区", "value": "日本"},
                    {"key": "国家/地区", "value": "中国"},
                ],
            }
        }
    )
    api.details[102] = conflict
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery(tuple(_candidate(subject_id) for subject_id in range(101, 105))),
    )

    run = sync.run(SyncScope((2026,), 4))

    stats = run.quarter_stats[0]
    assert stats.country_excluded_conflict == 1
    assert stats.unsupported == 1
    assert stats.ownership_mismatch == 1
    assert api.episode_calls == [101]
    assert len(api.image_calls) == 1


def test_completed_subject_reuses_episodes_but_keeps_detail_and_cover_incremental(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail = _detail(101, finished=True)
    api = FakeApi({101: detail}, episodes={101: _episodes(101)})
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    sync.run(SyncScope((2026,), 4))
    second = sync.run(SyncScope((2026,), 4))

    assert api.calls == [101, 101]
    assert api.episode_calls == [101]
    assert second.quarter_stats[0].updated == 1


def test_cover_failure_keeps_structured_subject_and_no_character_requests(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    cover = "https://images.example/broken.png"
    api = FakeApi(
        {101: _detail(101, cover=cover)},
        episodes={101: _episodes(101)},
        image_failures={cover},
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    run = sync.run(SyncScope((2026,), 4))

    assert run.exit_code == 1
    assert sync.repository.subject_exists(101)
    assert (api.role_calls, api.character_calls, api.person_calls) == (0, 0, 0)


def test_interrupt_writes_partial_reports_without_scope_expansion(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    stream = StringIO()
    api = FakeApi(
        {101: _detail(101)}, episodes={101: _episodes(101)}, interruptions={102}
    )
    with ConsoleProgressReporter("sync", mode="plain", stream=stream) as reporter:
        run = _synchronizer(
            tmp_path,
            rules,
            api,
            FakeDiscovery((_candidate(101), _candidate(102))),
            reporter,
        ).run(SyncScope((2026,), 4))

    assert run.exit_code == 130
    assert run.country_audit_report.exists()
    assert "\r" not in stream.getvalue()


def test_summary_normalisation_remains_deterministic() -> None:
    assert _normalise_summary(" first\n line\n\n\n second ") == "first line\n\nsecond"
