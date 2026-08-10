"""Failure-focused tests for the single-site incremental writer."""

from __future__ import annotations

from pathlib import Path

import pytest

import bgm_side_b.build.writer as writer_module
from bgm_side_b.build.fingerprint import BuildState, read_build_state
from bgm_side_b.build.writer import IncrementalSiteWriter, SiteWriteError


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
