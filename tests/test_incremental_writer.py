"""Failure-focused tests for the single-site incremental writer."""

from __future__ import annotations

from pathlib import Path

import pytest

import bgm_side_b.build.writer as writer_module
from bgm_side_b.build.fingerprint import BuildState, read_build_state
from bgm_side_b.build.writer import (
    IncrementalSiteWriter,
    SiteRecoveryError,
    SiteWriteError,
)


def _state() -> BuildState:
    return BuildState(
        1, "shared", {"2026-04": "quarter"}, {"2026": "year"}, "archive", {}
    )


def test_writer_stages_only_changes_and_removes_stale_artifacts(tmp_path: Path) -> None:
    site = tmp_path / "dist" / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    desired = {"index.html": b"one", "data/a.json": b"{}\n"}
    result = writer.apply(desired, _state(), validate_staged=lambda _: None)
    assert set(result.written) == set(desired)
    state = read_build_state(workspace / "build-state.json")
    assert state is not None

    second = writer.apply(desired, state, validate_staged=lambda _: None)
    assert second.written == ()
    assert set(second.reused) == set(desired)

    desired = {"index.html": b"two"}
    third = writer.apply(desired, state, validate_staged=lambda _: None)
    assert third.deleted == ("data/a.json",)
    assert (site / "index.html").read_bytes() == b"two"
    assert not (site / "data/a.json").exists()


def test_validation_failure_leaves_site_and_state_unchanged(tmp_path: Path) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    state_before = (workspace / "build-state.json").read_bytes()

    def fail(_: object) -> None:
        raise ValueError("invalid staged output")

    with pytest.raises(SiteWriteError):
        writer.apply({"index.html": b"new"}, _state(), validate_staged=fail)
    assert (site / "index.html").read_bytes() == b"old"
    assert (workspace / "build-state.json").read_bytes() == state_before


def test_staging_failure_never_rolls_back_unmodified_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    state_before = (workspace / "build-state.json").read_bytes()

    def fail_staging(*_args: object, **_kwargs: object) -> None:
        raise OSError("staging disk failure")

    monkeypatch.setattr(writer, "_stage_files", fail_staging)
    with pytest.raises(SiteWriteError, match="previous output was restored"):
        writer.apply({"index.html": b"new"}, _state(), validate_staged=lambda _: None)

    assert (site / "index.html").read_bytes() == b"old"
    assert (workspace / "build-state.json").read_bytes() == state_before
    assert not tuple((workspace / "build-staging").glob("recovery-*"))


def test_replace_failure_rolls_back_touched_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    original_replace = writer_module.os.replace

    def fail_target(source: str | Path, target: str | Path) -> None:
        if str(target).endswith("index.html") and ".restore" not in str(source):
            raise PermissionError("locked")
        original_replace(source, target)

    monkeypatch.setattr(writer_module.os, "replace", fail_target)
    with pytest.raises(SiteWriteError):
        writer.apply({"index.html": b"new"}, _state(), validate_staged=lambda _: None)
    assert (site / "index.html").read_bytes() == b"old"


def test_restore_failure_invalidates_state_and_keeps_bounded_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply(
        {"index.html": b"old", "untouched.txt": b"keep"},
        _state(),
        validate_staged=lambda _: None,
    )
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    original_replace = writer_module.os.replace

    def fail_replace(source: str | Path, target: str | Path) -> None:
        if str(target).endswith("index.html"):
            raise PermissionError("locked")
        original_replace(source, target)

    monkeypatch.setattr(writer_module.os, "replace", fail_replace)
    with pytest.raises(SiteRecoveryError, match="site recovery incomplete"):
        writer.apply(
            {"index.html": b"new", "untouched.txt": b"keep"},
            previous,
            validate_staged=lambda _: None,
        )
    assert (site / "untouched.txt").read_bytes() == b"keep"
    assert not (workspace / "build-state.json").exists()
    recoveries = sorted((workspace / "build-staging").glob("recovery-*"))
    assert len(recoveries) == 1
    assert (recoveries[0] / "index.html").read_bytes() == b"old"
    assert not (recoveries[0] / "untouched.txt").exists()
    assert (recoveries[0] / "build-state.invalid.json").is_file()

    monkeypatch.setattr(writer_module.os, "replace", original_replace)
    writer.apply(
        {"index.html": b"new", "untouched.txt": b"keep"},
        _state(),
        validate_staged=lambda _: None,
    )
    assert (site / "index.html").read_bytes() == b"new"
    assert not tuple((workspace / "build-staging").glob("recovery-*"))


