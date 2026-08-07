"""Release readiness and prepared-release orchestration coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b import __version__
from bgm_side_b.build.builder import ArchiveBuilder
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.release import workflow
from bgm_side_b.release.candidate import (
    advance_data_generation,
    mark_data_generation_dirty,
)
from bgm_side_b.release.workflow import (
    WorkflowError,
    doctor,
    local_status,
    prepare_release,
    publish_prepared_release,
)
from bgm_side_b.repository import (
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)

ROOT = Path(__file__).parents[1]


@pytest.fixture
def workflow_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    for name in ("config", "static", "templates"):
        shutil.copytree(ROOT / name, root / name)
    (root / "CHANGELOG.md").write_text(
        "## Unreleased\n\n### Changes\n\n- \u53d1布流程测试\n", "utf-8"
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0'\n", "utf-8"
    )
    (root / ".gitignore").write_text("workspace/\ndist/\n", "utf-8")
    _git(root, "init")
    _git(root, "checkout", "-b", "main")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        repository.upsert_subject(
            connection,
            SubjectRecord(101, "tv", None, date(2026, 4, 1), 12, 7.0, 1),
        )
        repository.replace_titles(
            connection, 101, [SubjectTitle("preferred", "发布流程测试")]
        )
        repository.replace_infobox(
            connection, 101, [SubjectInfoboxItem("国家/地区", "Japan")]
        )
        repository.replace_quarters(connection, 101, [SubjectQuarter(2026, 4, "new")])
    settings, tags, sources = load_rules(root / "config")
    ArchiveBuilder(root, database, settings, tags, sources).build(None, target="pages")
    return root, remote


def test_local_status_reports_the_fresh_candidate_and_one_next_step(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root

    status = local_status(root)

    assert status.source_version == __version__
    assert status.worktree_clean
    assert status.data_status == "clean"
    assert status.pages_build == "fresh"
    assert status.pages_candidate == "OK"
    assert status.prepared_release_status == "none"
    assert status.prepared_release_version is None
    assert status.next_step() == "bgmb release prepare"
    assert "下一步：\nbgmb release prepare" in status.render_status()


def test_status_prioritises_pending_promotion_and_dirty_worktree(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    pending = root / "workspace" / "state" / "pending-promotion.json"
    pending.write_text(
        json.dumps(
            {
                "schema": 1,
                "profile": "pages",
                "source_commit": "commit",
                "app_version": __version__,
                "data_generation": 0,
                "tree_hash": "hash",
                "created_at": "2026-08-07T00:00:00Z",
                "relative_stage_path": ".staging/pages-verified-test",
            }
        ),
        "utf-8",
    )

    assert local_status(root).next_step() == "bgmb promote pages"
    (root / "uncommitted.txt").write_text("dirty", "utf-8")
    assert local_status(root).next_step() == "git status"


def test_doctor_local_is_read_only_and_warns_about_stale_package_metadata(
    workflow_root: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = workflow_root
    monkeypatch.setattr(workflow, "distribution_version", lambda _: "0.1.2")

    result = doctor(root, local_only=True)

    assert result.audit.passed
    assert result.origin_main == "未检查"
    assert result.gh_pages == "未检查"
    assert "包元数据版本     0.1.2（与源码不一致）" in result.render()
    assert "本地检查完成" in result.conclusion()


def test_doctor_remote_refreshes_only_git_remote_state(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    _git(root, "push", "-u", "origin", "main")

    result = doctor(root)

    assert result.origin_main == "synchronized"
    assert result.gh_pages == "reachable"


def test_stale_head_marks_the_pages_build_stale(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    (root / "CHANGELOG.md").write_text("## 未发布\n\n- 新改动\n", "utf-8")
    _git(root, "add", "CHANGELOG.md")
    _commit(root, "change")

    status = local_status(root)

    assert status.worktree_clean
    assert status.pages_build == "stale"
    assert status.pages_candidate == "stale"


def test_prepare_writes_a_project_relative_bound_state(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root

    prepared = prepare_release(root)

    payload = json.loads(prepared.state_path.read_text("utf-8"))
    assert payload["schema"] == 1
    assert payload["app_version"] == __version__
    assert payload["source_commit"] == _git(root, "rev-parse", "HEAD")
    assert payload["dry_run_report"].startswith("workspace/reports/")
    assert not Path(payload["dry_run_report"]).is_absolute()
    assert prepared.report_path.is_file()


def test_status_reports_a_locally_valid_prepared_release(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    prepared = prepare_release(root)

    status = local_status(root)

    assert status.prepared_release_status == "valid_local"
    assert status.prepared_release_version == prepared.release_version
    assert status.next_step() == "确认 main 已 push 后运行：\nbgmb release publish"
    assert "本地有效" in status.render_status()


@pytest.mark.parametrize("changed", ("head", "generation", "version", "candidate"))
def test_status_marks_changed_prepared_bindings_stale(
    workflow_root: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    root, _ = workflow_root
    prepare_release(root)
    if changed == "head":
        _commit(root, "advance head", allow_empty=True)
    elif changed == "generation":
        advance_data_generation(root / "workspace")
    elif changed == "version":
        monkeypatch.setattr(workflow, "__version__", "99.0.0")
    else:
        page = root / "dist" / "pages" / "index.html"
        page.write_text(page.read_text("utf-8") + "\n", "utf-8")

    status = local_status(root)

    assert status.prepared_release_status == "stale"
    assert status.next_step() == "bgmb release prepare"


def test_status_marks_a_malformed_prepared_state_invalid(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    state = root / "workspace" / "state" / "prepared-release.json"
    state.write_text("{}", "utf-8")

    status = local_status(root)

    assert status.prepared_release_status == "invalid"
    assert "无效 prepared state" in status.next_step()


def test_doctor_marks_a_synchronized_prepared_release_publishable(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    _git(root, "push", "-u", "origin", "main")
    prepare_release(root)

    result = doctor(root)

    assert result.origin_main == "synchronized"
    assert result.prepared_release_status == "publishable"
    assert "bgmb release publish" in result.conclusion()


def test_doctor_tells_an_ahead_prepared_release_to_push_main_first(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    _git(root, "push", "-u", "origin", "main")
    _commit(root, "unpublished release", allow_empty=True)
    prepare_release(root)

    result = doctor(root)

    assert result.origin_main == "ahead"
    assert result.prepared_release_status == "valid_local"
    assert "git push origin main" in result.conclusion()


def test_prepare_refuses_a_dirty_data_generation(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    mark_data_generation_dirty(root / "workspace")

    with pytest.raises(WorkflowError, match="资料状态不是 clean"):
        prepare_release(root)


@pytest.mark.parametrize("changed", ("head", "generation", "version", "candidate"))
def test_publish_refuses_every_local_prepared_state_invalidation(
    workflow_root: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    root, _ = workflow_root
    prepare_release(root)
    if changed == "head":
        _commit(root, "advance head", allow_empty=True)
    elif changed == "generation":
        advance_data_generation(root / "workspace")
    elif changed == "version":
        monkeypatch.setattr(workflow, "__version__", "99.0.0")
    else:
        page = root / "dist" / "pages" / "index.html"
        page.write_text(page.read_text("utf-8") + "\n", "utf-8")

    with pytest.raises(WorkflowError, match="prepared release 已失效"):
        publish_prepared_release(root)


def test_publish_requires_origin_main_before_any_real_publish(
    workflow_root: tuple[Path, Path],
) -> None:
    root, _ = workflow_root
    prepare_release(root)

    with pytest.raises(WorkflowError, match="prepared release 已失效"):
        publish_prepared_release(root)


def test_publish_refuses_when_gh_pages_changed_after_prepare(
    workflow_root: tuple[Path, Path], tmp_path: Path
) -> None:
    root, remote = workflow_root
    _git(root, "push", "-u", "origin", "main")
    prepare_release(root)
    pages = tmp_path / "pages"
    pages.mkdir()
    _git(pages, "init")
    (pages / "index.html").write_text("other release", "utf-8")
    _git(pages, "add", ".")
    _commit(pages, "other release")
    _git(pages, "remote", "add", "origin", str(remote))
    _git(pages, "push", "origin", "HEAD:gh-pages")

    result = doctor(root)
    assert result.prepared_release_status == "stale"
    assert "prepared release 已失效" in result.conclusion()

    with pytest.raises(WorkflowError, match="prepared release 已失效"):
        publish_prepared_release(root)


def _commit(root: Path, message: str, *, allow_empty: bool = False) -> None:
    command = [
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        message,
    ]
    if allow_empty:
        command.insert(-2, "--allow-empty")
    _git(root, *command)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()
