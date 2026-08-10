"""Stable build-state fingerprints and simple dirty propagation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from bgm_side_b.build.site_projection import (
    PROJECTION_VERSION,
    ArchiveIndexProjection,
    QuarterProjection,
    YearCatalogProjection,
    json_bytes,
)

STATE_SCHEMA = 1


@dataclass(frozen=True)
class BuildState:
    """The compact derived state persisted after a successful site patch."""

    schema: int
    shared: str
    quarters: Mapping[str, str]
    years: Mapping[str, str]
    archive: str
    artifacts: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "shared": self.shared,
            "quarters": dict(sorted(self.quarters.items())),
            "years": dict(sorted(self.years.items())),
            "archive": self.archive,
            "artifacts": dict(sorted(self.artifacts.items())),
        }


@dataclass(frozen=True)
class DirtySet:
    """The minimum public scopes that must be regenerated."""

    dirty_quarters: tuple[str, ...]
    skipped_quarters: tuple[str, ...]
    dirty_years: tuple[str, ...]
    archive_dirty: bool
    shared_dirty: bool
    removed_quarters: tuple[str, ...] = ()

    @property
    def any_dirty(self) -> bool:
        return bool(
            self.dirty_quarters
            or self.dirty_years
            or self.archive_dirty
            or self.shared_dirty
            or self.removed_quarters
        )


def fingerprint(value: object) -> str:
    """Hash one canonical JSON value with the repository's UTF-8 policy."""
    return hashlib.sha256(json_bytes(value)).hexdigest()


def shared_fingerprint(
    *,
    stylesheet: bytes,
    script: bytes,
    tag_rules: object,
    excluded_subject_ids: frozenset[int],
    site_base_path: str = "/bangumi-side-b/",
) -> str:
    """Fingerprint source assets and global projection inputs."""
    return fingerprint(
        {
            "projection": PROJECTION_VERSION,
            "site_base_path": site_base_path,
            "stylesheet": hashlib.sha256(stylesheet).hexdigest(),
            "script": hashlib.sha256(script).hexdigest(),
            "tag_rules": _tag_rules_value(tag_rules),
            "blacklist": sorted(excluded_subject_ids),
        }
    )


def assign_fingerprints(
    quarters: tuple[QuarterProjection, ...],
    years: tuple[YearCatalogProjection, ...],
    archive: ArchiveIndexProjection,
    *,
    shared: str,
) -> tuple[
    tuple[QuarterProjection, ...],
    tuple[YearCatalogProjection, ...],
    ArchiveIndexProjection,
    BuildState,
]:
    """Return projections with deterministic revisions and a state skeleton."""
    quarter_values = tuple(
        replace(item, fingerprint=fingerprint(_without_revision(item.to_dict())))
        for item in sorted(quarters, key=lambda item: item.quarter)
    )
    quarter_by_label = {item.quarter: item.fingerprint for item in quarter_values}
    year_values = tuple(
        replace(
            item,
            fingerprint=fingerprint(
                {
                    "projection": PROJECTION_VERSION,
                    "year": item.year,
                    "records": item.records,
                    "quarters": sorted(
                        label
                        for label in quarter_by_label
                        if int(label[:4]) == item.year
                    ),
                }
            ),
        )
        for item in sorted(years, key=lambda item: item.year)
    )
    year_by_label = {str(item.year): item.fingerprint for item in year_values}
    archive_value = fingerprint(
        {
            "projection": PROJECTION_VERSION,
            "years": archive.years,
            "quarters": archive.quarters,
        }
    )
    archive_value = replace(archive, fingerprint=archive_value)
    state = BuildState(
        STATE_SCHEMA,
        shared,
        quarter_by_label,
        year_by_label,
        archive_value.fingerprint,
        {},
    )
    return quarter_values, year_values, archive_value, state


def read_build_state(path: Path) -> BuildState | None:
    """Read a state file, returning ``None`` for missing or unsafe state."""
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != STATE_SCHEMA:
        return None
    if not isinstance(value.get("shared"), str) or not isinstance(
        value.get("archive"), str
    ):
        return None
    quarters = _string_map(value.get("quarters"))
    years = _string_map(value.get("years"))
    artifacts = _string_map(value.get("artifacts", {}))
    if quarters is None or years is None or artifacts is None:
        return None
    return BuildState(
        STATE_SCHEMA,
        value["shared"],
        quarters,
        years,
        value["archive"],
        artifacts,
    )


def write_build_state(path: Path, state: BuildState) -> bytes:
    """Return stable state bytes and write only through a caller-controlled path."""
    content = json_bytes(state.to_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def derive_dirty_set(
    previous: BuildState | None,
    current: BuildState,
    *,
    available_quarters: tuple[str, ...],
    requested_quarters: tuple[str, ...] | None = None,
) -> DirtySet:
    """Compare state maps and propagate quarter changes to owning indexes."""
    labels = tuple(sorted(set(available_quarters)))
    if requested_quarters is not None:
        requested = set(requested_quarters)
        labels = tuple(label for label in labels if label in requested)
    previous_quarters = {} if previous is None else dict(previous.quarters)
    changed = tuple(
        label
        for label in labels
        if previous is None
        or previous_quarters.get(label) != current.quarters.get(label)
    )
    removed = tuple(
        label
        for label in sorted(set(previous_quarters) - set(current.quarters))
        if requested_quarters is None or label in set(requested_quarters)
    )
    changed_years = {
        str(int(label[:4])) for label in changed if len(label) == 7 and label[4] == "-"
    }
    dirty_years = tuple(
        year
        for year in sorted(set(current.years) | set(changed_years))
        if previous is None
        or previous.years.get(year) != current.years.get(year)
        or year in changed_years
    )
    shared_dirty = previous is None or previous.shared != current.shared
    archive_dirty = (
        previous is None
        or previous.archive != current.archive
        or bool(changed)
        or bool(removed)
    )
    skipped = tuple(label for label in labels if label not in changed)
    return DirtySet(
        tuple(changed),
        skipped,
        dirty_years,
        archive_dirty,
        shared_dirty,
        removed,
    )


def _without_revision(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    result.pop("revision", None)
    return result


def _tag_rules_value(value: object) -> object:
    allowed = getattr(value, "allowed_tags", None)
    aliases = getattr(value, "aliases", None)
    if not isinstance(allowed, tuple) or aliases is None:
        return repr(value)
    return {
        "allowed_tags": list(allowed),
        "aliases": dict(sorted(dict(aliases).items())),
    }


def _string_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        return None
    return dict(value)