def test_failed_recovery_retention_never_deletes_the_only_old_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    original_replace = writer_module.os.replace
    original_copyfile = writer_module.shutil.copyfile
    target_replaced = False

    def ambiguous_replace(source: str | Path, target: str | Path) -> None:
        nonlocal target_replaced
        source_path = Path(source)
        target_path = Path(target)
        if (
            target_path == site / "index.html"
            and source_path.name == "index.html"
            and not source_path.name.endswith(".restore")
            and not target_replaced
        ):
            target_replaced = True
            original_replace(source, target)
            raise PermissionError("replace reported failure after moving the file")
        if target_path == site / "index.html" and source_path.name.endswith(
            ".restore"
        ):
            raise PermissionError("restore locked")
        original_replace(source, target)

    def fail_legacy_recovery_copy(
        source: str | Path, target: str | Path
    ) -> str:
        source_path = Path(source)
        target_path = Path(target)
        if any(part.startswith("backup-") for part in source_path.parts) and any(
            part.startswith("recovery-") for part in target_path.parts
        ):
            raise PermissionError("recovery copy locked")
        return original_copyfile(source, target)

    monkeypatch.setattr(writer_module.os, "replace", ambiguous_replace)
    monkeypatch.setattr(writer_module.shutil, "copyfile", fail_legacy_recovery_copy)
    with pytest.raises(SiteRecoveryError, match="site recovery incomplete"):
        writer.apply(
            {"index.html": b"new"},
            previous,
            validate_staged=lambda _: None,
        )

    recoveries = tuple((workspace / "build-staging").glob("recovery-*"))
    assert len(recoveries) == 1
    assert (recoveries[0] / "index.html").read_bytes() == b"old"
    assert (recoveries[0] / "build-state.invalid.json").is_file()
    assert not (workspace / "build-state.json").exists()


def test_delete_failure_with_restore_failure_is_hard_and_invalidates_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply(
        {"index.html": b"old", "stale.json": b"stale"},
        _state(),
        validate_staged=lambda _: None,
    )
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    original_unlink = Path.unlink
    original_replace = writer_module.os.replace

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "stale.json":
            raise PermissionError("locked")
        original_unlink(path, missing_ok=missing_ok)

    def fail_restore(source: str | Path, target: str | Path) -> None:
        if str(target).endswith("stale.json") and ".restore" in str(source):
            raise PermissionError("locked")
        original_replace(source, target)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    monkeypatch.setattr(writer_module.os, "replace", fail_restore)
    with pytest.raises(SiteRecoveryError, match="site recovery incomplete"):
        writer.apply(
            {"index.html": b"new"},
            previous,
            validate_staged=lambda _: None,
        )
    assert not (workspace / "build-state.json").exists()
    assert (site / "index.html").read_bytes() == b"old"


def test_state_commit_failure_restores_previous_state_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    state_before = (workspace / "build-state.json").read_bytes()

    def fail_commit(_: object) -> None:
        raise SiteWriteError("state commit failed")

    monkeypatch.setattr(writer, "_commit_state", fail_commit)
    with pytest.raises(SiteWriteError, match="state commit failed"):
        writer.apply(
            {"index.html": b"new"}, previous, validate_staged=lambda _: None
        )
    assert (site / "index.html").read_bytes() == b"old"
    assert (workspace / "build-state.json").read_bytes() == state_before


def test_ambiguous_state_replace_is_reconciled_before_output_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    original_replace = writer_module.os.replace
    state_replaced = False

    def ambiguous_replace(source: str | Path, target: str | Path) -> None:
        nonlocal state_replaced
        if Path(target) == workspace / "build-state.json" and not state_replaced:
            state_replaced = True
            original_replace(source, target)
            raise PermissionError("replace reported failure after moving the state")
        original_replace(source, target)

    monkeypatch.setattr(writer_module.os, "replace", ambiguous_replace)
    first = writer.apply(
        {"index.html": b"new"}, previous, validate_staged=lambda _: None
    )
    committed = read_build_state(workspace / "build-state.json")
    assert first.written == ("index.html",)
    assert committed is not None
    assert committed.artifacts["index.html"] == writer_module._sha256(b"new")
    assert (site / "index.html").read_bytes() == b"new"

    second = writer.apply(
        {"index.html": b"new"}, committed, validate_staged=lambda _: None
    )
    assert second.written == ()
    assert (site / "index.html").read_bytes() == b"new"


def test_ambiguous_state_that_cannot_be_invalidated_keeps_old_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    workspace = tmp_path / "workspace"
    writer = IncrementalSiteWriter(site, workspace)
    writer.apply({"index.html": b"old"}, _state(), validate_staged=lambda _: None)
    previous = read_build_state(workspace / "build-state.json")
    assert previous is not None
    original_replace = writer_module.os.replace
    original_unlink = Path.unlink

    def fail_state_replace(source: str | Path, target: str | Path) -> None:
        if Path(target) == workspace / "build-state.json":
            raise PermissionError("state locked")
        original_replace(source, target)

    def fail_state_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == workspace / "build-state.json":
            raise PermissionError("state cannot be invalidated")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(writer_module.os, "replace", fail_state_replace)
    monkeypatch.setattr(Path, "unlink", fail_state_unlink)
    with pytest.raises(SiteRecoveryError, match="could not be invalidated"):
        writer.apply(
            {"index.html": b"new"}, previous, validate_staged=lambda _: None
        )

    recoveries = tuple((workspace / "build-staging").glob("recovery-*"))
    assert len(recoveries) == 1
    assert (recoveries[0] / "index.html").read_bytes() == b"old"
    assert (site / "index.html").read_bytes() == b"new"
