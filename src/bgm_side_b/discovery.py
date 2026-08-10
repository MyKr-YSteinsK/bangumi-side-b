"""Thin, deterministic adapters around documented Bangumi subject discovery."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from bgm_side_b.api import (
    MOVIE_CATEGORY,
    TV_CATEGORY,
    BangumiApiClient,
    BangumiApiError,
    CandidateSubject,
    SubjectDetail,
)
from bgm_side_b.domain import MediaFormat, Quarter

ANIME_SUBJECT_TYPE = 2
TV_BOUNDARY_LOOKBACK_DAYS = 7


class DiscoverySource(StrEnum):
    """The API pathway that supplied a current-run candidate observation."""

    BROWSE = "browse"
    SEARCH = "search"


@dataclass(frozen=True)
class DiscoveryFailure:
    """A classified discovery request failure without response content."""

    source: DiscoverySource
    scope: str
    code: str
    summary: str


@dataclass(frozen=True)
class DiscoveredSubject:
    """Merged current-run observations for one subject ID."""

    subject_id: int
    media_formats: frozenset[MediaFormat]
    candidate_dates: frozenset[date]
    subject_types: frozenset[int]
    provenance: tuple[str, ...]
    detail: SubjectDetail | None = None

    def __post_init__(self) -> None:
        if self.subject_id <= 0:
            raise ValueError("subject id must be positive")
        if not self.provenance:
            raise ValueError("discovered subject requires provenance")

    @property
    def candidate_media_format(self) -> MediaFormat | None:
        """Return the only observed Browse format; conflicts remain explicit."""
        return next(iter(self.media_formats)) if len(self.media_formats) == 1 else None

    @property
    def candidate_date(self) -> date | None:
        """Return the only observed date; conflicting dates remain explicit."""
        if len(self.candidate_dates) != 1:
            return None
        return next(iter(self.candidate_dates))

    @property
    def has_media_conflict(self) -> bool:
        return len(self.media_formats) > 1

    @property
    def has_date_conflict(self) -> bool:
        return len(self.candidate_dates) > 1


@dataclass(frozen=True)
class DiscoveryBatch:
    """One adapter's complete candidate observations and recoverable failures."""

    candidates: tuple[DiscoveredSubject, ...]
    failures: tuple[DiscoveryFailure, ...] = ()


class BrowseDiscoveryAdapter:
    """Discover documented Anime TV/Movie category pages for a quarter."""

    def __init__(self, client: BangumiApiClient) -> None:
        self.client = client

    def discover(self, quarter: Quarter) -> DiscoveryBatch:
        candidates: list[DiscoveredSubject] = []
        failures: list[DiscoveryFailure] = []
        for month in range(quarter.month, quarter.month + 3):
            for category, media_format in (
                (TV_CATEGORY, MediaFormat.TV),
                (MOVIE_CATEGORY, MediaFormat.MOVIE),
            ):
                provenance = _browse_provenance(media_format, quarter.year, month)
                try:
                    page = self.client.browse_subjects(
                        year=quarter.year, month=month, category=category
                    )
                except BangumiApiError as error:
                    failures.append(
                        DiscoveryFailure(
                            DiscoverySource.BROWSE,
                            provenance,
                            error.code,
                            error.summary,
                        )
                    )
                    continue
                candidates.extend(
                    _from_api_candidate(candidate, media_format, provenance)
                    for candidate in page
                )
        boundary_year, boundary_month = _previous_month(quarter)
        provenance = _browse_provenance(
            MediaFormat.TV, boundary_year, boundary_month
        )
        try:
            boundary_page = self.client.browse_subjects(
                year=boundary_year, month=boundary_month, category=TV_CATEGORY
            )
        except BangumiApiError as error:
            failures.append(
                DiscoveryFailure(
                    DiscoverySource.BROWSE, provenance, error.code, error.summary
                )
            )
        else:
            target_start = date(quarter.year, quarter.month, 1)
            boundary_start = target_start - timedelta(days=TV_BOUNDARY_LOOKBACK_DAYS)
            candidates.extend(
                _from_api_candidate(candidate, MediaFormat.TV, provenance)
                for candidate in boundary_page
                if candidate.air_date is not None
                and boundary_start <= candidate.air_date < target_start
            )
        return _batch(candidates, failures)


