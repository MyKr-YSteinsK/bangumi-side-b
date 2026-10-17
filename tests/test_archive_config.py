"""Strict parsing for archive synchronization policy configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from bgm_side_b.archive_config import load_archive_sync_settings

ROOT = Path(__file__).resolve().parents[1]


def _settings_file(tmp_path: Path, exemption_value: str) -> Path:
    path = tmp_path / "bangumi.toml"
    path.write_text(
        """
[filters]
excluded_subject_ids = []
auto_excluded_subject_ids = []
"""
        + f"low_rating_exempt_subject_ids = {exemption_value}\n"
        + """
[sync]
api_concurrency = 1
request_timeout_seconds = 1
max_retries = 0
""",
        encoding="utf-8",
    )
    return path


def test_project_configuration_has_only_the_approved_low_rating_exemption() -> None:
    settings = load_archive_sync_settings(ROOT / "config" / "bangumi.toml")

    assert settings.low_rating_exempt_subject_ids == frozenset({501729})
    assert len(settings.excluded_subject_ids) == 2
    assert len(settings.auto_excluded_subject_ids) == 413
    assert 501729 not in settings.auto_excluded_subject_ids
    assert 501729 not in settings.excluded_subject_ids


@pytest.mark.parametrize(
    "value",
    (
        "true",
        "[true]",
        '["501729"]',
        "[0]",
        "[501729, 501729]",
        "[501730]",
    ),
)
def test_low_rating_exemption_rejects_invalid_or_unapproved_values(
    tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValueError, match="low_rating_exempt_subject_ids"):
        load_archive_sync_settings(_settings_file(tmp_path, value))


def test_missing_low_rating_exemption_defaults_to_empty(tmp_path: Path) -> None:
    path = _settings_file(tmp_path, "[]")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "low_rating_exempt_subject_ids = []\n", ""
        ),
        encoding="utf-8",
    )

    assert load_archive_sync_settings(path).low_rating_exempt_subject_ids == frozenset()
