"""Anonymous Bangumi API access, DTO conversion, and quarterly discovery."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from bgm_side_b.legacy_rules import is_quarter_month, normalize_format
from bgm_side_b.progress import NullProgressReporter, ProgressReporter

API_BASE_URL = "https://api.bgm.tv/v0"
ANIME_SUBJECT_TYPE = 2
TV_CATEGORY = 1
MOVIE_CATEGORY = 3
DISCOVERY_CATEGORIES = (TV_CATEGORY,)
DEFAULT_USER_AGENT = (
    "Bangumi-Side-B/0.1.2 (+https://github.com/MyKr-YSteinsK/bangumi-side-b)"
)
MAIN_EPISODE_TYPE = 0
IMAGE_SIZE_ORDER = ("large", "medium", "common", "grid", "small")
DEFAULT_IMAGE_FILENAME = "no_icon_subject.png"


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
class ImageUrls:
    """Explicit API image URLs, with no-image placeholders excluded."""

    large: str | None = None
    medium: str | None = None
    common: str | None = None
    grid: str | None = None
    small: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ImageUrls:
        data = payload if isinstance(payload, Mapping) else {}
        values = {
            size: _usable_image_url(data.get(size)) for size in IMAGE_SIZE_ORDER
        }
        return cls(**values)

    @property
    def largest_available(self) -> str | None:
        """Return the largest explicit non-placeholder image URL if one exists."""
        return next(
            (getattr(self, size) for size in IMAGE_SIZE_ORDER if getattr(self, size)),
            None,
        )


@dataclass(frozen=True)
class ApiEpisode:
    """One API episode, retaining only the facts required for persistence."""

    episode_id: int
    episode_type: int | None
    episode_number: float | None
    sort_number: float | None
    name: str | None
    name_cn: str | None
    air_date: date | None
    duration_seconds: int | None
    raw_duration: str | None
    position: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], position: int) -> ApiEpisode:
        duration_seconds = _optional_integer(payload.get("duration_seconds"))
        return cls(
            episode_id=_required_id(payload, "episode"),
            episode_type=_optional_integer(payload.get("type")),
            episode_number=_optional_number(payload.get("ep")),
            sort_number=_optional_number(payload.get("sort")),
            name=_optional_string(payload.get("name")),
            name_cn=_optional_string(payload.get("name_cn")),
            air_date=_optional_date(payload.get("airdate")),
            duration_seconds=(
                duration_seconds
                if duration_seconds is not None and duration_seconds > 0
                else None
            ),
            raw_duration=_optional_string(payload.get("duration")),
            position=position,
        )


@dataclass(frozen=True)
class ApiPersonSummary:
    """A person embedded in a role response, without retaining person media."""

    person_id: int
    original_name: str | None
    person_type: int | None
    career: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ApiPersonSummary:
        return cls(
            person_id=_required_id(payload, "person"),
            original_name=_optional_string(payload.get("name")),
            person_type=_optional_integer(payload.get("type")),
            career=_string_tuple(payload.get("career")),
        )


@dataclass(frozen=True)
class RelatedCharacter:
    """One subject-local character relationship and its embedded actors."""

    character_id: int
    original_name: str | None
    summary: str | None
    character_type: int | None
    relation: str | None
    images: ImageUrls
    actors: tuple[ApiPersonSummary, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RelatedCharacter:
        actors = payload.get("actors")
        return cls(
            character_id=_required_id(payload, "character"),
            original_name=_optional_string(payload.get("name")),
            summary=_optional_string(payload.get("summary")),
            character_type=_optional_integer(payload.get("type")),
            relation=_optional_string(payload.get("relation")),
            images=ImageUrls.from_payload(payload.get("images")),
            actors=tuple(
                ApiPersonSummary.from_payload(actor)
                for actor in actors
                if isinstance(actor, Mapping)
            )
            if isinstance(actors, list)
            else (),
        )


@dataclass(frozen=True)
class CharacterDetail:
    """Global character detail; missing optional fields remain absent."""

    character_id: int
    original_name: str | None
    summary: str | None
    character_type: int | None
    infobox: tuple[ApiInfoboxItem, ...]
    images: ImageUrls

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CharacterDetail:
        return cls(
            character_id=_required_id(payload, "character"),
            original_name=_optional_string(payload.get("name")),
            summary=_optional_string(payload.get("summary")),
            character_type=_optional_integer(payload.get("type")),
            infobox=_infobox_from_payload(payload.get("infobox")),
            images=ImageUrls.from_payload(payload.get("images")),
        )


@dataclass(frozen=True)
class PersonDetail:
    """Global voice-actor detail, deliberately excluding person image storage."""

    person_id: int
    original_name: str | None
    person_type: int | None
    career: tuple[str, ...]
    infobox: tuple[ApiInfoboxItem, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PersonDetail:
        return cls(
            person_id=_required_id(payload, "person"),
            original_name=_optional_string(payload.get("name")),
            person_type=_optional_integer(payload.get("type")),
            career=_string_tuple(payload.get("career")),
            infobox=_infobox_from_payload(payload.get("infobox")),
        )


@dataclass
class RequestMetrics:
    """Counts of actual JSON and binary requests, kept independently."""

    json_requests: int = 0
    json_retries: int = 0
    json_item_failures: int = 0
    image_requests: int = 0
    image_retries: int = 0


@dataclass(frozen=True)
class ImageResponse:
    """A binary image response, separate from JSON DTO handling."""

    content: bytes
    content_type: str | None
    final_url: str


@dataclass(frozen=True)
class _RequestContext:
    """Safe labels retained only while one API request is active."""

    label: str
    entity_type: str | None
    entity_id: int | None
    current: str | None = None


@dataclass(frozen=True)
class CandidateSubject:
    """A browse result that must still be confirmed with a detail request."""

    subject_id: int
    platform: str | None
    name: str | None
    name_cn: str | None
    category: int
    rating_score: float | None
    rating_total: int | None

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], category: int
    ) -> CandidateSubject:
        rating = payload.get("rating")
        rating_data = rating if isinstance(rating, Mapping) else {}
        return cls(
            subject_id=_subject_id(payload),
            platform=_optional_string(payload.get("platform")),
            name=_optional_string(payload.get("name")),
            name_cn=_optional_string(payload.get("name_cn")),
            category=category,
            rating_score=_optional_number(rating_data.get("score")),
            rating_total=_optional_integer(rating_data.get("total")),
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
    images: ImageUrls

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
            images=ImageUrls.from_payload(payload.get("images")),
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
        reporter: ProgressReporter | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or concurrency < 1:
            raise ValueError("timeout, retry count, and concurrency must be valid")
        self.max_retries = max_retries
        self.concurrency = concurrency
        self.timeout_seconds = timeout_seconds
        self.metrics = RequestMetrics()
        self._sleeper = sleeper
        self._jitter = jitter
        self.reporter = reporter or NullProgressReporter()
        self._request_context = threading.local()
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
            label = "discovery-tv" if category == TV_CATEGORY else "discovery-movie"
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
                request_label=label,
                current=f"offset {offset}",
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
        _validate_positive_id(subject_id, "subject")
        return SubjectDetail.from_payload(
            self._request_json(
                f"/subjects/{subject_id}",
                request_label="subject-detail",
                entity_type="subject",
                entity_id=subject_id,
            )
        )

    def get_episodes(self, subject_id: int) -> tuple[ApiEpisode, ...]:
        """Fetch all main-story episode pages and preserve their API positions."""
        _validate_positive_id(subject_id, "subject")
        offset = 0
        episodes: list[ApiEpisode] = []
        while True:
            body = self._request_json(
                "/episodes",
                {
                    "subject_id": subject_id,
                    "type": MAIN_EPISODE_TYPE,
                    "limit": 200,
                    "offset": offset,
                },
                request_label="episodes",
                entity_type="subject",
                entity_id=subject_id,
            )
            data = body.get("data")
            if not isinstance(data, list):
                raise ResponseShapeError(
                    "invalid_page", "episode response has no data list"
                )
            for index, item in enumerate(data):
                if not isinstance(item, Mapping):
                    self.metrics.json_item_failures += 1
                    continue
                try:
                    episode = ApiEpisode.from_payload(item, offset + index)
                except ResponseShapeError:
                    self.metrics.json_item_failures += 1
                    continue
                if episode.episode_type == MAIN_EPISODE_TYPE:
                    episodes.append(episode)
            if not data:
                return tuple(episodes)
            next_offset = offset + len(data)
            total = _optional_integer(body.get("total"))
            if total is not None and next_offset >= total:
                return tuple(episodes)
            if total is None and len(data) < 200:
                return tuple(episodes)
            offset = next_offset

    def get_related_characters(self, subject_id: int) -> tuple[RelatedCharacter, ...]:
        """Fetch subject-local character relations in the API response order."""
        _validate_positive_id(subject_id, "subject")
        body = self._request_json_list(
            f"/subjects/{subject_id}/characters",
            request_label="subject-characters",
            entity_type="subject",
            entity_id=subject_id,
        )
        relations: list[RelatedCharacter] = []
        for item in body:
            if not isinstance(item, Mapping):
                self.metrics.json_item_failures += 1
                continue
            try:
                relations.append(RelatedCharacter.from_payload(item))
            except ResponseShapeError:
                self.metrics.json_item_failures += 1
        return tuple(relations)

    def get_character(self, character_id: int) -> CharacterDetail:
        """Fetch a global character detail DTO without persistence."""
        _validate_positive_id(character_id, "character")
        return CharacterDetail.from_payload(
            self._request_json(
                f"/characters/{character_id}",
                request_label="character-detail",
                entity_type="character",
                entity_id=character_id,
            )
        )

    def get_person(self, person_id: int) -> PersonDetail:
        """Fetch a global person detail DTO without persistence."""
        _validate_positive_id(person_id, "person")
        return PersonDetail.from_payload(
            self._request_json(
                f"/persons/{person_id}",
                request_label="person-detail",
                entity_type="person",
                entity_id=person_id,
            )
        )

    def fetch_image(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        request_label: str = "image",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> ImageResponse:
        """Download binary image content separately from API JSON requests."""
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("image URL must be absolute HTTP or HTTPS")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("image byte limit must be positive")
        response = self._request_binary(
            url,
            request_label=request_label,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        content_length = response.headers.get("Content-Length")
        if max_bytes is not None and content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    raise BangumiApiError("image_too_large", "image exceeds byte limit")
            except ValueError:
                pass
        if max_bytes is not None and len(response.content) > max_bytes:
            raise BangumiApiError("image_too_large", "image exceeds byte limit")
        return ImageResponse(
            content=response.content,
            content_type=response.headers.get("Content-Type"),
            final_url=str(response.url),
        )

    def _request_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        request_label: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
        current: str | None = None,
    ) -> dict[str, Any]:
        context = _RequestContext(request_label, entity_type, entity_id, current)
        return self._request_with_context(
            context, lambda: self._request_json_response(path, params)
        )

    def _request_json_response(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        retry_after: float | None = None
        for attempt in range(self.max_retries + 1):
            self.metrics.json_requests += 1
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
            self.metrics.json_retries += 1
            delay = retry_after if retry_after is not None else _backoff_delay(
                attempt, self._jitter
            )
            self._report_retry(failure, attempt, delay, retry_after is not None)
            self._sleeper(delay)

        raise AssertionError("unreachable")

    def _request_json_list(
        self,
        path: str,
        *,
        request_label: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> list[Any]:
        context = _RequestContext(request_label, entity_type, entity_id)
        return self._request_with_context(
            context, lambda: self._request_json_list_response(path)
        )

    def _request_json_list_response(self, path: str) -> list[Any]:
        retry_after: float | None = None
        for attempt in range(self.max_retries + 1):
            self.metrics.json_requests += 1
            try:
                response = self._client.get(path)
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
                    if not isinstance(body, list):
                        raise ResponseShapeError(
                            "invalid_json", "response JSON was not an array"
                        )
                    return body
                retry_after = _retry_after_seconds(response)
                failure = BangumiApiError(
                    f"http_{response.status_code}", f"HTTP {response.status_code}"
                )
                if not _is_transient_status(response.status_code):
                    raise failure

            if attempt == self.max_retries:
                raise failure
            self.metrics.json_retries += 1
            delay = retry_after if retry_after is not None else _backoff_delay(
                attempt, self._jitter
            )
            self._report_retry(failure, attempt, delay, retry_after is not None)
            self._sleeper(delay)

        raise AssertionError("unreachable")

    def _request_binary(
        self,
        url: str,
        *,
        request_label: str,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> httpx.Response:
        context = _RequestContext(request_label, entity_type, entity_id)
        return self._request_with_context(
            context, lambda: self._request_binary_response(url)
        )

    def _request_binary_response(self, url: str) -> httpx.Response:
        retry_after: float | None = None
        for attempt in range(self.max_retries + 1):
            self.metrics.image_requests += 1
            try:
                response = self._client.get(url, follow_redirects=True)
            except httpx.TimeoutException:
                retry_after = None
                failure = BangumiApiError("image_timeout", "image request timed out")
            except httpx.TransportError:
                retry_after = None
                failure = BangumiApiError("image_network", "image request failed")
            else:
                if 200 <= response.status_code < 300:
                    return response
                retry_after = _retry_after_seconds(response)
                failure = BangumiApiError(
                    f"image_http_{response.status_code}",
                    f"image HTTP {response.status_code}",
                )
                if not _is_transient_status(response.status_code):
                    raise failure

            if attempt == self.max_retries:
                raise failure
            self.metrics.image_retries += 1
            delay = retry_after if retry_after is not None else _backoff_delay(
                attempt, self._jitter
            )
            self._report_retry(failure, attempt, delay, retry_after is not None)
            self._sleeper(delay)

        raise AssertionError("unreachable")

    def _request_with_context(
        self, context: _RequestContext, request: Callable[[], Any]
    ) -> Any:
        previous = getattr(self._request_context, "value", None)
        self._request_context.value = context
        try:
            with self.reporter.activity(
                stage=context.label,
                message="等待 Bangumi API",
                current=context.current,
                entity_type=context.entity_type,
                entity_id=context.entity_id,
            ):
                return request()
        finally:
            self._request_context.value = previous

    def _report_retry(
        self,
        failure: BangumiApiError,
        attempt: int,
        delay: float,
        used_retry_after: bool,
    ) -> None:
        context = getattr(self._request_context, "value", None)
        if not isinstance(context, _RequestContext):
            return
        message = failure.summary
        if used_retry_after:
            message = f"{message}｜按 Retry-After 等待"
        self.reporter.retry(
            stage=context.label,
            message=message,
            entity_type=context.entity_type,
            entity_id=context.entity_id,
            attempt=attempt + 1,
            max_attempts=self.max_retries,
            retry_delay_seconds=delay,
        )


class QuarterlyDiscovery:
    """Discover and deterministically filter quarter candidates without persistence."""

    def __init__(
        self, client: BangumiApiClient, reporter: ProgressReporter | None = None
    ) -> None:
        self.client = client
        self.reporter = reporter or NullProgressReporter()

    def discover(
        self,
        year: int,
        quarter_month: int,
        excluded_subject_ids: set[int] | frozenset[int] = frozenset(),
    ) -> DiscoveryResult:
        """Fetch only the configured TV discovery pages for three months."""
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
            for completed, future in enumerate(as_completed(futures), start=1):
                month, category = futures[future]
                try:
                    candidates.extend(future.result())
                except BangumiApiError as error:
                    failures.append(
                        DiscoveryFailure(month, category, error.code, error.summary)
                    )
                self.reporter.progress(
                    stage="discovery",
                    message="已完成候选发现",
                    current="TV" if category == TV_CATEGORY else "剧场版",
                    completed=completed,
                    total=len(requests),
                    quarter=f"{year}-{month:02d}",
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
        if normalised is not None and normalised != "tv":
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
    return _required_id(payload, "subject")


def _required_id(payload: Mapping[str, Any], entity: str) -> int:
    entity_id = payload.get("id")
    if (
        not isinstance(entity_id, int)
        or isinstance(entity_id, bool)
        or entity_id <= 0
    ):
        raise ResponseShapeError(
            f"missing_{entity}_id", f"{entity} response has no valid id"
        )
    return entity_id


def _validate_positive_id(entity_id: int, entity: str) -> None:
    if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id <= 0:
        raise ValueError(f"{entity} id must be positive")


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


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def is_default_image_url(url: str | None) -> bool:
    """Identify the documented default no-image endpoint by its exact filename."""
    return (
        isinstance(url, str)
        and urlsplit(url).path.rsplit("/", 1)[-1] == DEFAULT_IMAGE_FILENAME
    )


def _usable_image_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or is_default_image_url(value):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


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
