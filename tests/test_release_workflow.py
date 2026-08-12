"""High-level release checks against isolated current-schema projects."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bgm_side_b.release import workflow
from bgm_side_b.release.workflow import WorkflowError
from tests.release_fixture import create_release_project, git, make_builder


def _remote_branch_exists(root: Path, remote: Path, branch: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "show-ref",
            "--verify",
            f"refs/heads/{branch}",
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _clone_pages(root: Path, remote: Path, destination: Path) -> Path:
    git(
        root.parent,
        "-c",
        "core.autocrlf=false",
        "clone",
        "-q",
        "--branch",
        "gh-pages",
        str(remote),
        str(destination),
    )
    return destination


def _assert_tree_matches(site: Path, published: Path) -> None:
    expected = sorted(
        path.relative_to(site).as_posix()
        for path in site.rglob("*")
        if path.is_file()
    )
    actual = sorted(
        path.relative_to(published).as_posix()
        for path in published.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(published).parts
    )
    assert actual == expected
    for relative in expected:
        assert (published / relative).read_bytes() == (site / relative).read_bytes()


def _commit_and_push_source_change(root: Path) -> str:
    css = root / "static" / "css" / "site.css"
    css.write_text(css.read_text("utf-8") + "\n/* release fixture change */\n", "utf-8")
    git(root, "add", "static/css/site.css")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture source change",
    )
    git(root, "push", "-q", "origin", "main")
    return git(root, "rev-parse", "HEAD")


def test_prepare_publish_replaces_exact_tree_and_increments_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")

    prepared = workflow.prepare_release(root)
    assert prepared.release_version.endswith(".1")
    assert not _remote_branch_exists(root, remote, "gh-pages")

    first = workflow.publish_prepared_release(root)
    assert first.published
    assert not (root / "workspace" / "state" / "prepared-release.json").exists()
    published = _clone_pages(root, remote, tmp_path / "published-first")
    _assert_tree_matches(root / "dist" / "site", published)
    assert not (published / "release-report.json").exists()
    source = git(root, "rev-parse", "HEAD")
    assert git(
        root, "--git-dir", str(remote), "log", "-1", "--format=%B", "gh-pages"
    ).splitlines()[0] == f"release: {first.release_version} [source {source[:12]}]"

    _commit_and_push_source_change(root)
    second_prepared = workflow.prepare_release(root)
    assert second_prepared.release_version.endswith(".2")
    second = workflow.publish_prepared_release(root)
    assert second.release_version == second_prepared.release_version
    source = git(root, "rev-parse", "HEAD")
    assert git(
        root, "--git-dir", str(remote), "log", "-1", "--format=%B", "gh-pages"
    ).splitlines()[0] == f"release: {second.release_version} [source {source[:12]}]"
    published = _clone_pages(root, remote, tmp_path / "published-second")
    _assert_tree_matches(root / "dist" / "site", published)


def test_publish_rejects_tree_mutation_before_writer_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    workflow.prepare_release(root)
    target = root / "dist" / "site" / "index.html"
    target.write_text(target.read_text("utf-8") + "\nmutation\n", "utf-8")
    with pytest.raises(WorkflowError, match="dist/site"):
        workflow.publish_prepared_release(root)


def test_publish_rejects_remote_race_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    workflow.prepare_release(root)
    first = workflow.publish_prepared_release(root)
    assert first.published
    _commit_and_push_source_change(root)
    workflow.prepare_release(root)

    racer = _clone_pages(root, remote, tmp_path / "racer")
    (racer / "race.txt").write_text("remote race\n", "utf-8")
    git(racer, "add", "race.txt")
    git(
        racer,
        "-c",
        "user.name=Racer",
        "-c",
        "user.email=racer@example.invalid",
        "commit",
        "-q",
        "-m",
        "remote race",
    )
    git(racer, "push", "-q", "origin", "HEAD:gh-pages")
    raced_commit = git(root, "--git-dir", str(remote), "rev-parse", "gh-pages")

    with pytest.raises(WorkflowError, match="prepared release"):
        workflow.publish_prepared_release(root)
    assert git(root, "--git-dir", str(remote), "rev-parse", "gh-pages") == raced_commit
    assert (racer / "race.txt").read_text("utf-8") == "remote race\n"


def test_prepare_builds_from_current_schema_fixture(tmp_path: Path) -> None:
    root, _ = create_release_project(tmp_path)
    builder = make_builder(root)
    run = builder.build()
    assert run.patch.written
    assert (root / "dist" / "site" / "2026-07" / "index.html").is_file()


def test_confirmed_publish_survives_prepared_state_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    workflow.prepare_release(root)
    prepared = root / "workspace" / "state" / "prepared-release.json"
    original_unlink = Path.unlink

    def fail_prepared_unlink(
        path: Path, missing_ok: bool = False
    ) -> None:
        if path.resolve() == prepared.resolve():
            raise PermissionError("private cleanup detail")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_prepared_unlink)
    run = workflow.publish_prepared_release(root)
    assert run.published
    assert prepared.is_file()
    assert run.remote_commit == git(
        root, "--git-dir", str(remote), "rev-parse", "gh-pages"
    )
    assert run.warnings == (
        "remote published but local prepared state cleanup failed",
    )
    warning = run.warnings[0]
    assert "Traceback" not in warning
    assert str(root) not in warning
    assert "http" not in warning

    with pytest.raises(WorkflowError, match="prepared release"):
        workflow.publish_prepared_release(root)
