"""Tests for subject-only sync orchestration and safe reports."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bgm_side_b.api import (
    ApiEpisode,
    ApiInfoboxItem,
    BangumiApiError,
    CandidateSubject,
    CharacterDetail,
    DiscoveryResult,
    DiscoveryStatistics,
    ImageResponse,
    PersonDetail,
    RelatedCharacter,
    SubjectDetail,
)
from bgm_side_b.config import ProjectSettings, load_rules
from bgm_side_b.database import Database
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import (
    SubjectSynchronizer,
    SyncScope,
    _normalise_summary,
    _source_infobox,
    parse_sync_scope,
)

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0"
    b"\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "subject_cases.json").read_text(
        encoding="utf-8"
    )
)
ENRICHED_FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "enriched_sync.json").read_text(
        encoding="utf-8"
    )
)


class FakeDiscovery:
    def __init__(
        self,
        candidates: tuple[CandidateSubject, ...],
        *,
        blacklisted: int = 0,
    ) -> None:
        self.candidates = candidates
        self.blacklisted = blacklisted
        self.calls: list[tuple[int, int]] = []

    def discover(self, year: int, month: int, _: frozenset[int]) -> DiscoveryResult:
        self.calls.append((year, month))
        return DiscoveryResult(
            self.candidates,
            DiscoveryStatistics(
                discovered=len(self.candidates) + self.blacklisted,
                blacklisted=self.blacklisted,
                needs_detail=len(self.candidates),
            ),
            (),
        )


class FakeApi:
    def __init__(
        self,
        details: dict[int, SubjectDetail],
        failures: set[int] | None = None,
        episodes: dict[int, tuple[ApiEpisode, ...]] | None = None,
        episode_failures: set[int] | None = None,
        roles: dict[int, tuple[RelatedCharacter, ...]] | None = None,
        role_failures: set[int] | None = None,
        characters: dict[int, CharacterDetail] | None = None,
        character_failures: set[int] | None = None,
        persons: dict[int, PersonDetail] | None = None,
        person_failures: set[int] | None = None,
        image_failures: set[str] | None = None,
        interruptions: set[int] | None = None,
    ) -> None:
        self.details = details
        self.failures = failures or set()
        self.episodes = episodes or {}
        self.episode_failures = episode_failures or set()
        self.roles = roles or {}
        self.role_failures = role_failures or set()
        self.characters = characters or {}
        self.character_failures = character_failures or set()
        self.persons = persons or {}
        self.person_failures = person_failures or set()
        self.image_failures = image_failures or set()
        self.interruptions = interruptions or set()
        self.calls: list[int] = []
        self.episode_calls: list[int] = []
        self.role_calls: list[int] = []
        self.character_calls: list[int] = []
        self.person_calls: list[int] = []
        self.image_calls: list[str] = []

    def get_subject(self, subject_id: int) -> SubjectDetail:
        self.calls.append(subject_id)
        if subject_id in self.interruptions:
            raise KeyboardInterrupt
        if subject_id in self.failures:
            raise BangumiApiError("network", "network request failed")
        return self.details[subject_id]

    def get_episodes(self, subject_id: int) -> tuple[ApiEpisode, ...]:
        self.episode_calls.append(subject_id)
        if subject_id in self.episode_failures:
            raise BangumiApiError("network", "network request failed")
        return self.episodes.get(subject_id, ())

    def get_related_characters(self, subject_id: int) -> tuple[RelatedCharacter, ...]:
        self.role_calls.append(subject_id)
        if subject_id in self.role_failures:
            raise BangumiApiError("network", "network request failed")
        return self.roles.get(subject_id, ())

    def get_character(self, character_id: int) -> CharacterDetail:
        self.character_calls.append(character_id)
        if character_id in self.character_failures:
            raise BangumiApiError("network", "network request failed")
        return self.characters[character_id]

    def get_person(self, person_id: int) -> PersonDetail:
        self.person_calls.append(person_id)
        if person_id in self.person_failures:
            raise BangumiApiError("network", "network request failed")
        return self.persons[person_id]

    def fetch_image(self, url: str, *, max_bytes: int | None = None) -> ImageResponse:
        self.image_calls.append(url)
        if url in self.image_failures:
            raise BangumiApiError("image_network", "image request failed")
        return ImageResponse(_PNG, "image/png", url)


@pytest.fixture
def rules() -> tuple[ProjectSettings, object, object]:
    settings, tags, sources = load_rules(Path(__file__).resolve().parents[1] / "config")
    return settings, tags, sources


def _candidate(subject_id: int, score: float = 7.5) -> CandidateSubject:
    return CandidateSubject(subject_id, "TV", "name", "中文", 1, score, 100)


def _synchronizer(
    tmp_path: Path,
    rules: tuple[ProjectSettings, object, object],
    api: FakeApi,
    discovery: FakeDiscovery,
) -> SubjectSynchronizer:
    settings, tag_rules, source_rules = rules
    repository = SubjectRepository(Database(tmp_path / "data" / "facts.sqlite3"))
    return SubjectSynchronizer(
        repository,
        api,
        settings,
        tag_rules,
        source_rules,
        discovery=discovery,
        reports_directory=tmp_path / "reports",
    )


def test_scope_parsing_is_ordered_and_rejects_invalid_quarters() -> None:
    assert parse_sync_scope(["2022", "1"]) == SyncScope((2022,), 1)
    assert parse_sync_scope(["2022-2023"]).quarters[0] == (2022, 1)
    with pytest.raises(ValueError):
        parse_sync_scope(["2022", "2"])


def test_summary_paragraphs_and_structured_infobox_values_are_preserved() -> None:
    assert _normalise_summary(" first\n line\n\n\n second ") == "first line\n\nsecond"
    values = _source_infobox(
        [ApiInfoboxItem("source", [{"v": "manga"}, {"v": "novel"}])]
    )
    assert [item.value for item in values] == ["manga", "novel"]


def _episodes(*payloads: dict[str, object]) -> tuple[ApiEpisode, ...]:
    return tuple(
        ApiEpisode.from_payload(payload, position)
        for position, payload in enumerate(payloads)
    )


def test_sync_writes_tv_subject_and_safe_reports(
    tmp_path: Path,
    rules: tuple[ProjectSettings, object, object],
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    api = FakeApi({101: detail})
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101),), blacklisted=1),
    )

    run = sync.run(SyncScope((2022,), 1))

    stats = run.quarter_stats[0]
    assert stats.created == 1
    assert stats.blacklisted == 1
    assert run.exit_code == 0
    assert run.sync_report.exists()
    payload = run.sync_report.read_text(encoding="utf-8")
    assert str(tmp_path) not in payload
    assert "authorization" not in payload.lower()
    report = json.loads(payload)
    assert report["app_version"]
    assert report["scope"]["force_images"] is False
    assert report["quarters"][0]["subject_details_requested"] == 1
    audit = json.loads(run.tag_audit_report.read_text(encoding="utf-8"))
    assert audit["tags"][0]["raw_tag"] == "喜剧"
    assert audit["tags"][0]["examples"] == [
        {"subject_id": 101, "title": "电视中文名"}
    ]


def test_incremental_sync_refreshes_detail_rating_and_preserves_missing_values(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    api = FakeApi({101: detail})
    discovery = FakeDiscovery((_candidate(101, 7.5),))
    sync = _synchronizer(tmp_path, rules, api, discovery)
    sync.run(SyncScope((2022,), 1))

    api.calls.clear()
    api.details[101] = SubjectDetail.from_payload(
        {**FIXTURES["tv"], "rating": {"score": 8.2, "total": 200}}
    )
    sync.discovery = FakeDiscovery((_candidate(101, 8.2),))
    run = sync.run(SyncScope((2022,), 1))

    assert api.calls == [101]
    assert run.quarter_stats[0].subject_details_requested == 1
    assert run.quarter_stats[0].ratings_updated == 1
    connection = sync.repository.database.connect()
    try:
        score = connection.execute(
            "SELECT rating_score FROM subjects WHERE id = 101"
        ).fetchone()[0]
        assert score == 8.2
    finally:
        connection.close()

    api.calls.clear()
    api.details[101] = SubjectDetail.from_payload({**FIXTURES["tv"], "rating": {}})
    sync.run(SyncScope((2022,), 1))
    connection = sync.repository.database.connect()
    try:
        assert connection.execute(
            "SELECT rating_score FROM subjects WHERE id = 101"
        ).fetchone()[0] == 8.2
    finally:
        connection.close()

    api.calls.clear()
    sync.run(SyncScope((2022,), 1), force=True)
    assert api.calls == [101]


def test_missing_date_and_local_failure_do_not_stop_other_subjects(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    missing = SubjectDetail.from_payload(FIXTURES["missing_date"])
    api = FakeApi({101: detail, 105: missing}, failures={106})
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101), _candidate(105), _candidate(106))),
    )

    run = sync.run(SyncScope((2022,), 1))

    assert run.quarter_stats[0].created == 1
    assert run.quarter_stats[0].missing_date == 1
    assert run.quarter_stats[0].failed == 1
    assert run.exit_code == 1
    assert sync.repository.subject_exists(101)


def test_episode_snapshot_replaces_old_rows_and_builds_continuing_quarters(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail_payload = {
        **FIXTURES["tv"],
        "eps": 3,
        "total_episodes": 3,
        "infobox": [{"key": "播放结束", "value": "2022-10-10"}],
    }
    episodes = _episodes(
        {
            "id": 500,
            "type": 0,
            "ep": 1,
            "sort": 1,
            "name": "one",
            "name_cn": "",
            "airdate": "2022-01-08",
            "duration": "24:00",
            "duration_seconds": 1440,
        },
        {
            "id": 501,
            "type": 0,
            "ep": 2,
            "sort": 2,
            "name": "two",
            "name_cn": "第二话",
            "airdate": "2022-04-08",
            "duration": "24:00",
            "duration_seconds": 1440,
        },
        {
            "id": 502,
            "type": 0,
            "ep": 3,
            "sort": 3,
            "name": "three",
            "name_cn": "",
            "airdate": "2022-07-08",
            "duration": "24:00",
            "duration_seconds": 0,
        },
    )
    api = FakeApi(
        {101: SubjectDetail.from_payload(detail_payload)}, episodes={101: episodes}
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 0
    assert api.episode_calls == [101]
    connection = sync.repository.database.connect()
    try:
        rows = connection.execute(
            """
            SELECT id, name_cn, duration_seconds, position FROM episodes
            WHERE subject_id = 101 ORDER BY position
            """
        ).fetchall()
        quarters = connection.execute(
            """
            SELECT year, month, appearance_kind, evidence_type
            FROM subject_quarters WHERE subject_id = 101
            ORDER BY year, month, appearance_kind
            """
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in rows] == [
        (500, "", 1440, 0),
        (501, "第二话", 1440, 1),
        (502, "", None, 2),
    ]
    assert [tuple(row) for row in quarters] == [
        (2022, 1, "new", "air_date"),
        (2022, 4, "continuing", "episode_air_date"),
        (2022, 7, "continuing", "episode_air_date"),
        (2022, 10, "continuing", "end_date"),
    ]

    api.episodes[101] = episodes[:1]
    rerun = sync.run(SyncScope((2022,), 1), force=True)
    assert "episode_count_conflict:101" in rerun.quarter_stats[0].warnings
    assert sync.repository.main_episode_count(101) == 1

    connection = sync.repository.database.connect()
    try:
        continuations = connection.execute(
            """
            SELECT year, month FROM subject_quarters
            WHERE subject_id = 101 AND appearance_kind = 'continuing'
            ORDER BY year, month
            """
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in continuations] == [(2022, 10)]


def test_episode_failure_preserves_the_last_successful_snapshot(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail_payload = {**FIXTURES["tv"], "eps": 2, "total_episodes": 2}
    api = FakeApi(
        {101: SubjectDetail.from_payload(detail_payload)},
        episodes={
            101: _episodes(
                {
                    "id": 500,
                    "type": 0,
                    "ep": 1,
                    "sort": 1,
                    "name": "one",
                    "name_cn": "",
                    "airdate": "2022-01-08",
                    "duration": "24:00",
                    "duration_seconds": 1440,
                }
            )
        },
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))
    sync.run(SyncScope((2022,), 1))
    api.episode_failures.add(101)

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 1
    assert sync.repository.main_episode_count(101) == 1
    state = sync.repository.get_sync_state("subject", 101, "episodes")
    assert state is not None and state.status == "failed"
    assert state.failure_count == 1


def test_completed_tv_skips_future_episode_refresh_unless_forced(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail_payload = {**FIXTURES["tv"], "eps": 1, "total_episodes": 1}
    api = FakeApi(
        {101: SubjectDetail.from_payload(detail_payload)},
        episodes={
            101: _episodes(
                {
                    "id": 500,
                    "type": 0,
                    "ep": 1,
                    "sort": 1,
                    "name": "one",
                    "name_cn": "",
                    "airdate": "2022-01-08",
                    "duration": "24:00",
                    "duration_seconds": 1440,
                }
            )
        },
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))
    sync.run(SyncScope((2022,), 1))
    api.episode_calls.clear()
    sync.discovery = FakeDiscovery(())

    sync.run(SyncScope((2022,), 7))
    assert api.episode_calls == []

    sync.run(SyncScope((2022,), 7), force=True)
    assert api.episode_calls == [101]


def test_unknown_completion_keeps_refreshing_and_movies_never_continue(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    tv_detail = SubjectDetail.from_payload(
        {**FIXTURES["tv"], "eps": None, "total_episodes": None}
    )
    movie_detail = SubjectDetail.from_payload(
        {**FIXTURES["movie"], "eps": 1, "total_episodes": 1}
    )
    api = FakeApi(
        {101: tv_detail, 102: movie_detail},
        episodes={
            101: _episodes(
                {
                    "id": 500,
                    "type": 0,
                    "ep": 1,
                    "sort": 1,
                    "name": "one",
                    "name_cn": "",
                    "airdate": "2022-01-08",
                    "duration": "24:00",
                    "duration_seconds": 1440,
                }
            ),
            102: _episodes(
                {
                    "id": 600,
                    "type": 0,
                    "ep": 1,
                    "sort": 1,
                    "name": "movie",
                    "name_cn": "",
                    "airdate": "2022-04-08",
                    "duration": "90:00",
                    "duration_seconds": 5400,
                }
            ),
        },
    )
    movie_candidate = CandidateSubject(102, "剧场版", "movie", None, 3, 7.0, 10)
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101), movie_candidate)),
    )
    sync.run(SyncScope((2022,), 1))
    api.episode_calls.clear()
    sync.discovery = FakeDiscovery(())

    sync.run(SyncScope((2022,), 4))

    assert api.episode_calls == [101]
    connection = sync.repository.database.connect()
    try:
        movie_continuations = connection.execute(
            """
            SELECT COUNT(*) FROM subject_quarters
            WHERE subject_id = 102 AND appearance_kind = 'continuing'
            """
        ).fetchone()[0]
    finally:
        connection.close()
    assert movie_continuations == 0


def test_main_role_snapshot_keeps_all_actors_when_one_detail_fails(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    roles = tuple(
        RelatedCharacter.from_payload(payload)
        for payload in ENRICHED_FIXTURES["roles"]
    )
    api = FakeApi(
        {101: SubjectDetail.from_payload(FIXTURES["tv"])},
        roles={101: roles},
        characters={
            100: CharacterDetail.from_payload(ENRICHED_FIXTURES["character_detail"])
        },
        persons={
            200: PersonDetail.from_payload(ENRICHED_FIXTURES["person_detail"])
        },
        person_failures={201},
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 1
    assert api.role_calls == [101]
    assert api.character_calls == [100]
    assert api.person_calls == [200, 201]
    assert api.image_calls == ["https://img.example/character-large.jpg"]
    connection = sync.repository.database.connect()
    try:
        characters = connection.execute(
            "SELECT id, original_name, chinese_name FROM characters ORDER BY id"
        ).fetchall()
        persons = connection.execute(
            "SELECT id, original_name, chinese_name FROM persons ORDER BY id"
        ).fetchall()
        relations = connection.execute(
            """
            SELECT character_id, role, position FROM subject_characters
            WHERE subject_id = 101 ORDER BY position
            """
        ).fetchall()
        voices = connection.execute(
            """
            SELECT character_id, person_id, language, position FROM character_voices
            WHERE subject_id = 101 ORDER BY position
            """
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in characters] == [
        (100, "Original Character", "中文角色名")
    ]
    assert [tuple(row) for row in persons] == [
        (200, "Actor One", "中文声优名"),
        (201, "Actor Two", None),
    ]
    assert [tuple(row) for row in relations] == [(100, "主角", 0)]
    assert [tuple(row) for row in voices] == [(100, 200, None, 0), (100, 201, None, 1)]
    person_state = sync.repository.get_sync_state("person", 201, "person_detail")
    assert person_state is not None and person_state.status == "failed"

    api.role_calls.clear()
    retry = sync.run(SyncScope((2022,), 1))
    assert retry.exit_code == 1
    assert api.role_calls == [101]
    assert api.character_calls == [100]
    assert api.person_calls == [200, 201, 201]

    api.person_failures.clear()
    api.persons[201] = PersonDetail.from_payload(
        ENRICHED_FIXTURES["person_detail_missing"]
    )
    completed = sync.run(SyncScope((2022,), 1))
    assert completed.exit_code == 0
    assert api.role_calls == [101, 101]
    assert api.person_calls == [200, 201, 201, 201]
    person_state = sync.repository.get_sync_state("person", 201, "person_detail")
    assert person_state is not None and person_state.status == "success"

    api.role_calls.clear()
    sync.run(SyncScope((2022,), 1))
    assert api.role_calls == []

    api.role_failures.add(101)
    rerun = sync.run(SyncScope((2022,), 1), force=True)
    assert rerun.exit_code == 1
    assert sync.repository.get_sync_state("subject", 101, "roles").status == "failed"
    connection = sync.repository.database.connect()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM character_voices WHERE subject_id = 101"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_character_detail_failure_keeps_relation_and_records_failed_state(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    role = RelatedCharacter.from_payload(
        {
            "id": 100,
            "name": "Fallback Character",
            "summary": "Fallback summary",
            "relation": "主角",
            "actors": [],
        }
    )
    api = FakeApi(
        {101: SubjectDetail.from_payload(FIXTURES["tv"])},
        roles={101: (role,)},
        character_failures={100},
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 1
    assert api.character_calls == [100]
    state = sync.repository.get_sync_state("character", 100, "character_detail")
    assert state is not None and state.status == "failed"
    connection = sync.repository.database.connect()
    try:
        row = connection.execute(
            "SELECT original_name, summary FROM characters WHERE id = 100"
        ).fetchone()
        relation = connection.execute(
            "SELECT role FROM subject_characters WHERE subject_id = 101"
        ).fetchone()
    finally:
        connection.close()
    assert tuple(row) == ("Fallback Character", "Fallback summary")
    assert relation["role"] == "主角"


def test_media_failure_keeps_structured_subject_facts(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    cover_url = "https://img.example/cover.jpg"
    detail = SubjectDetail.from_payload(
        {**FIXTURES["tv"], "images": {"large": cover_url}}
    )
    api = FakeApi(
        {101: detail}, image_failures={cover_url}
    )
    sync = _synchronizer(tmp_path, rules, api, FakeDiscovery((_candidate(101),)))

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 1
    assert sync.repository.subject_exists(101)
    record = sync.repository.get_media_record("subject", 101, "cover")
    assert record is not None and record.status == "failed"
    assert run.quarter_stats[0].media_failed == 1
    report = json.loads(run.sync_report.read_text(encoding="utf-8"))
    failure = report["quarters"][0]["failures"][0]
    assert set(failure) == {
        "quarter",
        "subject_id",
        "entity_type",
        "entity_id",
        "data_type",
        "error_code",
        "summary",
        "retry_count",
    }


def test_interrupt_writes_partial_safe_report_and_returns_130(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    api = FakeApi(
        {101: SubjectDetail.from_payload(FIXTURES["tv"])}, interruptions={106}
    )
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101), _candidate(106))),
    )

    run = sync.run(SyncScope((2022,), 1))

    assert run.exit_code == 130
    assert sync.repository.subject_exists(101)
    report = json.loads(run.sync_report.read_text(encoding="utf-8"))
    assert "interrupted" in report["quarters"][0]["warnings"]
    assert not list((tmp_path / "tmp").glob("*.part"))


def test_blacklisted_candidate_is_deleted_without_followup_requests(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    settings, tag_rules, source_rules = rules
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    initial_api = FakeApi({101: detail})
    initial = SubjectSynchronizer(
        SubjectRepository(Database(tmp_path / "data" / "facts.sqlite3")),
        initial_api,
        settings,
        tag_rules,
        source_rules,
        discovery=FakeDiscovery((_candidate(101),)),
        reports_directory=tmp_path / "reports",
    )
    initial.run(SyncScope((2022,), 1))
    blacklisted_api = FakeApi({101: detail})
    blocked = SubjectSynchronizer(
        initial.repository,
        blacklisted_api,
        replace(settings, excluded_subject_ids=frozenset({101})),
        tag_rules,
        source_rules,
        discovery=FakeDiscovery((_candidate(101),)),
        reports_directory=tmp_path / "reports",
    )

    run = blocked.run(SyncScope((2022,), 1))

    assert run.exit_code == 0
    assert not blocked.repository.subject_exists(101)
    assert blacklisted_api.calls == []
    assert blacklisted_api.episode_calls == []
    assert blacklisted_api.role_calls == []
    assert blacklisted_api.image_calls == []
    connection = blocked.repository.database.connect()
    try:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM sync_states
            WHERE entity_type = 'subject' AND entity_id = 101
            """
        ).fetchone()[0] == 0
    finally:
        connection.close()
