"""Risk-focused tests for deterministic release metadata."""

from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import pytest

from bgm_side_b.release.history import (
    change_kind_display,
    next_release_version,
    unreleased_changes,
)
from bgm_side_b.release.manifest import (
    ManifestError,
    build_snapshot_manifest,
    candidate_content_hash,
    index_candidate,
    manifest_json,
    validate_manifest_payload,
)
from bgm_side_b.release.publish import (
    PublishError,
    _change_kind,
    _change_kind_label,
    _change_lines,
    _data_changed,
    _system_changes,
    _validate_public_system_summary,
)
from bgm_side_b.release.snapshot import diff_snapshots
from bgm_side_b.release.validation import validate_release_payload

ROOT = Path(__file__).parents[1]


def test_candidate_index_is_stable_and_excludes_control_files(tmp_path: Path) -> None:
    candidate = tmp_path / "pages"
    (candidate / "assets").mkdir(parents=True)
    (candidate / "quarters" / "2022-01").mkdir(parents=True)
    (candidate / "assets" / "site.abc.js").write_text("console.log(1)", "utf-8")
    (candidate / "assets" / ".gitkeep").write_text("", "utf-8")
    (candidate / ".nojekyll").write_text("", "utf-8")
    (candidate / ".DS_Store").write_text("metadata", "utf-8")
    (candidate / "Thumbs.db").write_text("metadata", "utf-8")
    (candidate / "quarters" / "2022-01" / "index.html").write_text("ok", "utf-8")
    (candidate / "release.json").write_text("{}", "utf-8")
    entries = index_candidate(candidate, "/bangumi-side-b/")
    assert [entry.url for entry in entries] == [
        "/bangumi-side-b/assets/site.abc.js",
        "/bangumi-side-b/quarters/2022-01/index.html",
    ]
    assert all(entry.url != "/bangumi-side-b/release.json" for entry in entries)
    assert all(
        ".gitkeep" not in entry.url and ".nojekyll" not in entry.url
        for entry in entries
    )
    assert candidate_content_hash(entries) == candidate_content_hash(
        tuple(reversed(entries))
    )


def test_manifest_avoids_self_hash_and_validates_counts(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("ok", "utf-8")
    entries = index_candidate(tmp_path, "/site/")
    manifest = build_snapshot_manifest(
        entries,
        release_version="2026.07.30.1",
        app_version="0.1.0",
        deployment_path="/site/",
        generated_at=datetime(2026, 7, 30, tzinfo=UTC),
    )
    payload = json.loads(manifest_json(manifest))
    assert payload["entry_count"] == 1
    assert validate_manifest_payload(payload)["content_hash"] == manifest.content_hash
    payload["total_bytes"] = 0
    with pytest.raises(ManifestError, match="byte count"):
        validate_manifest_payload(payload)


def test_release_versions_changelog_and_snapshot_changes_are_explicit(
    tmp_path: Path,
) -> None:
    assert (
        next_release_version("2026.07.30.1", now=datetime(2026, 7, 30, tzinfo=UTC))
        == "2026.07.30.2"
    )
    assert (
        next_release_version("2026.07.29.9", now=datetime(2026, 7, 30, tzinfo=UTC))
        == "2026.07.30.1"
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## 尚未发布\n\n### 修复\n\n- PWA 快照校验\n\n## 0.1.0\n", "utf-8"
    )
    assert unreleased_changes(changelog) == ("PWA 快照校验",)
    current = {
        "schema": 1,
        "quarters": [{"key": "2022-01"}],
        "subjects": {"1": {"episode_count": 2, "role_count": 1, "person_count": 1}},
        "covers": {"1": "cover"},
        "rules_hash": "rules",
        "blacklist_hash": "blacklist",
    }
    initial = diff_snapshots(None, current)
    assert initial["kind"] == "initial_snapshot"
    updated = diff_snapshots(current, {**current, "rules_hash": "new-rules"})
    assert updated["rules_changed"]


def test_public_release_summaries_use_chinese_text() -> None:
    _validate_public_system_summary(("PWA 快照校验", "Service Worker 控制器交接"))
    with pytest.raises(PublishError, match="Chinese public wording"):
        _validate_public_system_summary(("Release flow repairs first installation",))

    changes = {
        "kind": "data",
        "subjects_added": 3,
        "subjects_removed": 1,
        "subjects_updated": 12,
        "episodes_added": 20,
        "episodes_removed": 3,
        "episodes_updated": 15,
        "covers_changed": 2,
    }
    assert _change_kind(changes, ("PWA 快照校验",)) == "system_and_data"
    assert _change_kind_label("system_and_data") == "系统与资料均有变化"
    assert _change_lines(changes) == [
        "新增作品 3 部",
        "移除作品 1 部",
        "更新作品 12 部",
        "章节变化 38 条",
        "封面变化 2 张",
    ]
    assert _change_lines({"kind": "initial_snapshot"}) == ["首次发布完整资料快照"]


def test_change_kind_display_keeps_legacy_and_unknown_history_readable() -> None:
    assert change_kind_display("both") == "系统与资料均有变化"
    assert change_kind_display("system_and_data") == "系统与资料均有变化"
    assert change_kind_display("系统与资料均有变化") == "系统与资料均有变化"
    assert change_kind_display("future_kind") == "future_kind"


def test_current_changelog_reads_only_the_unreleased_section() -> None:
    changes = unreleased_changes(ROOT / "CHANGELOG.md")

    assert (
        "Windows 构建预检在输出恢复失败时保留完整 recovery tree，避免丢失唯一旧输出。"
        in changes
    )
    assert not any("首次初始化完成后" in item for item in changes)
    assert not any("新增 `bgmb doctor`" in item for item in changes)


def test_release_change_kinds_only_count_structured_data_facts() -> None:
    unchanged = {
        "kind": "data",
        "rules_changed": True,
        "blacklist_changed": True,
        "failure_summary": [],
    }
    assert not _data_changed(unchanged)
    assert _change_kind(unchanged, ()) == "none"
    assert _change_kind(unchanged, ("程序版本 0.1.2 → 0.1.3",)) == "system"
    changed = {**unchanged, "covers_changed": 1}
    assert _change_kind(changed, ()) == "data"
    assert _change_kind({"kind": "initial_snapshot"}, ()) == "initial"


def test_release_payload_rejects_contradictory_change_summaries() -> None:
    payload = _release_payload("data", system=[], data=["资料无结构化变化"])
    with pytest.raises(ManifestError, match="conflicts"):
        validate_release_payload(payload)
    payload = _release_payload("system", system=[], data=["资料无结构化变化"])
    with pytest.raises(ManifestError, match="conflicts"):
        validate_release_payload(payload)
    payload = _release_payload(
        "system_and_data",
        system=["程序版本 0.1.2 → 0.1.3"],
        data=["资料无结构化变化"],
    )
    with pytest.raises(ManifestError, match="conflicts"):
        validate_release_payload(payload)
    assert validate_release_payload(
        _release_payload("none", system=[], data=["资料无结构化变化"])
    )["change_kind"] == "none"


def test_project_version_is_sourced_from_the_version_module() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "bgm_side_b._version.__version__"
    }
    assert (ROOT / "src" / "bgm_side_b" / "_version.py").read_text("utf-8").count(
        '"0.1.3"'
    ) == 1