class SearchDiscoveryAdapter:
    """Use the experimental date search only as a supplementary observation."""

    def __init__(
        self,
        client: BangumiApiClient,
        *,
        boundary_lookback_days: int = TV_BOUNDARY_LOOKBACK_DAYS,
    ) -> None:
        if boundary_lookback_days < 0:
            raise ValueError("boundary lookback days must not be negative")
        self.client = client
        self.boundary_lookback_days = boundary_lookback_days

    def discover(self, quarter: Quarter) -> DiscoveryBatch:
        start = date(quarter.year, quarter.month, 1) - timedelta(
            days=self.boundary_lookback_days
        )
        end = _quarter_end_exclusive(quarter)
        provenance = f"search:air_date:{start.isoformat()}..{end.isoformat()}"
        try:
            candidates = self.client.search_subjects(
                air_date_start=start, air_date_end=end
            )
        except BangumiApiError as error:
            return DiscoveryBatch(
                (),
                (
                    DiscoveryFailure(
                        DiscoverySource.SEARCH,
                        provenance,
                        error.code,
                        error.summary,
                    ),
                ),
            )
        return _batch(
            [
                _from_api_candidate(candidate, None, provenance)
                for candidate in candidates
            ],
            (),
        )


def merge_discovery_batches(*batches: DiscoveryBatch) -> DiscoveryBatch:
    """Union adapter observations by ID without allowing a last response to win."""
    candidates = [candidate for batch in batches for candidate in batch.candidates]
    failures = [failure for batch in batches for failure in batch.failures]
    return _batch(candidates, failures)


def _batch(
    candidates: list[DiscoveredSubject],
    failures: list[DiscoveryFailure] | tuple[DiscoveryFailure, ...],
) -> DiscoveryBatch:
    grouped: dict[int, list[DiscoveredSubject]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.subject_id].append(candidate)
    merged = tuple(
        DiscoveredSubject(
            subject_id,
            frozenset(
                media_format
                for candidate in observations
                for media_format in candidate.media_formats
            ),
            frozenset(
                candidate_date
                for candidate in observations
                for candidate_date in candidate.candidate_dates
            ),
            frozenset(
                subject_type
                for candidate in observations
                for subject_type in candidate.subject_types
            ),
            tuple(
                sorted(
                    {
                        provenance
                        for candidate in observations
                        for provenance in candidate.provenance
                    }
                )
            ),
            next(
                (
                    candidate.detail
                    for candidate in observations
                    if candidate.detail is not None
                ),
                None,
            ),
        )
        for subject_id, observations in sorted(grouped.items())
    )
    return DiscoveryBatch(
        merged,
        tuple(
            sorted(
                failures,
                key=lambda failure: (
                    failure.source.value,
                    failure.scope,
                    failure.code,
                ),
            )
        ),
    )


def _from_api_candidate(
    candidate: CandidateSubject | SubjectDetail,
    media_format: MediaFormat | None,
    provenance: str,
) -> DiscoveredSubject:
    candidate_date = (
        candidate.air_date
        if isinstance(candidate, SubjectDetail)
        else candidate.candidate_date
    )
    return DiscoveredSubject(
        candidate.subject_id,
        frozenset(() if media_format is None else (media_format,)),
        frozenset(
            () if candidate_date is None else (candidate_date,)
        ),
        frozenset(
            () if candidate.subject_type is None else (candidate.subject_type,)
        ),
        (provenance,),
        candidate if isinstance(candidate, SubjectDetail) else None,
    )


def _browse_provenance(media_format: MediaFormat, year: int, month: int) -> str:
    return f"browse:{media_format.value}:{year:04d}-{month:02d}"


def _quarter_end_exclusive(quarter: Quarter) -> date:
    if quarter.month == 10:
        return date(quarter.year + 1, 1, 1)
    return date(quarter.year, quarter.month + 3, 1)


def _previous_month(quarter: Quarter) -> tuple[int, int]:
    if quarter.month == 1:
        return quarter.year - 1, 12
    return quarter.year, quarter.month - 1
