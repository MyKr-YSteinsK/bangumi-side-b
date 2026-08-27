"""Current-contract discovery adapter coverage without persistence."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx

from bgm_side_b.api import DEFAULT_USER_AGENT, BangumiApiClient, CandidateSubject
from bgm_side_b.discovery import (
    BrowseDiscoveryAdapter,
    DiscoveryBatch,
    DiscoverySource,
    SearchDiscoveryAdapter,
    merge_discovery_batches,
)
from bgm_side_b.domain import MediaFormat, Quarter

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


def test_search_posts_documented_date_filter_and_paginates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        data = [FIXTURES["tv"]] if offset == 0 else [FIXTURES["movie"]]
        return httpx.Response(200, json={"total": 2, "data": data})

    subjects = _client(handler).search_subjects(
        air_date_start=date(2026, 3, 25), air_date_end=date(2026, 7, 1), limit=1
    )

    assert [subject.subject_id for subject in subjects] == [101, 102]
    assert all(request.method == "POST" for request in requests)
    assert requests[0].url.path == "/v0/search/subjects"
    assert json.loads(requests[0].content) == {
        "keyword": "",
        "filter": {
            "type": [2],
            "air_date": [">=2026-03-25", "<2026-07-01"],
        },
    }


def test_search_lookback_keeps_only_tv_boundary_candidates() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total": 3,
                "data": [
                    {**FIXTURES["tv"], "id": 201, "date": "2026-03-28"},
                    {**FIXTURES["movie"], "id": 202, "date": "2026-03-28"},
                    {**FIXTURES["movie"], "id": 203, "date": "2026-04-02"},
                ],
            },
        )

    batch = SearchDiscoveryAdapter(_client(handler)).discover(Quarter(2026, 4))

    assert [candidate.subject_id for candidate in batch.candidates] == [201, 203]


def test_main_episode_airdates_paginate_at_two_hundred_and_discard_other_fields(
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        if offset == 0:
            data = [{"id": 1, "type": 0, "airdate": "2026-07-12", "name": "1"}]
            data.extend(
                {
                    "id": index,
                    "type": 1,
                    "airdate": "2026-07-13",
                    "name": "SP",
                }
                for index in range(2, 201)
            )
        else:
            data = [
                {"id": 3, "type": 0, "airdate": "not-a-date", "name": "2"},
                {"id": 4, "type": 0, "airdate": "2026-07-05", "name": "3"},
            ]
        return httpx.Response(200, json={"total": 202, "data": data})

    airdates = _client(handler).get_main_episode_airdates(101)

    assert airdates == (date(2026, 7, 5), date(2026, 7, 12))
    assert [request.url.params["offset"] for request in requests] == ["0", "200"]
    assert all(request.url.path == "/v0/episodes" for request in requests)
    assert all(request.url.params["subject_id"] == "101" for request in requests)
    assert all(request.url.params["type"] == "0" for request in requests)
    assert all(request.url.params["limit"] == "200" for request in requests)


def test_main_episode_airdates_ignore_supplemental_entries() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total": 2,
                "data": [
                    {"type": 1, "airdate": "2026-07-02"},
                    {"type": 2, "airdate": "2026-07-03"},
                ],
            },
        )

    assert _client(handler).get_main_episode_airdates(101) == ()


def test_main_episode_count_accepts_only_a_complete_contiguous_main_sequence() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "total": 3,
                "limit": 200,
                "offset": 0,
                "data": [
                    {"type": 0, "ep": 1},
                    {"type": 0, "ep": 2},
                    {"type": 0, "ep": 3, "airdate": "2026-09-01"},
                ],
            },
        )

    assert _client(handler).get_main_episode_count(571784) == 3
    assert requests[0].url.params["type"] == "0"
    assert requests[0].url.params["limit"] == "200"
    assert requests[0].url.params["offset"] == "0"


def test_main_episode_count_rejects_partial_or_non_main_registry_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["subject_id"] == "101":
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "data": [{"type": 0, "ep": 1}, {"type": 0, "ep": 2}],
                },
            )
        return httpx.Response(
            200,
            json={
                "total": 2,
                "data": [{"type": 0, "ep": 1}, {"type": 1, "ep": 2}],
            },
        )

    client = _client(handler)
    assert client.get_main_episode_count(101) is None
    assert client.get_main_episode_count(102) is None


def test_browse_queries_tv_and_movie_months_and_merges_provenance() -> None:
    pages = {
        (3, 1): [],
        (4, 1): [FIXTURES["tv"]],
        (4, 3): [FIXTURES["movie"]],
        (5, 1): [FIXTURES["tv"]],
        (5, 3): [],
        (6, 1): [],
        (6, 3): [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/subjects"
        key = (int(request.url.params["month"]), int(request.url.params["cat"]))
        return httpx.Response(200, json={"total": len(pages[key]), "data": pages[key]})

    batch = BrowseDiscoveryAdapter(_client(handler)).discover(Quarter(2026, 4))

    assert [candidate.subject_id for candidate in batch.candidates] == [101, 102]
    tv, movie = batch.candidates
    assert tv.candidate_media_format is MediaFormat.TV
    assert tv.provenance == ("browse:TV:2026-04", "browse:TV:2026-05")
    assert tv.subject_id == 101
    assert movie.candidate_media_format is MediaFormat.MOVIE
    assert not batch.failures


def test_merge_keeps_conflicting_media_explicit_instead_of_last_response_winning(
) -> None:
    tv = CandidateSubject(
        101, "TV", "A", None, 1, None, None, date(2026, 4, 1), 2
    )
    movie = CandidateSubject(
        101, "Movie", "A", None, 3, None, None, date(2026, 4, 1), 2
    )

    class StubClient:
        def browse_subjects(
            self, *, year: int, month: int, category: int
        ) -> tuple[CandidateSubject, ...]:
            if month != 4:
                return ()
            return (tv,) if category == 1 else (movie,)

    batch = BrowseDiscoveryAdapter(StubClient()).discover(Quarter(2026, 4))  # type: ignore[arg-type]

    candidate = batch.candidates[0]
    assert candidate.candidate_media_format is None
    assert candidate.has_media_conflict
    assert candidate.media_formats == frozenset({MediaFormat.TV, MediaFormat.MOVIE})


def test_browse_adds_only_the_seven_day_previous_month_tv_boundary_window() -> None:
    boundary = {**FIXTURES["tv"], "date": "2026-03-28"}
    outside = {**FIXTURES["tv"], "id": 202, "date": "2026-03-24"}

    def handler(request: httpx.Request) -> httpx.Response:
        month = int(request.url.params["month"])
        category = int(request.url.params["cat"])
        data = [boundary, outside] if (month, category) == (3, 1) else []
        return httpx.Response(200, json={"total": len(data), "data": data})

    batch = BrowseDiscoveryAdapter(_client(handler)).discover(Quarter(2026, 4))

    assert [candidate.subject_id for candidate in batch.candidates] == [101]
    assert batch.candidates[0].provenance == ("browse:TV:2026-03",)


def test_experimental_search_failure_is_isolated_and_preserved_for_sync() -> None:
    class StubClient:
        def search_subjects(
            self, *, air_date_start: date, air_date_end: date
        ) -> tuple[CandidateSubject, ...]:
            from bgm_side_b.api import BangumiApiError

            raise BangumiApiError("http_500", "HTTP 500")

    search = SearchDiscoveryAdapter(StubClient()).discover(Quarter(2026, 4))  # type: ignore[arg-type]
    merged = merge_discovery_batches(DiscoveryBatch(()), search)

    assert not merged.candidates
    assert merged.failures[0].source is DiscoverySource.SEARCH
    assert merged.failures[0].code == "http_500"
