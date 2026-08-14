"""Small, Plan-13 configuration readers kept separate from legacy Build rules."""

from __future__ import annotations

import os
import re
import tempfile
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


def add_auto_excluded_subject(
    path: Path,
    subject_id: int,
    *,
    name_cn: str | None = None,
    name_original: str | None = None,
) -> bool:
    """Persist one automatic exclusion while leaving manual config untouched.

    The existing TOML is edited only inside the ``[filters]`` automatic list.
    A temporary file and an atomic replacement keep a failed write from
    exposing a partial configuration.  The return value is false when the ID
    was already present, making repeated decisions idempotent.
    """
    if not _positive_id(subject_id):
        raise ValueError("auto-excluded subject id must be positive")
    settings = load_archive_sync_settings(path)
    if subject_id in settings.auto_excluded_subject_ids:
        return False
    subject_ids = sorted((*settings.auto_excluded_subject_ids, subject_id))
    title = _comment_title(subject_id, name_cn, name_original)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    filters_start, filters_end = _filters_section(lines)
    rendered = _render_auto_exclusions(subject_ids, title, subject_id)
    auto_start = next(
        (
            index
            for index in range(filters_start + 1, filters_end)
            if _AUTO_KEY_RE.match(lines[index])
        ),
        None,
    )
    comments: dict[int, str] = {}
    if auto_start is None:
        insertion = filters_end
        block = rendered
        if insertion > filters_start + 1 and not lines[insertion - 1].endswith(
            ("\n", "\r")
        ):
            block = "\n" + block
        lines[insertion:insertion] = [block]
    else:
        auto_end = _array_end(lines, auto_start)
        comments = _auto_comments(lines[auto_start:auto_end])
        lines[auto_start:auto_end] = [
            _render_auto_exclusions(subject_ids, title, subject_id, comments)
        ]
    desired = "".join(lines)
    try:
        _atomic_replace_text(path, desired)
    except OSError:
        # A Windows replace can complete at the filesystem boundary and still
        # surface an exception.  Reconcile that observable final state before
        # reporting failure to callers.
        if path.read_text(encoding="utf-8") == desired:
            return True
        raise
    return True


def restore_archive_config(path: Path, content: bytes) -> None:
    """Atomically restore a previously captured UTF-8 configuration snapshot."""
    _atomic_replace_bytes(path, content)


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


_AUTO_KEY_RE = re.compile(r"^\s*auto_excluded_subject_ids\s*=")
_TABLE_RE = re.compile(r"^\s*\[[^]]+\]\s*$")


def _filters_section(lines: list[str]) -> tuple[int, int]:
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == "[filters]"),
        None,
    )
    if start is None:
        raise ValueError("bangumi.toml must define a filters table")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if _TABLE_RE.match(lines[index])
        ),
        len(lines),
    )
    return start, end


def _array_end(lines: list[str], start: int) -> int:
    depth = 0
    for index in range(start, len(lines)):
        value = lines[index].split("#", 1)[0]
        depth += value.count("[") - value.count("]")
        if index == start and depth <= 0:
            return index + 1
        if index > start and depth <= 0:
            return index + 1
    raise ValueError("auto_excluded_subject_ids must be a closed array")


def _render_auto_exclusions(
    subject_ids: list[int],
    title: str,
    subject_id: int,
    comments: Mapping[int, str] | None = None,
) -> str:
    known_comments = {} if comments is None else comments
    lines = ["auto_excluded_subject_ids = [\n"]
    for item in subject_ids:
        comment = known_comments.get(item, title if item == subject_id else str(item))
        lines.append(f"    {item}, # {comment}\n")
    lines.append("]\n")
    return "".join(lines)


def _auto_comments(lines: list[str]) -> dict[int, str]:
    comments: dict[int, str] = {}
    for line in lines[1:]:
        value, _, comment = line.partition("#")
        match = re.search(r"\b(\d+)\b", value)
        if match and comment.strip():
            comments[int(match.group(1))] = " ".join(comment.split())
    return comments


def _comment_title(
    subject_id: int, name_cn: str | None, name_original: str | None
) -> str:
    for value in (name_cn, name_original):
        if isinstance(value, str) and value.strip():
            return " ".join(value.split()).replace("\r", " ").replace("\n", " ")
    return str(subject_id)


def _atomic_replace_text(path: Path, content: str) -> None:
    _atomic_replace_bytes(path, content.encode("utf-8"))


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


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