def test_source_version_ignores_stale_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metadata, "version", lambda _: "0.1.2")

    from bgm_side_b import __version__

    assert __version__ == "0.1.3"


def test_system_changes_include_an_app_version_change_without_changelog() -> None:
    assert _system_changes(
        {"app_version": "0.1.2", "system_changelog_hash": "unchanged"}, ()
    ) == ("程序版本 0.1.2 → 0.1.3",)


def _release_payload(
    change_kind: str, *, system: list[str], data: list[str]
) -> dict[str, object]:
    return {
        "schema": 1,
        "release_version": "2026.08.07.1",
        "app_version": "0.1.3",
        "generated_at": "2026-08-07T00:00:00Z",
        "published_at": "2026-08-07T00:00:00Z",
        "quarter_count": 1,
        "subject_count": 1,
        "total_bytes": 3,
        "content_hash": "a" * 64,
        "manifest_url": "snapshot-manifest.json",
        "manifest_sha256": "b" * 64,
        "change_kind": change_kind,
        "summary": {"system": system, "data": data},
    }


def test_public_readme_and_changelog_use_chinese_project_information() -> None:
    readme = (ROOT / "README.md").read_text("utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    for heading in (
        "项目简介",
        "当前收录范围",
        "PWA 与离线使用",
        "数据与版权说明",
    ):
        assert heading in readme
    assert "77 部作品" in readme
    assert "角色、声优、角色图片或声优图片" in readme
    assert "# 更新日志" in changelog
    assert "## 尚未发布" in changelog
    assert "Release 0.1.1 repairs" not in changelog
    _validate_public_system_summary(unreleased_changes(ROOT / "CHANGELOG.md"))


def test_release_payload_requires_small_control_file_schema() -> None:
    payload = {
        "schema": 1,
        "release_version": "2026.07.30.1",
        "app_version": "0.1.0",
        "generated_at": "2026-07-30T00:00:00Z",
        "published_at": "2026-07-30T00:00:00Z",
        "quarter_count": 1,
        "subject_count": 2,
        "total_bytes": 3,
        "content_hash": "a" * 64,
        "manifest_url": "snapshot-manifest.json",
        "manifest_sha256": "b" * 64,
        "change_kind": "data",
        "summary": {"system": [], "data": ["新增作品 1 部"]},
    }
    assert validate_release_payload(payload) == payload
    payload["manifest_url"] = "https://invalid.example/manifest"
    with pytest.raises(ManifestError, match="manifest URL"):
        validate_release_payload(payload)
