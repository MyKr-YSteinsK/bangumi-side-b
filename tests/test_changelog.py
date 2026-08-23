"""Focused tests for the build-time CHANGELOG subset."""

from __future__ import annotations

from pathlib import Path

import pytest

from bgm_side_b.build.changelog import (
    ChangelogError,
    group_releases_for_settings,
    load_changelog,
    parse_changelog,
)


def test_changelog_model_preserves_releases_groups_and_utf8_order() -> None:
    document = parse_changelog(
        """# 更新日志

说明段落

## 0.4.0 - 2026-08-21

### 新增

- 第一项

## 0.1.3 - 2026-08-07

历史版本日期

## 0.1.2 - 2026-08-01

历史版本日期

## 0.1.1 - 2026-08-01

历史版本日期

## 0.1.0 - 2026-07-29

旧版本短段落
"""
    )

    assert document.title == "更新日志"
    assert document.preamble == ("说明段落",)
    assert [release.heading for release in document.releases] == [
        "0.4.0 - 2026-08-21",
        "0.1.3 - 2026-08-07",
        "0.1.2 - 2026-08-01",
        "0.1.1 - 2026-08-01",
        "0.1.0 - 2026-07-29",
    ]
    assert document.release_for_version("0.4.0").date == "2026-08-21"
    assert document.release_for_version("0.1.3").date == "2026-08-07"
    assert document.release_for_version("0.1.2").date == "2026-08-01"
    assert document.release_for_version("0.1.1").date == "2026-08-01"
    assert document.release_for_version("0.1.0").date == "2026-07-29"
    old_item = document.release_for_version("0.1.0").blocks[0]
    assert old_item.kind == "paragraph"
    assert old_item.text == "旧版本短段落"


def test_changelog_joins_indented_bullet_continuations() -> None:
    document = parse_changelog(
        """# Changelog

## 1.0.0 - 2027-01-01

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


def test_changelog_rejects_unreleased_sections() -> None:
    with pytest.raises(ChangelogError, match="unreleased sections are not supported"):
        parse_changelog("# Changelog\n\n## 尚未发布\n\n- item\n")


def test_changelog_rejects_duplicate_concrete_versions() -> None:
    with pytest.raises(ChangelogError, match="duplicate release heading"):
        parse_changelog(
            "# Changelog\n\n## 0.6.0\n\n- first\n\n## 0.6.0\n\n- duplicate\n"
        )


def test_new_concrete_releases_require_a_date() -> None:
    with pytest.raises(ChangelogError, match="requires a YYYY-MM-DD date"):
        parse_changelog("# Changelog\n\n## 0.6.1\n\n- missing date\n")


def test_settings_grouping_keeps_new_patches_standalone_until_next_milestone() -> None:
    document = parse_changelog(
        """# Changelog

## 0.7.0 - 2027-01-01
- next milestone
## 0.6.3 - 2026-08-23
- patch three
## 0.6.2 - 2026-08-23
- patch two
## 0.6.1 - 2026-08-23
- patch one
## 0.6.0 - 2026-08-23
- milestone
## 0.5.1 - 2026-08-22
- old patch
## 0.5.0 - 2026-08-22
- old milestone
"""
    )
    groups = group_releases_for_settings(document)
    assert groups.standalone == ()
    grouped = [
        (group.label, group.date, [release.version for release in group.releases])
        for group in groups.milestones
    ]
    assert grouped == [
        ("0.7", "2027-01-01", ["0.7.0", "0.6.3", "0.6.2", "0.6.1"]),
        ("0.6", "2026-08-23", ["0.6.0", "0.5.1"]),
        ("0.5", "2026-08-22", ["0.5.0"]),
    ]

    without_next = parse_changelog(
        """# Changelog

## 0.6.3 - 2026-08-23
- patch three
## 0.6.2 - 2026-08-23
- patch two
## 0.6.1 - 2026-08-23
- patch one
## 0.6.0 - 2026-08-23
- milestone
"""
    )
    groups = group_releases_for_settings(without_next)
    assert [release.version for release in groups.standalone] == [
        "0.6.3",
        "0.6.2",
        "0.6.1",
    ]
    assert [release.version for release in groups.milestones[0].releases] == ["0.6.0"]


def test_settings_grouping_handles_major_jumps() -> None:
    document = parse_changelog(
        """# Changelog

## 1.0.0 - 2027-01-01
- major
## 0.6.2 - 2026-08-23
- patch two
## 0.6.1 - 2026-08-23
- patch one
## 0.6.0 - 2026-08-23
- milestone
"""
    )
    groups = group_releases_for_settings(document)
    grouped = [
        (group.label, group.date, [release.version for release in group.releases])
        for group in groups.milestones
    ]
    assert grouped == [
        ("1.0", "2027-01-01", ["1.0.0", "0.6.2", "0.6.1"]),
        ("0.6", "2026-08-23", ["0.6.0"]),
    ]


def test_repository_milestone_dates_use_concrete_zero_anchors() -> None:
    document = load_changelog(Path(__file__).parents[1] / "CHANGELOG.md")
    groups = group_releases_for_settings(document)
    assert {group.label: group.date for group in groups.milestones} == {
        "0.6": "2026-08-23",
        "0.5": "2026-08-22",
        "0.4": "2026-08-21",
        "0.3": "2026-08-19",
        "0.2": "2026-08-13",
        "0.1": "2026-07-29",
    }
