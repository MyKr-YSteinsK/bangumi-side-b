"""Focused tests for the build-time CHANGELOG subset."""

from __future__ import annotations

import pytest

from bgm_side_b.build.changelog import ChangelogError, parse_changelog


def test_changelog_model_preserves_releases_groups_and_utf8_order() -> None:
    document = parse_changelog(
        """# 更新日志

说明段落

## 尚未发布

### 修复

- <b>不执行</b>
- 中文条目

## 0.2.0 - 2026-08-13

### 新增

- 第一项

## 0.1.0

旧版本短段落
"""
    )

    assert document.title == "更新日志"
    assert document.preamble == ("说明段落",)
    assert [release.heading for release in document.releases] == [
        "尚未发布",
        "0.2.0 - 2026-08-13",
        "0.1.0",
    ]
    assert document.unreleased is document.releases[0]
    assert document.release_for_version("0.2.0").date == "2026-08-13"
    assert document.release_for_version("0.1.0").date is None
    section = document.releases[0].blocks[0]
    assert section.title == "修复"
    assert [item.text for item in section.items] == ["<b>不执行</b>", "中文条目"]
    old_item = document.releases[2].blocks[0]
    assert old_item.kind == "paragraph"
    assert old_item.text == "旧版本短段落"


def test_changelog_joins_indented_bullet_continuations() -> None:
    document = parse_changelog(
        """# Changelog

## 1.0.0

### Added

- long item
  continues on the next line
"""
    )

    item = document.releases[0].blocks[0].items[0]
    assert item.text == "long item continues on the next line"


@pytest.mark.parametrize(
    "heading",
    ("0.2", "0.2.0 (2026-08-13)", "0.2.0 - 2026/08/13", "future release"),
)
def test_changelog_rejects_malformed_release_headings(heading: str) -> None:
    with pytest.raises(ChangelogError, match="malformed release heading"):
        parse_changelog(f"# Changelog\n\n## {heading}\n\n- item\n")


def test_changelog_requires_a_release_section() -> None:
    with pytest.raises(ChangelogError, match="no release sections"):
        parse_changelog("# Changelog\n\nOnly an introduction.\n")
