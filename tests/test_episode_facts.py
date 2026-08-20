"""Strict canonical episode-count normalization coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bgm_side_b.api import SubjectDetail
from bgm_side_b.sync import _episode_count, _resolve_episode_count

ROOT = Path(__file__).resolve().parents[1]
RE_ZERO = json.loads(
    (ROOT / "tests" / "fixtures" / "api" / "subject-547888.json").read_text(
        encoding="utf-8"
    )
)
RE_571784 = json.loads(
    (ROOT / "tests" / "fixtures" / "api" / "subject-571784.json").read_text(
        encoding="utf-8"
    )
)
RE_MISMATCH = json.loads(
    (
        ROOT / "tests" / "fixtures" / "api" / "subject-episode-count-mismatch.json"
    ).read_text(encoding="utf-8")
)


def _detail(
    *, total: object = 0, eps: object = 0, infobox: object = ()
) -> SubjectDetail:
    payload = {
        **RE_ZERO,
        "total_episodes": total,
        "eps": eps,
        "infobox": [{"key": key, "value": value} for key, value in infobox],
    }
    return SubjectDetail.from_payload(payload)


def test_bgm_547888_canonical_detail_has_eleven_episodes() -> None:
    detail = SubjectDetail.from_payload(RE_ZERO)

    assert detail.subject_id == 547888
    assert detail.total_episodes == 11
    assert detail.eps == 11
    assert _episode_count(detail) == 11


def test_bgm_571784_fixture_resolves_twelve_planned_episodes() -> None:
    detail = SubjectDetail.from_payload(RE_571784)

    assert detail.subject_id == 571784
    assert detail.total_episodes == 12
    assert detail.eps == 12
    assert _resolve_episode_count(detail).value == 12


def test_eps_wins_when_bangumi_episode_row_count_differs() -> None:
    detail = SubjectDetail.from_payload(RE_MISMATCH)

    result = _resolve_episode_count(detail)

    assert (result.value, result.source, result.warning) == (
        12,
        "subject_structured",
        None,
    )


@pytest.mark.parametrize(
    ("total", "eps", "infobox", "expected"),
    (
        (0, 11, (), 11),
        (0, 0, (("话数", "11"),), 11),
        (0, 0, (("话数", 11),), 11),
        (0, 0, (("话数", "11话"),), None),
        (-1, -2, (("话数", "*"),), None),
        (None, None, (), None),
    ),
)
def test_episode_count_accepts_only_positive_canonical_or_strict_infobox_values(
    total: object,
    eps: object,
    infobox: tuple[tuple[str, object], ...],
    expected: int | None,
) -> None:
    assert _episode_count(_detail(total=total, eps=eps, infobox=infobox)) == expected


def test_episode_count_resolver_reports_canonical_and_infobox_sources() -> None:
    canonical = _resolve_episode_count(_detail(total=12, eps=12))
    assert (canonical.value, canonical.source, canonical.warning) == (
        12,
        "subject_structured",
        None,
    )

    infobox = _resolve_episode_count(_detail(infobox=(("话数", "12"),)))
    assert (infobox.value, infobox.source, infobox.warning) == (12, "infobox", None)


@pytest.mark.parametrize(
    ("total", "eps", "infobox"),
    (
        (0, 12, (("话数", "13"),)),
    ),
)
def test_episode_count_conflicts_fail_closed(
    total: object, eps: object, infobox: tuple[tuple[str, object], ...]
) -> None:
    result = _resolve_episode_count(_detail(total=total, eps=eps, infobox=infobox))

    assert (result.value, result.source, result.warning) == (
        None,
        "conflict",
        "episode_count_conflict",
    )


@pytest.mark.parametrize("total", (8, 20))
def test_total_episodes_never_overrides_positive_eps(total: int) -> None:
    result = _resolve_episode_count(_detail(total=total, eps=12))

    assert (result.value, result.source, result.warning) == (
        12,
        "subject_structured",
        None,
    )


def test_total_episodes_alone_is_not_a_planned_count() -> None:
    result = _resolve_episode_count(_detail(total=8))

    assert (result.value, result.source, result.warning) == (None, "unknown", None)


def test_episode_registry_is_only_a_fallback_after_missing_trusted_facts() -> None:
    result = _resolve_episode_count(_detail(total=8), registry_count=12)

    assert (result.value, result.source, result.warning) == (
        12,
        "episode_registry",
        None,
    )
