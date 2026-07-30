"""Tests for minimal Bangumi DTOs, retrying HTTP, and candidate discovery."""

import json
import time
from collections.abc import Callable
from io import StringIO
from pathlib import Path

import httpx
import pytest

from bgm_side_b.api import (
    DEFAULT_USER_AGENT,
    MOVIE_CATEGORY,
    TV_CATEGORY,
    BangumiApiClient,
    BangumiApiError,
    CharacterDetail,
    PersonDetail,
    QuarterlyDiscovery,
    ResponseShapeError,
    SubjectDetail,
    is_default_image_url,
)
from bgm_side_b.progress import ConsoleProgressReporter

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


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> BangumiApiClient:
    return BangumiApiClient(
        client=httpx.Client(
            base_url="https://api.bgm.tv/v0",
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ),
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )


def test_subject_detail_handles_optional_fields_and_preserves_structure() -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    assert detail.air_date.isoformat() == "2022-01-08"
    assert detail.rating_score == 7.5
    assert detail.tags[0].count == 12
    assert detail.infobox[0].value == "漫画"

    assert SubjectDetail.from_payload(FIXTURES["missing_date"]).air_date is None
    assert SubjectDetail.from_payload(FIXTURES["no_chinese_title"]).name_cn == ""
    assert SubjectDetail.from_payload(FIXTURES["no_rating"]).rating_score is None
    infobox_value = SubjectDetail.from_payload(
        FIXTURES["infobox_multi_value"]
    ).infobox[0].value
    assert infobox_value == [
        {"v": "Alias A"},
        {"v": "Alias B"},
    ]
    assert SubjectDetail.from_payload(FIXTURES["raw_tags"]).tags[0].name == "未映射"
    with_unknown = {**FIXTURES["tv"], "future_field": {"ignored": True}}
    assert SubjectDetail.from_payload(with_unknown).subject_id == 101


def test_subject_detail_requires_an_id() -> None:
    with pytest.raises(ResponseShapeError) as error:
        SubjectDetail.from_payload({"name": "missing id"})
    assert error.value.code == "missing_subject_id"


def test_browse_paginates_and_retries_rate_limits() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["User-Agent"] == DEFAULT_USER_AGENT
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "limit": 2,
                    "offset": 0,
                    "data": [FIXTURES["tv"], FIXTURES["movie"]],
                },
            )
        return httpx.Response(
            200,
            json={"total": 3, "limit": 2, "offset": 2, "data": [FIXTURES["web"]]},
        )

    client = BangumiApiClient(
        client=httpx.Client(
            base_url="https://api.bgm.tv/v0",
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ),
        sleeper=delays.append,
        jitter=lambda: 0,
    )
    subjects = client.browse_subjects(
        year=2022,
        month=1,
        category=TV_CATEGORY,
        limit=2,
    )

    assert [subject.subject_id for subject in subjects] == [101, 102, 103]
    assert delays == [2.0]
    assert calls == 3
    assert client.metrics.json_retries == 1


def test_non_transient_errors_do_not_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"secret": "not retained"})

    with pytest.raises(BangumiApiError) as error:
        _client(handler).get_subject(999)
    assert error.value.code == "http_404"
    assert error.value.summary == "HTTP 404"
    assert calls == 1


def test_server_errors_retry_with_exponential_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=FIXTURES["tv"])

    client = BangumiApiClient(
        client=httpx.Client(
            base_url="https://api.bgm.tv/v0",
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ),
        sleeper=delays.append,
        jitter=lambda: 0,
    )
    assert client.get_subject(101).subject_id == 101
    assert calls == 2
    assert delays == [0.5]


def test_timeouts_retry_and_return_safe_failure() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("connection information must not leak")

    client = BangumiApiClient(
        client=httpx.Client(
            base_url="https://api.bgm.tv/v0",
            transport=httpx.MockTransport(handler),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ),
        max_retries=2,
        sleeper=delays.append,
        jitter=lambda: 0,
    )
    with pytest.raises(BangumiApiError) as error:
        client.get_subject(101)
    assert error.value.code == "timeout"
    assert error.value.summary == "request timed out"
    assert calls == 3
    assert delays == [0.5, 1.0]


def test_retry_progress_is_immediate_safe_and_labels_retry_after() -> None:
    calls = 0
    stream = StringIO()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=FIXTURES["tv"])

    with ConsoleProgressReporter("sync", mode="plain", stream=stream) as reporter:
        client = BangumiApiClient(
            client=httpx.Client(
                base_url="https://api.bgm.tv/v0",
                transport=httpx.MockTransport(handler),
                headers={"User-Agent": DEFAULT_USER_AGENT},
            ),
            sleeper=lambda _: None,
            jitter=lambda: 0,
            reporter=reporter,
        )
        assert client.get_subject(101).subject_id == 101

    output = stream.getvalue()
    assert "[重试]" in output
    assert "作品详情" in output
    assert "Retry-After" in output
    assert "https://" not in output
    assert "authorization" not in output.lower()


def test_slow_request_uses_the_current_api_activity_for_heartbeat() -> None:
    stream = StringIO()

    def handler(_: httpx.Request) -> httpx.Response:
        time.sleep(0.03)
        return httpx.Response(200, json=FIXTURES["tv"])

    with ConsoleProgressReporter(
        "sync", mode="plain", stream=stream, heartbeat_interval_seconds=0.01
    ) as reporter:
        client = BangumiApiClient(
            client=httpx.Client(
                base_url="https://api.bgm.tv/v0",
                transport=httpx.MockTransport(handler),
                headers={"User-Agent": DEFAULT_USER_AGENT},
            ),
            reporter=reporter,
        )
        assert client.get_subject(101).subject_id == 101

    output = stream.getvalue()
    assert "仍在运行" in output
    assert "等待 Bangumi API" in output
    assert "subject 101" in output


