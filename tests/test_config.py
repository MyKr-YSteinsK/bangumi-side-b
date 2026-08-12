"""Tests for the active exact-tag configuration contract."""

from pathlib import Path

import pytest

from bgm_side_b.config import load_tag_rules

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_tag_whitelist_loads_without_alias_metadata() -> None:
    rules = load_tag_rules(ROOT / "config" / "allowed-tags.toml")

    assert rules.allowed_tags[:2] == ("喜剧", "恋爱")
    assert not hasattr(rules, "aliases")


@pytest.mark.parametrize(
    "content",
    (
        "allowed_tags = [\"喜剧\", \"喜剧\"]\n",
        "allowed_tags = [\"喜剧\", 1]\n",
        "allowed_tags = [\"\"]\n",
    ),
)
def test_tag_whitelist_rejects_non_exact_values(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "allowed-tags.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_tag_rules(path)
