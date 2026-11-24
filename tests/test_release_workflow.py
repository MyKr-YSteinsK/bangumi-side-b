"""High-level release checks against isolated current-schema projects."""

from __future__ import annotations

import json
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
    assert json.loads(prepared.state_path.read_text("utf-8"))["app_version"] == (
        workflow.__version__
    )
    assert json.loads(prepared.report_path.read_text("utf-8"))["app_version"] == (
        workflow.__version__
    )
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


@pytest.mark.parametrize("mutation", ["same-length", "added-file", "missing-file"])
def test_publish_rejects_tree_mutation_before_writer_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    prepared = workflow.prepare_release(root)
    before = prepared.state_path.read_bytes()
    site = root / "dist/site"
    target = site / "index.html"
    if mutation == "same-length":
        content = target.read_bytes()
        target.write_bytes(content[:-1] + (b"X" if content[-1:] != b"X" else b"Y"))
        # The fast status only compares selected file sizes.
        assert workflow.local_status(root).site_status == "valid"
    elif mutation == "added-file":
        (site / "extra.txt").write_bytes(b"fixture")
    else:
        (site / "icons/pwa-192.png").unlink()
    with pytest.raises(WorkflowError, match="dist/site"):
        workflow.publish_prepared_release(root)
    assert prepared.state_path.read_bytes() == before
    assert not _remote_branch_exists(root, remote, "gh-pages")


def test_publish_rejects_prepared_state_from_previous_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, remote = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    prepared = workflow.prepare_release(root)
    saved = json.loads(prepared.state_path.read_bytes())
    candidate = workflow.validate_site(
        root / "dist/site", source_commit=git(root, "rev-parse", "HEAD")
    )
    assert saved["source_commit"] == candidate.identity.source_commit
    assert saved["candidate_content_hash"] == candidate.identity.content_hash
    assert saved["artifact_count"] == candidate.identity.artifact_count
    assert saved["total_bytes"] == candidate.identity.total_bytes
    assert saved["public_quarters"] == list(candidate.public_quarters)
    assert saved["remote_gh_pages_commit"] is None
    _commit_and_push_source_change(root)

    with pytest.raises(WorkflowError, match="prepared release"):
        workflow.publish_prepared_release(root)

    assert json.loads(prepared.state_path.read_bytes()) == saved
    assert not _remote_branch_exists(root, remote, "gh-pages")


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


def test_status_uses_metadata_without_full_integrity_or_site_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = create_release_project(tmp_path)
    make_builder(root).build()

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("full validation must not run during status")

    monkeypatch.setattr(workflow.Database, "verify_integrity", unexpected)
    monkeypatch.setattr(workflow.UnifiedReleaseAuditor, "audit", unexpected)
    monkeypatch.setattr(workflow, "validate_site", unexpected)

    status = workflow.local_status(root)
    assert status.sqlite_status == "OK"
    assert status.site_status == "valid"
    assert status.site_candidate_hash is not None


def test_doctor_reuses_one_full_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = create_release_project(tmp_path)
    make_builder(root).build()
    audit_calls = 0
    native_audit = workflow.UnifiedReleaseAuditor.audit

    def counted_audit(auditor: workflow.UnifiedReleaseAuditor):
        nonlocal audit_calls
        audit_calls += 1
        return native_audit(auditor)

    monkeypatch.setattr(workflow.UnifiedReleaseAuditor, "audit", counted_audit)
    result = workflow.doctor(root, local_only=True)

    assert result.audit.passed
    assert audit_calls == 1


def test_release_prepare_retains_full_database_and_site_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    counts = {"integrity": 0, "candidate": 0}
    native_integrity = workflow.Database.verify_integrity
    native_site = workflow.validate_site

    def counted_integrity(database: workflow.Database) -> None:
        counts["integrity"] += 1
        native_integrity(database)

    def counted_site(
        site: Path, *, source_commit: str = "", workspace: Path | None = None
    ):
        assert workspace == root / "workspace"
        counts["candidate"] += 1
        return native_site(site, source_commit=source_commit, workspace=workspace)

    monkeypatch.setattr(workflow.Database, "verify_integrity", counted_integrity)
    monkeypatch.setattr(workflow, "validate_site", counted_site)
    workflow.prepare_release(root)

    assert counts["integrity"] == 1
    assert counts["candidate"] >= 1


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
