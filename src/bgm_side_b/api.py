"""Current Bangumi v0 API boundary for archive facts and covers only."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from bgm_side_b.progress import NullProgressReporter, ProgressReporter

API_BASE_URL = "https://api.bgm.tv/v0"
ANIME_SUBJECT_TYPE = 2
TV_CATEGORY = 1
MOVIE_CATEGORY = 3
BROWSE_CATEGORIES = frozenset({TV_CATEGORY, MOVIE_CATEGORY})
DEFAULT_USER_AGENT = (
    "Bangumi-Side-B/0.1.2 (+https://github.com/MyKr-YSteinsK/bangumi-side-b)"
)
IMAGE_SIZE_ORDER = ("large", "medium", "common", "grid", "small")
DEFAULT_IMAGE_FILENAME = "no_icon_subject.png"


class BangumiApiError(RuntimeError):
    """A classified API failure that intentionally excludes response bodies."""

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary
        super().__init__(summary)


class ResponseShapeError(BangumiApiError):
    """A required public API field was absent or had an incompatible shape."""


@dataclass(frozen=True)
class ApiTag:
    """One raw API tag before deterministic archive normalization."""

    name: str
    count: int | None


@dataclass(frozen=True)
class ApiInfoboxItem:
    """One structured API infobox item without coercing its value."""

    key: str
    value: Any


@dataclass(frozen=True)
class ImageUrls:
    """Known image variants; the API does not guarantee their exact dimensions."""

    large: str | None = None
    medium: str | None = None
    common: str | None = None
    grid: str | None = None
    small: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ImageUrls:
        data = payload if isinstance(payload, Mapping) else {}
        return cls(
            **{size: _usable_image_url(data.get(size)) for size in IMAGE_SIZE_ORDER}
        )

    @property
    def largest_available(self) -> str | None:
        """Return the most suitable advertised variant without assuming pixels."""
        return next(
            (getattr(self, size) for size in IMAGE_SIZE_ORDER if getattr(self, size)),
            None,
        )

    @property
    def largest_variant(self) -> str | None:
        """Return the variant label paired with :attr:`largest_available`."""
        return next(
            (size for size in IMAGE_SIZE_ORDER if getattr(self, size)), None
        )


@dataclass(frozen=True)
class CandidateSubject:
    """A discovery result that must still be confirmed by ``get_subject``."""

    subject_id: int
    platform: str | None
    name: str | None
    name_cn: str | None
    category: int | None
    rating_score: float | None
    rating_total: int | None
    candidate_date: date | None = None
    subject_type: int | None = None

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], category: int | None
    ) -> CandidateSubject:
        rating = payload.get("rating")
        rating_data = rating if isinstance(rating, Mapping) else {}
        return cls(
            _subject_id(payload),
            _optional_string(payload.get("platform")),
            _optional_string(payload.get("name")),
            _optional_string(payload.get("name_cn")),
            category,
            _optional_number(rating_data.get("score")),
            _optional_integer(rating_data.get("total")),
            _optional_date(payload.get("date")),
            _optional_integer(payload.get("type")),
        )


@dataclass(frozen=True)
class SubjectDetail:
    """The complete public subject payload needed by the archive model."""

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
    meta_tags: tuple[str, ...]
    tags: tuple[ApiTag, ...]
    infobox: tuple[ApiInfoboxItem, ...]
    images: ImageUrls

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SubjectDetail:
        rating = payload.get("rating")
        rating_data = rating if isinstance(rating, Mapping) else {}
        return cls(
            _subject_id(payload),
            _optional_integer(payload.get("type")),
            _optional_string(payload.get("name")),
            _optional_string(payload.get("name_cn")),
            _optional_string(payload.get("summary")),
            _optional_date(payload.get("date")),
            _optional_string(payload.get("platform")),
            _optional_integer(payload.get("eps")),
            _optional_integer(payload.get("total_episodes")),
            _optional_number(rating_data.get("score")),
            _optional_integer(rating_data.get("total")),
            _meta_tags_from_payload(payload.get("meta_tags")),
            _tags_from_payload(payload.get("tags")),
            _infobox_from_payload(payload.get("infobox")),
            ImageUrls.from_payload(payload.get("images")),
        )


@dataclass
class RequestMetrics:
    """Small safe request counters for structured sync reports."""

    json_requests: int = 0
    json_retries: int = 0
    image_requests: int = 0
    image_retries: int = 0


@dataclass(frozen=True)
class ImageResponse:
    """A bounded binary response kept separate from JSON DTO conversion."""

    content: bytes
    content_type: str | None
    final_url: str


class BangumiApiClient:
    """Small injectable synchronous client for the documented public endpoints."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_retries: int = 3,
        concurrency: int = 3,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
        reporter: ProgressReporter | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or concurrency < 1:
            raise ValueError("timeout, retry count, and concurrency must be valid")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.concurrency = concurrency
        self.metrics = RequestMetrics()
        self._sleeper = sleeper
        self._jitter = jitter
        self.reporter = reporter or NullProgressReporter()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=API_BASE_URL,
            timeout=timeout_seconds,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            limits=httpx.Limits(max_connections=concurrency),
        )

    def close(self) -> None:
        """Close only the internally created HTTP client."""
        if self._owns_client:
            self._client.close()

    def browse_subjects(
        self, *, year: int, month: int, category: int, limit: int = 100
    ) -> tuple[SubjectDetail, ...]:
        """Read every documented Browse page for one month and media category."""
        if category not in BROWSE_CATEGORIES:
            raise ValueError("category must be the TV or theatrical movie category")
        return tuple(
            SubjectDetail.from_payload(item)
            for item in self._paged_json(
                "/subjects",
                {
                    "type": ANIME_SUBJECT_TYPE,
                    "cat": category,
                    "year": year,
                    "month": month,
                },
                limit=limit,
                request_label="discovery",
            )
        )

    def search_subjects(
        self,
        *,
        air_date_start: date,
        air_date_end: date,
        limit: int = 100,
    ) -> tuple[SubjectDetail, ...]:
        """Use the official experimental date search as a supplementary signal."""
        if air_date_start >= air_date_end:
            raise ValueError("search air-date range must be non-empty")
        body = {
            "keyword": "",
            "filter": {
                "type": [ANIME_SUBJECT_TYPE],
                "air_date": [
                    f">={air_date_start.isoformat()}",
                    f"<{air_date_end.isoformat()}",
                ],
            },
        }
        return tuple(
            SubjectDetail.from_payload(item)
            for item in self._paged_json(
                "/search/subjects",
                {},
                limit=limit,
                request_label="search-discovery",
                method="POST",
                json_body=body,
            )
        )

    def get_subject(self, subject_id: int) -> SubjectDetail:
        """Fetch one subject detail without inference or persistence."""
        if subject_id <= 0:
            raise ValueError("subject id must be positive")
        return SubjectDetail.from_payload(
            self._request_json(
                f"/subjects/{subject_id}", {}, request_label="subject-detail"
            )
        )

    def fetch_image(self, url: str, *, max_bytes: int) -> ImageResponse:
        """Fetch a bounded HTTP(S) image while preserving no response diagnostics."""
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BangumiApiError("image_url", "image URL is not a valid HTTP URL")
        response = self._request("GET", url, None, None, image=True)
        content = response.content
        if len(content) > max_bytes:
            raise BangumiApiError("image_too_large", "image body exceeds size limit")
        return ImageResponse(
            content, response.headers.get("content-type"), str(response.url)
        )

    def _paged_json(
        self,
        path: str,
        params: Mapping[str, object],
        *,
        limit: int,
        request_label: str,
        method: str = "GET",
        json_body: Mapping[str, object] | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        if not 1 <= limit <= 100:
            raise ValueError("page limit must be between 1 and 100")
        offset = 0
        items: list[Mapping[str, Any]] = []
        while True:
            page_params = {**params, "limit": limit, "offset": offset}
            payload = self._request_json(
                path,
                page_params,
                request_label=request_label,
                method=method,
                json_body=json_body,
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise ResponseShapeError("missing_data", "response data must be a list")
            page = tuple(item for item in data if isinstance(item, Mapping))
            if len(page) != len(data):
                raise ResponseShapeError(
                    "invalid_item", "response data contains invalid item"
                )
            items.extend(page)
            total = payload.get("total")
            if isinstance(total, int) and not isinstance(total, bool):
                if len(items) >= total:
                    break
            elif len(page) < limit:
                break
            if not page:
                break
            offset += len(page)
        return tuple(items)

    def _request_json(
        self,
        path: str,
        params: Mapping[str, object],
        *,
        request_label: str,
        method: str = "GET",
        json_body: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        response = self._request(method, path, params, json_body, image=False)
        try:
            payload = response.json()
        except ValueError as error:
            raise ResponseShapeError(
                "invalid_json", "response is not valid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise ResponseShapeError("invalid_json", "response root must be an object")
        return payload

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        *,
        image: bool,
    ) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response: httpx.Response | None = None
            if image:
                self.metrics.image_requests += 1
            else:
                self.metrics.json_requests += 1
            try:
                with self.reporter.activity(
                    stage="api", message="等待 Bangumi API", current=None
                ):
                    response = self._client.request(
                        method, path, params=params, json=json_body
                    )
            except httpx.TimeoutException:
                failure = BangumiApiError("timeout", "request timed out")
            except httpx.HTTPError:
                failure = BangumiApiError("network", "network request failed")
            else:
                if response.status_code < 400:
                    return response
                code = f"{'image_' if image else ''}http_{response.status_code}"
                failure = BangumiApiError(code, f"HTTP {response.status_code}")
                if (
                    response.status_code not in {408, 429}
                    and response.status_code < 500
                ):
                    raise failure
            if attempt == self.max_retries:
                raise failure
            if image:
                self.metrics.image_retries += 1
            else:
                self.metrics.json_retries += 1
            delay = _retry_delay(response, attempt, self._jitter)
            self.reporter.retry(
                stage="api",
                message=failure.summary,
                attempt=attempt + 1,
                max_attempts=self.max_retries + 1,
                retry_delay_seconds=delay,
            )
            self._sleeper(delay)
        raise AssertionError("unreachable")


def _tags_from_payload(value: Any) -> tuple[ApiTag, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        ApiTag(name, _optional_integer(item.get("count")))
        for item in value
        if isinstance(item, Mapping)
        and (name := _optional_string(item.get("name"))) is not None
    )


def _meta_tags_from_payload(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        sorted(
            {
                name
                for item in value
                if (name := _optional_string(item)) is not None
            }
        )
    )


def _infobox_from_payload(value: Any) -> tuple[ApiInfoboxItem, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        ApiInfoboxItem(key, item.get("value"))
        for item in value
        if isinstance(item, Mapping)
        and (key := _optional_string(item.get("key"))) is not None
    )


def _subject_id(payload: Mapping[str, Any]) -> int:
    subject_id = _optional_integer(payload.get("id"))
    if subject_id is None or subject_id <= 0:
        raise ResponseShapeError(
            "missing_subject_id", "subject id is missing or invalid"
        )
    return subject_id


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _usable_image_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.path.rsplit("/", 1)[-1] == DEFAULT_IMAGE_FILENAME:
        return None
    return value


def _retry_delay(
    response: httpx.Response | None, attempt: int, jitter: Callable[[], float]
) -> float:
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_time = parsedate_to_datetime(retry_after).timestamp()
                return max(0.0, retry_time - time.time())
            except (TypeError, ValueError):
                pass
    return 0.5 * (2**attempt) + jitter()
