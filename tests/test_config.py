"""Tests for the active exact-tag configuration contract."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from bgm_side_b.archive_config import (
    add_auto_excluded_subject,
    load_archive_sync_settings,
    remove_auto_excluded_subject,
    remove_auto_excluded_subjects,
    should_auto_blacklist,
)
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


def _blacklist_config(tmp_path: Path, *, auto: str = "") -> Path:
    path = tmp_path / "bangumi.toml"
    auto_block = f"auto_excluded_subject_ids = {auto}\n" if auto else ""
    path.write_text(
        (
            "[filters]\n"
            "excluded_subject_ids = [101, 404] # manual stays unchanged\n"
            f"{auto_block}\n"
            "[sync]\n"
            "api_concurrency = 3\n"
            "request_timeout_seconds = 20\n"
            "max_retries = 3\n"
        ),
        encoding="utf-8",
    )
    return path


def test_add_auto_excluded_subject_keeps_manual_config_and_writes_title_comment(
    tmp_path: Path,
) -> None:
    path = _blacklist_config(tmp_path)

    assert add_auto_excluded_subject(
        path, 202, name_cn="中文名", name_original="Original"
    )
    content = path.read_text(encoding="utf-8")

    assert "excluded_subject_ids = [101, 404] # manual stays unchanged" in content
    assert "auto_excluded_subject_ids = [" in content
    assert "202, # 中文名" in content
    assert (
        load_archive_sync_settings(path).auto_excluded_subject_ids == frozenset({202})
    )


def test_add_auto_excluded_subject_is_idempotent_and_stably_sorted(
    tmp_path: Path,
) -> None:
    path = _blacklist_config(tmp_path, auto="[303, 202]")
    before = path.read_text(encoding="utf-8")

    assert add_auto_excluded_subject(path, 202, name_cn="ignored") is False
    assert path.read_text(encoding="utf-8") == before

    assert add_auto_excluded_subject(path, 101, name_original="新标题")
    content = path.read_text(encoding="utf-8")
    assert content.index("101,") < content.index("202,") < content.index("303,")
    assert "303, # 303" in content


def test_add_auto_excluded_subject_falls_back_to_original_or_id(
    tmp_path: Path,
) -> None:
    path = _blacklist_config(tmp_path)
    add_auto_excluded_subject(path, 202, name_original="Original 名")
    assert "202, # Original 名" in path.read_text(encoding="utf-8")

    path = _blacklist_config(tmp_path)
    add_auto_excluded_subject(path, 303)
    assert "303, # 303" in path.read_text(encoding="utf-8")


def test_add_auto_excluded_subject_write_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _blacklist_config(tmp_path)
    original = path.read_bytes()

    def fail_replace(*_: object, **__: object) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr("bgm_side_b.archive_config.os.replace", fail_replace)

    with pytest.raises(PermissionError, match="replace denied"):
        add_auto_excluded_subject(path, 202, name_cn="标题")

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".bangumi.toml.*.tmp"))


def test_remove_auto_excluded_subjects_preserves_manual_config_and_comments(
    tmp_path: Path,
) -> None:
    path = _blacklist_config(tmp_path)
    add_auto_excluded_subject(path, 202, name_cn="保留中文名")
    add_auto_excluded_subject(path, 303, name_original="保留原名")
    before = path.read_text(encoding="utf-8")

    assert remove_auto_excluded_subjects(path, (404, 202)) == (202,)
    content = path.read_text(encoding="utf-8")

    assert "excluded_subject_ids = [101, 404] # manual stays unchanged" in content
    assert "202," not in content
    assert "303, # 保留原名" in content
    assert load_archive_sync_settings(path).auto_excluded_subject_ids == (
        frozenset({303})
    )
    assert remove_auto_excluded_subject(path, 202) is False
    assert remove_auto_excluded_subjects(path, (202,)) == ()
    assert content != before


def test_remove_auto_excluded_subject_write_failure_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _blacklist_config(tmp_path, auto="[202, 303]")
    original = path.read_bytes()

    def fail_replace(*_: object, **__: object) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr("bgm_side_b.archive_config.os.replace", fail_replace)

    with pytest.raises(PermissionError, match="replace denied"):
        remove_auto_excluded_subject(path, 202)

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".bangumi.toml.*.tmp"))
