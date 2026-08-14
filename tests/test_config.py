"""Tests for the active exact-tag configuration contract."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from bgm_side_b.archive_config import load_archive_sync_settings, should_auto_blacklist
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


@pytest.mark.parametrize(
    ("days", "rating_count", "expected"),
    (
        (7, 29, False),
        (8, 29, True),
        (8, 30, False),
        (8, 0, True),
    ),
)
def test_auto_blacklist_rule_uses_strict_age_and_rating_boundaries(
    days: int, rating_count: int, expected: bool
) -> None:
    evaluation_date = date(2026, 8, 14)

    assert (
        should_auto_blacklist(
            evaluation_date - timedelta(days=days), rating_count, evaluation_date
        )
        is expected
    )


@pytest.mark.parametrize(
    ("air_date", "rating_count"),
    ((None, 0), (date(2026, 8, 1), None)),
)
def test_auto_blacklist_rule_does_not_guess_missing_facts(
    air_date: date | None, rating_count: int | None
) -> None:
    assert not should_auto_blacklist(air_date, rating_count, date(2026, 8, 14))


def test_archive_sync_settings_accepts_old_config_and_exposes_union(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bangumi.toml"
    path.write_text(
        """
[filters]
excluded_subject_ids = [101]

[sync]
api_concurrency = 3
request_timeout_seconds = 20
max_retries = 3
""".lstrip(),
        encoding="utf-8",
    )

    settings = load_archive_sync_settings(path)

    assert settings.auto_excluded_subject_ids == frozenset()
    assert settings.all_excluded_subject_ids == frozenset({101})


def test_archive_sync_settings_loads_distinct_auto_exclusions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bangumi.toml"
    path.write_text(
        """
[filters]
excluded_subject_ids = [101]
auto_excluded_subject_ids = [202, 303]

[sync]
api_concurrency = 3
request_timeout_seconds = 20
max_retries = 3
""".lstrip(),
        encoding="utf-8",
    )

    settings = load_archive_sync_settings(path)

    assert settings.excluded_subject_ids == frozenset({101})
    assert settings.auto_excluded_subject_ids == frozenset({202, 303})
    assert settings.all_excluded_subject_ids == frozenset({101, 202, 303})
