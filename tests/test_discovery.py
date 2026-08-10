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


def test_browse_queries_tv_and_movie_months_and_merges_provenance() -> None:
    pages = {
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
    assert tv.detail is not None and tv.detail.subject_id == 101
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