def test_quarterly_discovery_deduplicates_and_filters_before_detail() -> None:
    pages = {
        (1, TV_CATEGORY): [FIXTURES["tv"], FIXTURES["web"]],
        (1, MOVIE_CATEGORY): [FIXTURES["movie"]],
        (2, TV_CATEGORY): [FIXTURES["tv"], FIXTURES["missing_date"]],
        (2, MOVIE_CATEGORY): [],
        (3, TV_CATEGORY): [FIXTURES["ova"]],
        (3, MOVIE_CATEGORY): [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        month = int(request.url.params["month"])
        category = int(request.url.params["cat"])
        return httpx.Response(
            200,
            json={
                "total": len(pages[(month, category)]),
                "data": pages[(month, category)],
            },
        )

    result = QuarterlyDiscovery(_client(handler)).discover(2022, 1, {102})

    assert [candidate.subject_id for candidate in result.candidates] == [101, 105]
    assert result.statistics.discovered == 4
    assert result.statistics.duplicates == 1
    assert result.statistics.blacklisted == 0
    assert result.statistics.unsupported == 2
    assert result.statistics.needs_detail == 2
    assert result.statistics.failed == 0


def test_discovery_reports_three_tv_month_groups_in_verbose_mode() -> None:
    stream = StringIO()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "data": []})

    with ConsoleProgressReporter(
        "sync", mode="plain", verbose=True, stream=stream
    ) as reporter:
        result = QuarterlyDiscovery(_client(handler), reporter).discover(2022, 1)

    assert result.statistics.discovered == 0
    lines = stream.getvalue().splitlines()
    assert len(lines) == 3
    assert all("候选发现" in line for line in lines)


def test_discovery_records_a_failed_month_and_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        month = int(request.url.params["month"])
        category = int(request.url.params["cat"])
        if (month, category) == (2, TV_CATEGORY):
            return httpx.Response(500)
        return httpx.Response(200, json={"total": 0, "data": []})

    client = _client(handler)
    client.max_retries = 0
    result = QuarterlyDiscovery(client).discover(2022, 1)

    assert result.statistics.failed == 1
    assert result.failures[0].month == 2
    assert result.failures[0].code == "http_500"


def test_episodes_paginate_filter_non_main_entries_and_skip_bad_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/episodes"
        assert request.url.params["subject_id"] == "101"
        assert request.url.params["type"] == "0"
        assert request.url.params["limit"] == "200"
        if request.url.params["offset"] == "0":
            return httpx.Response(
                200,
                json={
                    "total": 4,
                    "data": ENRICHED_FIXTURES["episodes_page_1"],
                },
            )
        return httpx.Response(
            200,
            json={"total": 4, "data": ENRICHED_FIXTURES["episodes_page_2"]},
        )

    client = _client(handler)
    episodes = client.get_episodes(101)

    assert [episode.episode_id for episode in episodes] == [500, 502]
    assert [episode.position for episode in episodes] == [0, 3]
    assert episodes[0].duration_seconds == 1440
    assert episodes[1].duration_seconds is None
    assert episodes[1].air_date is None
    assert client.metrics.json_item_failures == 1


def test_role_and_detail_dtos_preserve_order_and_explicit_images() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        paths = {
            "/v0/subjects/101/characters": ENRICHED_FIXTURES["roles"],
            "/v0/characters/100": ENRICHED_FIXTURES["character_detail"],
            "/v0/persons/200": ENRICHED_FIXTURES["person_detail"],
            "/v0/persons/201": ENRICHED_FIXTURES["person_detail_missing"],
        }
        return httpx.Response(200, json=paths[request.url.path])

    client = _client(handler)
    roles = client.get_related_characters(101)
    character = client.get_character(100)
    person = client.get_person(200)
    missing_person = client.get_person(201)

    assert [role.character_id for role in roles] == [100, 101]
    assert roles[0].relation == "主角"
    assert [actor.person_id for actor in roles[0].actors] == [200, 201]
    assert roles[0].images.largest_available == "https://img.example/character-large.jpg"
    assert roles[1].images.largest_available is None
    assert isinstance(character, CharacterDetail)
    assert character.infobox[0].value == "中文角色名"
    assert isinstance(person, PersonDetail)
    assert person.infobox[0].value == "中文声优名"
    assert missing_person.infobox == ()


def test_image_requests_are_separate_and_recognise_default_urls() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            content=b"image",
            headers={"Content-Type": "image/jpeg"},
        )

    client = _client(handler)
    image = client.fetch_image("https://img.example/cover.jpg")

    assert image.content == b"image"
    assert image.content_type == "image/jpeg"
    assert calls == ["https://img.example/cover.jpg"]
    assert client.metrics.image_requests == 1
    assert client.metrics.json_requests == 0
    assert is_default_image_url(ENRICHED_FIXTURES["default_image"])
    assert not is_default_image_url("https://img.example/cover.jpg")


def test_image_404_is_not_retried_or_counted_as_a_json_failure() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(BangumiApiError) as error:
        client.fetch_image("https://img.example/missing.jpg")

    assert error.value.code == "image_http_404"
    assert calls == 1
    assert client.metrics.image_retries == 0
    assert client.metrics.json_requests == 0


def test_image_retries_transient_responses_and_enforces_byte_limit() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=b"four",
            headers={"Content-Type": "image/png", "Content-Length": "4"},
        )

    client = _client(handler)
    with pytest.raises(BangumiApiError) as error:
        client.fetch_image("https://img.example/cover.png", max_bytes=3)

    assert error.value.code == "image_too_large"
    assert calls == 2
    assert client.metrics.image_retries == 1
