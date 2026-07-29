"""Tests for safe inlined quick-drawer data and static interaction contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from test_templates import _quarter

from bgm_side_b.build.frontend import drawer_json

ROOT = Path(__file__).parents[1]


def test_drawer_json_cannot_escape_its_script_element() -> None:
    quarter = _quarter()
    drawer = replace(
        quarter.details[0].drawer,
        summary="</script><img src=x onerror=alert(1)>\u2028\u2029",
    )
    unsafe_quarter = replace(
        quarter, details=(replace(quarter.details[0], drawer=drawer),)
    )
    payload = drawer_json(unsafe_quarter)
    assert "</script>" not in payload
    assert "\\u003c/script\\u003e" in payload
    assert "\\u2028" in payload
    assert "\\u2029" in payload


def test_native_frontend_stays_offline_and_uses_only_supported_sort_contract() -> None:
    script = (ROOT / "static" / "js" / "site.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "quarter.html").read_text(encoding="utf-8")
    for value in ("score-desc", "score-asc", "votes-desc", "votes-asc"):
        assert value in template
    assert "history.pushState" in script
    assert "bsbArchive" in script
    assert "pageshow" in script
    assert "history.back" in script
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script
