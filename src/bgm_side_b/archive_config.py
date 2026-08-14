"""Small, Plan-13 configuration readers kept separate from legacy Build rules."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bgm_side_b.domain import SourceType


@dataclass(frozen=True)
class ArchiveSyncSettings:
    """The settings consumed by the clean archive synchronization workflow."""

    excluded_subject_ids: frozenset[int]
    api_concurrency: int
    request_timeout_seconds: int
    max_retries: int
    auto_excluded_subject_ids: frozenset[int] = frozenset()

    @property
    def all_excluded_subject_ids(self) -> frozenset[int]:
        """Return the read-only union of manual and automatic exclusions."""
        return self.excluded_subject_ids | self.auto_excluded_subject_ids


@dataclass(frozen=True)
class ArchiveSourceRules:
    """Exact configured source evidence used by clean fact normalization."""

    infobox_keys: frozenset[str]
    infobox_values: Mapping[str, SourceType]
    tag_values: Mapping[str, SourceType]


def load_archive_sync_settings(path: Path) -> ArchiveSyncSettings:
    """Read only blacklist and network controls needed by the clean Sync path."""
    with path.open("rb") as file:
        data = tomllib.load(file)
    filters = data.get("filters")
    sync = data.get("sync")
    if not isinstance(filters, dict) or not isinstance(sync, dict):
        raise ValueError("bangumi.toml must define filters and sync tables")
    excluded = _subject_ids(filters, "excluded_subject_ids", required=True)
    auto_excluded = _subject_ids(
        filters, "auto_excluded_subject_ids", required=False
    )
    values = ("api_concurrency", "request_timeout_seconds", "max_retries")
    if not all(_integer(sync.get(value)) for value in values):
        raise ValueError("sync settings must be integers")
    concurrency = sync["api_concurrency"]
    timeout = sync["request_timeout_seconds"]
    retries = sync["max_retries"]
    if concurrency < 1 or timeout <= 0 or retries < 0:
        raise ValueError(
            "sync settings must have positive concurrency/timeout and retries"
        )
    return ArchiveSyncSettings(
        frozenset(excluded),
        concurrency,
        timeout,
        retries,
        frozenset(auto_excluded),
    )


def should_auto_blacklist(
    air_date: date | None,
    rating_count: int | None,
    evaluation_date: date,
) -> bool:
    """Return whether a reliable, older low-rating-count subject is eligible.

    ``evaluation_date`` is supplied by the caller so the rule remains
    deterministic in both synchronization and tests.  Missing values are
    deliberately treated as unknown rather than as a zero rating count.
    """
    if air_date is None or rating_count is None or rating_count < 0:
        return False
    return (evaluation_date - air_date).days > 7 and rating_count < 30


def _subject_ids(
    filters: Mapping[str, object], key: str, *, required: bool
) -> list[int]:
    value = filters.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(_positive_id(item) for item in value):
        raise ValueError(f"{key} must be an array of positive integers")
    if len(set(value)) != len(value):
        raise ValueError(f"{key} must not contain duplicates")
    return value


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_id(value: object) -> bool:
    return _integer(value) and value > 0


def load_archive_source_rules(path: Path) -> ArchiveSourceRules:
    """Read only exact source evidence mappings into archive-domain values."""
    with path.open("rb") as file:
        data = tomllib.load(file)
    infobox = data.get("infobox")
    tag_fallback = data.get("tag_fallback")
    if not isinstance(infobox, dict) or not isinstance(tag_fallback, dict):
        raise ValueError("source rules must define infobox and tag_fallback tables")
    keys = infobox.get("source_keys")
    infobox_values = infobox.get("exact_values")
    tag_values = tag_fallback.get("exact_values")
    if not isinstance(keys, list) or not all(
        isinstance(value, str) and value for value in keys
    ):
        raise ValueError("source rule keys must be non-empty strings")
    return ArchiveSourceRules(
        frozenset(keys),
        _source_mapping(infobox_values),
        _source_mapping(tag_values),
    )


def _source_mapping(value: object) -> Mapping[str, SourceType]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("source rule mappings must contain strings")
    types = {
        "manga": SourceType.MANGA,
        "light_novel": SourceType.LIGHT_NOVEL,
        "novel": SourceType.NOVEL,
        "game": SourceType.GAME,
        "visual_novel": SourceType.VISUAL_NOVEL,
        "original": SourceType.ORIGINAL_ANIME,
        "other": SourceType.OTHER_ADAPTATION,
    }
    try:
        return {key: types[item] for key, item in value.items()}
    except KeyError as error:
        raise ValueError("source rule mapping contains an unknown source") from error
