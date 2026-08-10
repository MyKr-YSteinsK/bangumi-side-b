"""Compact tests for deterministic build state and dirty propagation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from bgm_side_b.build.fingerprint import (
    BuildState,
    assign_fingerprints,
    derive_dirty_set,
    read_build_state,
    shared_fingerprint,
)
from bgm_side_b.build.site_projection import (
    ArchiveIndexProjection,
    QuarterProjection,
    YearCatalogProjection,
    json_bytes,
)
from bgm_side_b.config import load_tag_rules

ROOT = Path(__file__).parents[1]


def _projections(value: str = "one"):
    quarter = QuarterProjection(value, (), (), ())
    year = YearCatalogProjection(int(value[:4]), ())
    archive = ArchiveIndexProjection((int(value[:4]),), (), value)
    return assign_fingerprints((quarter,), (year,), archive, shared="shared")


def test_fingerprint_state_is_stable_and_corrupt_state_is_dirty(tmp_path: Path) -> None:
    _, _, _, state = _projections("2026-04")
    state_path = tmp_path / "build-state.json"
    state_path.write_bytes(json_bytes(state.to_dict()))
    assert read_build_state(state_path) == state
    state_path.write_text("{not json", encoding="utf-8")
    assert read_build_state(state_path) is None


def test_dirty_propagates_quarter_changes_to_year_and_archive() -> None:
    _, _, _, first = _projections("2026-04")
    _, _, _, second = _projections("2026-04")
    assert derive_dirty_set(
        first,
        second,
        available_quarters=("2026-04",),
    ).any_dirty is False

    changed = BuildState(
        second.schema,
        second.shared,
        {"2026-04": "changed"},
        second.years,
        second.archive,
        {},
    )
    dirty = derive_dirty_set(
        first,
        changed,
        available_quarters=("2026-04",),
    )
    assert dirty.dirty_quarters == ("2026-04",)
    assert dirty.dirty_years == ("2026",)
    assert dirty.archive_dirty


def test_shared_fingerprint_includes_whitelist_and_blacklist() -> None:
    rules = load_tag_rules(
        ROOT / "config" / "allowed-tags.toml",
    )
    first = shared_fingerprint(
        stylesheet=b"css",
        script=b"js",
        tag_rules=rules,
        excluded_subject_ids=frozenset(),
    )
    second = shared_fingerprint(
        stylesheet=b"css",
        script=b"js",
        tag_rules=rules,
        excluded_subject_ids=frozenset({101}),
    )
    assert first != second

    reordered = replace(rules, allowed_tags=tuple(reversed(rules.allowed_tags)))
    assert shared_fingerprint(
        stylesheet=b"css",
        script=b"js",
        tag_rules=reordered,
        excluded_subject_ids=frozenset(),
    ) != first

    alias_only = replace(rules, aliases={"搞笑": "恋爱"})
    assert shared_fingerprint(
        stylesheet=b"css",
        script=b"js",
        tag_rules=alias_only,
        excluded_subject_ids=frozenset(),
    ) == first
