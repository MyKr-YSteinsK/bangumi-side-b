"""Tests for deterministic configuration loading."""

from pathlib import Path

import pytest

from bgm_side_b.config import load_project_settings, load_rules

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_configuration_loads() -> None:
    settings, tag_rules, source_rules = load_rules(ROOT / "config")

    assert settings.excluded_subject_ids == frozenset()
    assert settings.sync.api_concurrency == 3
    assert settings.scope.release_quarters == ("2026-04",)
    assert settings.scope.formats == ("tv",)
    assert settings.scope.include_continuations is False
    assert settings.country_filter.country_keys == frozenset(
        {"制片国家/地区", "国家/地区"}
    )
    assert settings.country_filter.country_value_aliases == frozenset({"日本", "Japan"})
    assert settings.country_filter.positive_tags == frozenset({"日本", "日本动画"})
    assert settings.country_filter.negative_tags == frozenset(
        {"国产", "中国", "中国动画", "欧美", "欧美动画", "美国", "韩国"}
    )
    assert settings.country_filter.allow_tv_default_without_country is True
    assert settings.main_character_relations == frozenset({"主角"})
    assert settings.end_date_infobox_keys == frozenset({"播放结束"})
    assert settings.chinese_name_infobox_keys == frozenset({"简体中文名"})
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


@pytest.mark.parametrize(
    "scope",
    [
        "[scope]\nrelease_quarters = [\"2026-02\"]\n"
        "formats = [\"tv\"]\ninclude_continuations = false",
        "[scope]\nrelease_quarters = [\"2026-04\"]\n"
        "formats = [\"movie\"]\ninclude_continuations = false",
        "[scope]\nrelease_quarters = [\"2026-04\"]\n"
        "formats = [\"tv\"]\ninclude_continuations = true",
    ],
)
def test_reduced_scope_configuration_rejects_invalid_values(
    tmp_path: Path, scope: str
) -> None:
    path = tmp_path / "bangumi.toml"
    path.write_text(
        """[filters]
excluded_subject_ids = []
[sync]
api_concurrency = 1
request_timeout_seconds = 1
max_retries = 0
"""
        + scope
        + """
[country_filter]
required_country = "日本"
country_keys = ["制片国家/地区"]
country_value_aliases = ["日本", "Japan"]
[roles]
main_character_relations = ["主角"]
[infobox]
end_date_keys = ["播放结束"]
chinese_name_keys = ["简体中文名"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_project_settings(path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('positive_tags = ["日本", "日本动画"]', 'positive_tags = []'),
        ('positive_tags = ["日本", "日本动画"]', 'positive_tags = ["日本", "日本"]'),
        ('positive_tags = ["日本", "日本动画"]', 'positive_tags = ["日本", "中国"]'),
        (
            "allow_tv_default_without_country = true",
            "allow_tv_default_without_country = false",
        ),
    ],
)
def test_country_filter_rejects_invalid_region_tag_configuration(
    tmp_path: Path, old: str, new: str
) -> None:
    content = (ROOT / "config" / "bangumi.toml").read_text(encoding="utf-8")
    path = tmp_path / "bangumi.toml"
    path.write_text(content.replace(old, new), encoding="utf-8")

    with pytest.raises(ValueError):
        load_project_settings(path)
