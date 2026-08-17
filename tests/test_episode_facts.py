"""Strict canonical episode-count normalization coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bgm_side_b.api import SubjectDetail
from bgm_side_b.sync import _episode_count

ROOT = Path(__file__).resolve().parents[1]
RE_ZERO = json.loads(
    (ROOT / "tests" / "fixtures" / "api" / "subject-547888.json").read_text(
        encoding="utf-8"
    )
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
