"""Anonymous Bangumi API access, DTO conversion, and quarterly discovery."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from bgm_side_b.rules import is_quarter_month, normalize_format

API_BASE_URL = "https://api.bgm.tv/v0"
ANIME_SUBJECT_TYPE = 2
TV_CATEGORY = 1
MOVIE_CATEGORY = 3
DISCOVERY_CATEGORIES = (TV_CATEGORY, MOVIE_CATEGORY)
DEFAULT_USER_AGENT = (
    "Bangumi-Side-B/0.1.0 (+https://github.com/MyKr-YSteinsK/bangumi-side-b)"
)


class BangumiApiError(RuntimeError):
    """A safe, classified API failure without response bodies or headers."""

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary
        super().__init__(summary)


class ResponseShapeError(BangumiApiError):
    """A response did not contain a required field in the expected shape."""


@dataclass(frozen=True)
class ApiTag:
    """A raw tag value returned by the subject API."""

    name: str
    count: int | None


@dataclass(frozen=True)
class ApiInfoboxItem:
    """A structured Infobox key/value pair returned by the subject API."""

    key: str
    value: Any


@dataclass(frozen=True)
class CandidateSubject:
    """A browse result that must still be confirmed with a detail request."""

    subject_id: int
    platform: str | None
    name: str | None
    name_cn: str | None
    category: int

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], category: int
    ) -> CandidateSubject:
        return cls(
            subject_id=_subject_id(payload),
            platform=_optional_string(payload.get("platform")),
            name=_optional_string(payload.get("name")),
            name_cn=_optional_string(payload.get("name_cn")),
            category=category,
        )


@dataclass(frozen=True)
class SubjectDetail:
    """The subject facts required by later deterministic normalisation."""

    subject_id: int
    subject_type: int | None
    name: str | None
    name_cn: str | None
    summary: str | None
    air_date: date | None
    platform: str | None
    eps: int | None
    total_episodes: int | None
    rating_score: float | None
    rating_total: int | None
    tags: tuple[ApiTag, ...]
    infobox: tuple[ApiInfoboxItem, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SubjectDetail:
        rating = payload.get("rating")
        rating_data = rating if isinstance(rating, Mapping) else {}
        return cls(
            subject_id=_subject_id(payload),
            subject_type=_optional_integer(payload.get("type")),
            name=_optional_string(payload.get("name")),
            name_cn=_optional_string(payload.get("name_cn")),
            summary=_optional_string(payload.get("summary")),
            air_date=_optional_date(payload.get("date")),
            platform=_optional_string(payload.get("platform")),
            eps=_optional_integer(payload.get("eps")),
            total_episodes=_optional_integer(payload.get("total_episodes")),
            rating_score=_optional_number(rating_data.get("score")),
            rating_total=_optional_integer(rating_data.get("total")),
            tags=_tags_from_payload(payload.get("tags")),
            infobox=_infobox_from_payload(payload.get("infobox")),
        )


@dataclass(frozen=True)
class DiscoveryStatistics:
    """Counts for one non-persistent quarterly candidate discovery pass."""

    discovered: int = 0
    duplicates: int = 0
    blacklisted: int = 0
    unsupported: int = 0
    needs_detail: int = 0
    failed: int = 0


@dataclass(frozen=True)
class DiscoveryFailure:
    """One month/category request that could not be discovered safely."""

    month: int
    category: int
    code: str
    summary: str


@dataclass(frozen=True)
class DiscoveryResult:
    """Deterministically ordered candidates plus counts and recoverable failures."""

    candidates: tuple[CandidateSubject, ...]
    statistics: DiscoveryStatistics
    failures: tuple[DiscoveryFailure, ...]


class BangumiApiClient:
    """Small, injectable synchronous client for public Bangumi v0 endpoints."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_retries: int = 3,
        concurrency: int = 3,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or concurrency < 1:
            raise ValueError("timeout, retry count, and concurrency must be valid")
        self.max_retries = max_retries
        self.concurrency = concurrency
        self._sleeper = sleeper
        self._jitter = jitter
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=API_BASE_URL,
            timeout=timeout_seconds,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            limits=httpx.Limits(max_connections=concurrency),
        )

    def close(self) -> None:
        """Close the client when this instance created it."""
        if self._owns_client:
            self._client.close()

    def browse_subjects(
        self,
        *,
        year: int,
        month: int,
        category: int,
        limit: int = 100,
    ) -> tuple[CandidateSubject, ...]:
        """Read every page for one month and one documented animation category."""
        if category not in DISCOVERY_CATEGORIES:
            raise ValueError("category must be the TV or theatrical movie category")
        offset = 0
        subjects: list[CandidateSubject] = []
        while True:
            body = self._request_json(
                "/subjects",
                {
                    "type": ANIME_SUBJECT_TYPE,
                    "cat": category,
                    "year": year,
                    "month": month,
                    "limit": limit,
                    "offset": offset,
                },
            )
            data = body.get("data")
            if not isinstance(data, list):
                raise ResponseShapeError(
                    "invalid_page", "browse response has no data list"
                )
            subjects.extend(
                CandidateSubject.from_payload(item, category)
                for item in data
                if isinstance(item, Mapping)
            )
            if not data:
                return tuple(subjects)
            next_offset = offset + len(data)
            total = body.get("total")
            if len(data) < limit or (
                isinstance(total, int) and next_offset >= total
            ):
                return tuple(subjects)
            offset = next_offset

    def get_subject(self, subject_id: int) -> SubjectDetail:
        """Fetch a subject detail DTO without database writes or inference."""
        if subject_id <= 0:
            raise ValueError("subject id must be positive")
        return SubjectDetail.from_payload(self._request_json(f"/subjects/{subject_id}"))

    def _request_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        retry_after: float | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.get(path, params=params)
            except httpx.TimeoutException:
                retry_after = None
                failure = BangumiApiError("timeout", "request timed out")
            except httpx.TransportError:
                retry_after = None
                failure = BangumiApiError("network", "network request failed")
            else:
                if 200 <= response.status_code < 300:
                    try:
                        body = response.json()
                    except ValueError as error:
                        raise ResponseShapeError(
                            "invalid_json", "response did not contain JSON"
                        ) from error
                    if not isinstance(body, dict):
                        raise ResponseShapeError(
                            "invalid_json", "response JSON was not an object"
                        )
                    return body
                retry_after = _retry_after_seconds(response)
                failure = BangumiApiError(
                    f"http_{response.status_code}",
                    f"HTTP {response.status_code}",
                )
                if not _is_transient_status(response.status_code):
                    raise failure

            if attempt == self.max_retries:
                raise failure
            delay = retry_after if retry_after is not None else _backoff_delay(
                attempt, self._jitter
            )
            self._sleeper(delay)

        raise AssertionError("unreachable")


