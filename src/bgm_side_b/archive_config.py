"""Small, Plan-13 configuration readers kept separate from legacy Build rules."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchiveSyncSettings:
    """The settings consumed by the clean archive synchronization workflow."""

    excluded_subject_ids: frozenset[int]
    api_concurrency: int
    request_timeout_seconds: int
    max_retries: int


def load_archive_sync_settings(path: Path) -> ArchiveSyncSettings:
    """Read only blacklist and network controls needed by the clean Sync path."""
    with path.open("rb") as file:
        data = tomllib.load(file)
    filters = data.get("filters")
    sync = data.get("sync")
    if not isinstance(filters, dict) or not isinstance(sync, dict):
        raise ValueError("bangumi.toml must define filters and sync tables")
    excluded = filters.get("excluded_subject_ids")
    if not isinstance(excluded, list) or not all(
        _positive_id(item) for item in excluded
    ):
        raise ValueError("excluded_subject_ids must be an array of positive integers")
    if len(set(excluded)) != len(excluded):
        raise ValueError("excluded_subject_ids must not contain duplicates")
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
    return ArchiveSyncSettings(frozenset(excluded), concurrency, timeout, retries)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_id(value: object) -> bool:
    return _integer(value) and value > 0
