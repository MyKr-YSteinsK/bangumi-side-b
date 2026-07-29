"""Tests for deterministic configuration loading."""

from pathlib import Path

from bgm_side_b.config import load_rules


def test_checked_in_configuration_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    settings, tag_rules, source_rules = load_rules(root / "config")

    assert settings.excluded_subject_ids == frozenset()
    assert settings.sync.api_concurrency == 3
    assert tag_rules.allowed_tags[:2] == ("喜剧", "恋爱")
    assert source_rules.order[-1] == "unknown"