class QuarterlyDiscovery:
    """Discover and deterministically filter quarter candidates without persistence."""

    def __init__(self, client: BangumiApiClient) -> None:
        self.client = client

    def discover(
        self,
        year: int,
        quarter_month: int,
        excluded_subject_ids: set[int] | frozenset[int] = frozenset(),
    ) -> DiscoveryResult:
        """Fetch all TV and movie pages for three months and filter candidates."""
        if not is_quarter_month(quarter_month):
            raise ValueError("quarter month must be one of 1, 4, 7, or 10")
        requests = [
            (month, category)
            for month in range(quarter_month, quarter_month + 3)
            for category in DISCOVERY_CATEGORIES
        ]
        candidates: list[CandidateSubject] = []
        failures: list[DiscoveryFailure] = []
        with ThreadPoolExecutor(max_workers=self.client.concurrency) as executor:
            futures = {
                executor.submit(
                    self.client.browse_subjects,
                    year=year,
                    month=month,
                    category=category,
                ): (month, category)
                for month, category in requests
            }
            for future in as_completed(futures):
                month, category = futures[future]
                try:
                    candidates.extend(future.result())
                except BangumiApiError as error:
                    failures.append(
                        DiscoveryFailure(month, category, error.code, error.summary)
                    )

        return _filter_candidates(candidates, excluded_subject_ids, failures)


def _filter_candidates(
    candidates: list[CandidateSubject],
    excluded_subject_ids: set[int] | frozenset[int],
    failures: list[DiscoveryFailure],
) -> DiscoveryResult:
    unique_candidates = sorted(candidates, key=lambda candidate: candidate.subject_id)
    seen: set[int] = set()
    selected: list[CandidateSubject] = []
    duplicates = blacklisted = unsupported = needs_detail = 0
    for candidate in unique_candidates:
        if candidate.subject_id in seen:
            duplicates += 1
            continue
        seen.add(candidate.subject_id)
        if candidate.subject_id in excluded_subject_ids:
            blacklisted += 1
            continue
        normalised = normalize_format(candidate.platform)
        if normalised is not None and normalised not in {"tv", "movie"}:
            unsupported += 1
            continue
        selected.append(candidate)
        needs_detail += 1

    statistics = DiscoveryStatistics(
        discovered=len(seen),
        duplicates=duplicates,
        blacklisted=blacklisted,
        unsupported=unsupported,
        needs_detail=needs_detail,
        failed=len(failures),
    )
    return DiscoveryResult(
        candidates=tuple(selected),
        statistics=statistics,
        failures=tuple(
            sorted(failures, key=lambda failure: (failure.month, failure.category))
        ),
    )


def _subject_id(payload: Mapping[str, Any]) -> int:
    subject_id = payload.get("id")
    if (
        not isinstance(subject_id, int)
        or isinstance(subject_id, bool)
        or subject_id <= 0
    ):
        raise ResponseShapeError(
            "missing_subject_id", "subject response has no valid id"
        )
    return subject_id


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _tags_from_payload(value: Any) -> tuple[ApiTag, ...]:
    if not isinstance(value, list):
        return ()
    tags: list[ApiTag] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _optional_string(item.get("name"))
        if name is not None:
            tags.append(ApiTag(name, _optional_integer(item.get("count"))))
    return tuple(tags)


def _infobox_from_payload(value: Any) -> tuple[ApiInfoboxItem, ...]:
    if not isinstance(value, list):
        return ()
    items: list[ApiInfoboxItem] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = _optional_string(item.get("key"))
        if key is not None and "value" in item:
            items.append(ApiInfoboxItem(key, item["value"]))
    return tuple(items)


def _is_transient_status(status_code: int) -> bool:
    return status_code == 429 or status_code == 408 or 500 <= status_code < 600


def _backoff_delay(attempt: int, jitter: Callable[[], float]) -> float:
    return 0.5 * (2**attempt) + jitter()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
