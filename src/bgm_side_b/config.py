"""Typed loaders for the repository's deterministic configuration."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class SyncSettings:
    """Network settings used by a future synchronisation command."""

    api_concurrency: int
    request_timeout_seconds: int
    max_retries: int


@dataclass(frozen=True)
class ProjectSettings:
    """Configuration that is shared by command orchestration."""

    excluded_subject_ids: frozenset[int]
    sync: SyncSettings


@dataclass(frozen=True)
class TagRules:
    """Exact display-tag mappings and their fixed whitelist order."""

    allowed_tags: tuple[str, ...]
    aliases: Mapping[str, str]


@dataclass(frozen=True)
class SourceRules:
    """Exact source extraction and display-order rules."""

    values: frozenset[str]
    order: tuple[str, ...]
    infobox_keys: frozenset[str]
    infobox_values: Mapping[str, str]
    tag_values: Mapping[str, str]


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as file:
        return tomllib.load(file)


def load_project_settings(path: Path) -> ProjectSettings:
    """Load blacklist and sync settings from ``bangumi.toml``."""
    data = _read_toml(path)
    filters = data["filters"]
    sync = data["sync"]
    if not isinstance(filters, dict) or not isinstance(sync, dict):
        raise ValueError("bangumi.toml must define filters and sync tables")

    excluded = filters["excluded_subject_ids"]
    valid_excluded = isinstance(excluded, list) and all(
        isinstance(item, int) for item in excluded
    )
    if not valid_excluded:
        raise ValueError("excluded_subject_ids must be an array of integers")

    values = ("api_concurrency", "request_timeout_seconds", "max_retries")
    if not all(isinstance(sync.get(value), int) for value in values):
        raise ValueError("sync settings must be integers")

    return ProjectSettings(
        excluded_subject_ids=frozenset(excluded),
        sync=SyncSettings(
            api_concurrency=sync["api_concurrency"],
            request_timeout_seconds=sync["request_timeout_seconds"],
            max_retries=sync["max_retries"],
        ),
    )


def load_tag_rules(allowed_path: Path, aliases_path: Path) -> TagRules:
    """Load display-tag whitelist and exact aliases from TOML files."""
    allowed_data = _read_toml(allowed_path)
    alias_data = _read_toml(aliases_path)
    allowed = allowed_data["allowed_tags"]
    aliases = alias_data["aliases"]
    valid_allowed = isinstance(allowed, list) and all(
        isinstance(item, str) for item in allowed
    )
    if not valid_allowed:
        raise ValueError("allowed_tags must be an array of strings")
    if len(set(allowed)) != len(allowed):
        raise ValueError("allowed_tags must not contain duplicates")
    if not isinstance(aliases, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in aliases.items()
    ):
        raise ValueError("aliases must be a table of strings")
    if not set(aliases.values()).issubset(allowed):
        raise ValueError("aliases must target an allowed tag")

    return TagRules(tuple(allowed), MappingProxyType(dict(aliases)))


def load_source_rules(path: Path) -> SourceRules:
    """Load exact source-evidence rules from ``source-rules.toml``."""
    data = _read_toml(path)
    sources = data["sources"]
    infobox = data["infobox"]
    tag_fallback = data["tag_fallback"]
    if not all(isinstance(value, dict) for value in (sources, infobox, tag_fallback)):
        raise ValueError(
            "source rules must use sources, infobox, and tag_fallback tables"
        )

    values = sources["values"]
    order = sources["order"]
    keys = infobox["source_keys"]
    infobox_values = infobox["exact_values"]
    tag_values = tag_fallback["exact_values"]
    collections = (values, order, keys)
    if not all(
        isinstance(items, list) and all(isinstance(item, str) for item in items)
        for items in collections
    ):
        raise ValueError("source rules must use arrays of strings")
    mappings = (infobox_values, tag_values)
    if not all(
        isinstance(mapping, dict)
        and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mapping.items()
        )
        for mapping in mappings
    ):
        raise ValueError("source mappings must use strings")

    source_values = frozenset(values)
    if set(order) != source_values:
        raise ValueError("source order must include every source exactly once")
    mapped_values = set(infobox_values.values()) | set(tag_values.values())
    if not mapped_values.issubset(source_values):
        raise ValueError("source mappings must target known sources")

    return SourceRules(
        values=source_values,
        order=tuple(order),
        infobox_keys=frozenset(keys),
        infobox_values=MappingProxyType(dict(infobox_values)),
        tag_values=MappingProxyType(dict(tag_values)),
    )


def load_rules(config_directory: Path) -> tuple[ProjectSettings, TagRules, SourceRules]:
    """Load all deterministic project configuration from a directory."""
    return (
        load_project_settings(config_directory / "bangumi.toml"),
        load_tag_rules(
            config_directory / "allowed-tags.toml",
            config_directory / "tag-aliases.toml",
        ),
        load_source_rules(config_directory / "source-rules.toml"),
    )
