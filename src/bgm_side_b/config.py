"""Typed loaders for the repository's deterministic configuration."""

from __future__ import annotations

import re
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
class ReleaseScope:
    """The deliberately narrow first-release scope."""

    release_quarters: tuple[str, ...]
    formats: tuple[str, ...]
    include_continuations: bool


@dataclass(frozen=True)
class CountryFilter:
    """Exact structured Infobox keys and tokens accepted for Japan."""

    required_country: str
    country_keys: frozenset[str]
    country_value_aliases: frozenset[str]


@dataclass(frozen=True)
class ProjectSettings:
    """Configuration that is shared by command orchestration."""

    excluded_subject_ids: frozenset[int]
    sync: SyncSettings
    scope: ReleaseScope
    country_filter: CountryFilter
    main_character_relations: frozenset[str]
    end_date_infobox_keys: frozenset[str]
    chinese_name_infobox_keys: frozenset[str]


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
    roles = data.get("roles")
    infobox = data.get("infobox")
    scope = data.get("scope")
    country_filter = data.get("country_filter")
    if not all(
        isinstance(value, dict)
        for value in (filters, sync, roles, infobox, scope, country_filter)
    ):
        raise ValueError(
            "bangumi.toml must define filters, sync, scope, country_filter, "
            "roles, and infobox tables"
        )

    excluded = filters["excluded_subject_ids"]
    valid_excluded = isinstance(excluded, list) and all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in excluded
    )
    if not valid_excluded:
        raise ValueError("excluded_subject_ids must be an array of positive integers")

    values = ("api_concurrency", "request_timeout_seconds", "max_retries")
    if not all(_is_integer(sync.get(value)) for value in values):
        raise ValueError("sync settings must be integers")
    if sync["api_concurrency"] < 1:
        raise ValueError("api_concurrency must be at least 1")
    if sync["request_timeout_seconds"] <= 0:
        raise ValueError("request_timeout_seconds must be greater than 0")
    if sync["max_retries"] < 0:
        raise ValueError("max_retries must not be negative")

    release_quarters = scope.get("release_quarters")
    if not _valid_release_quarters(release_quarters):
        raise ValueError("release_quarters must contain valid unique YYYY-MM quarters")
    formats = scope.get("formats")
    if formats != ["tv"]:
        raise ValueError("the current release scope only supports formats = [\"tv\"]")
    include_continuations = scope.get("include_continuations")
    if include_continuations is not False:
        raise ValueError(
            "the current release scope requires include_continuations = false"
        )

    required_country = country_filter.get("required_country")
    country_keys = country_filter.get("country_keys")
    country_aliases = country_filter.get("country_value_aliases")
    if required_country != "日本":
        raise ValueError("the current release scope requires required_country = 日本")
    if not _valid_string_array(country_keys):
        raise ValueError("country_keys must be a non-empty unique string array")
    if not _valid_string_array(country_aliases) or set(country_aliases) != {
        "日本",
        "Japan",
    }:
        raise ValueError(
            "country_value_aliases must be the exact tokens 日本 and Japan"
        )

    main_relations = roles.get("main_character_relations")
    if not isinstance(main_relations, list) or not main_relations or not all(
        isinstance(value, str) and value for value in main_relations
    ):
        raise ValueError("main_character_relations must be a non-empty string array")
    if len(set(main_relations)) != len(main_relations):
        raise ValueError("main_character_relations must not contain duplicates")

    end_date_keys = infobox.get("end_date_keys")
    if not isinstance(end_date_keys, list) or not end_date_keys or not all(
        isinstance(value, str) and value for value in end_date_keys
    ):
        raise ValueError("end_date_keys must be a non-empty string array")
    if len(set(end_date_keys)) != len(end_date_keys):
        raise ValueError("end_date_keys must not contain duplicates")

    chinese_name_keys = infobox.get("chinese_name_keys")
    if not isinstance(chinese_name_keys, list) or not chinese_name_keys or not all(
        isinstance(value, str) and value for value in chinese_name_keys
    ):
        raise ValueError("chinese_name_keys must be a non-empty string array")
    if len(set(chinese_name_keys)) != len(chinese_name_keys):
        raise ValueError("chinese_name_keys must not contain duplicates")

    return ProjectSettings(
        excluded_subject_ids=frozenset(excluded),
        sync=SyncSettings(
            api_concurrency=sync["api_concurrency"],
            request_timeout_seconds=sync["request_timeout_seconds"],
            max_retries=sync["max_retries"],
        ),
        scope=ReleaseScope(
            release_quarters=tuple(release_quarters),
            formats=tuple(formats),
            include_continuations=include_continuations,
        ),
        country_filter=CountryFilter(
            required_country=required_country,
            country_keys=frozenset(country_keys),
            country_value_aliases=frozenset(country_aliases),
        ),
        main_character_relations=frozenset(main_relations),
        end_date_infobox_keys=frozenset(end_date_keys),
        chinese_name_infobox_keys=frozenset(chinese_name_keys),
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


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_string_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _valid_release_quarters(value: object) -> bool:
    if not _valid_string_array(value):
        return False
    for item in value:
        match = re.fullmatch(r"(\d{4})-(\d{2})", item)
        if match is None or int(match.group(1)) < 1 or int(match.group(2)) not in {
            1,
            4,
            7,
            10,
        }:
            return False
    return True
