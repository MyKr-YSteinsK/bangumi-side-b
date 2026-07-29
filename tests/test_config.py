"""Tests for deterministic configuration loading."""

from pathlib import Path

import pytest

from bgm_side_b.config import load_project_settings, load_rules


def test_checked_in_configuration_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    settings, tag_rules, source_rules = load_rules(root / "config")

    assert settings.excluded_subject_ids == frozenset()
    assert settings.sync.api_concurrency == 3
    assert settings.main_character_relations == frozenset({"主角"})
    assert tag_rules.allowed_tags[:2] == ("喜剧", "恋爱")
    assert source_rules.order[-1] == "unknown"


@pytest.mark.parametrize(
    "content",
    [
        (
            "[filters]\nexcluded_subject_ids = [true]\n[sync]\n"
            "api_concurrency = 1\nrequest_timeout_seconds = 1\nmax_retries = 0\n"
        ),
        (
            "[filters]\nexcluded_subject_ids = [0]\n[sync]\n"
            "api_concurrency = 1\nrequest_timeout_seconds = 1\nmax_retries = 0\n"
        ),
        (
            "[filters]\nexcluded_subject_ids = []\n[sync]\n"
            "api_concurrency = true\nrequest_timeout_seconds = 1\nmax_retries = 0\n"
        ),
        (
            "[filters]\nexcluded_subject_ids = []\n[sync]\n"
            "api_concurrency = 0\nrequest_timeout_seconds = 1\nmax_retries = 0\n"
        ),
    ],
)
def test_invalid_project_settings_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bangumi.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_project_settings(path)
