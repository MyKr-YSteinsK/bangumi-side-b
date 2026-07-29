"""Tests for minimal Bangumi DTOs, retrying HTTP, and candidate discovery."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from bgm_side_b.api import (
    DEFAULT_USER_AGENT,
    MOVIE_CATEGORY,
    TV_CATEGORY,
    BangumiApiClient,
    BangumiApiError,
    QuarterlyDiscovery,
    ResponseShapeError,
    SubjectDetail,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "subject_cases.json").read_text(
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
    assert result.statistics.discovered == 5
    assert result.statistics.duplicates == 1
    assert result.statistics.blacklisted == 1
    assert result.statistics.unsupported == 2
    assert result.statistics.needs_detail == 2
    assert result.statistics.failed == 0


def test_discovery_records_a_failed_month_and_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        month = int(request.url.params["month"])
        category = int(request.url.params["cat"])
        if (month, category) == (2, MOVIE_CATEGORY):
            return httpx.Response(500)
        return httpx.Response(200, json={"total": 0, "data": []})

    client = _client(handler)
    client.max_retries = 0
    result = QuarterlyDiscovery(client).discover(2022, 1)

    assert result.statistics.failed == 1
    assert result.failures[0].month == 2
    assert result.failures[0].code == "http_500"
