"""Generated resource closure across updates and release candidates."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from bgm_side_b.build.site_builder import BuildError, UnifiedSiteBuilder
from bgm_side_b.build.site_paths import app_asset_identity, quarter_paths
from bgm_side_b.database import Database
from bgm_side_b.domain import MediaFormat, Quarter
from bgm_side_b.release import workflow
from bgm_side_b.release.site_candidate import (
    SiteCandidateError,
    validate_build_state,
    validate_site,
)
from bgm_side_b.repository import QuarterSyncState, ReviewIssue, SubjectRepository
from tests.release_fixture import create_release_project, git, make_builder
from tests.test_release_workflow import _commit_and_push_source_change
from tests.test_site_builder import ROOT, _build_fixture, _subject


def _tree(site: Path) -> dict[str, str]:
    return {
        path.relative_to(site).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in site.rglob("*")
        if path.is_file()
    }


def _package(site: Path, label: str) -> dict:
    return json.loads((site / quarter_paths(label)[2]).read_bytes())


def _css(package: dict) -> str:
    return next(
        item["url"]
        for item in package["resources"]
        if (identity := app_asset_identity(item["url"]))
        and identity[0] == "assets/app.css"
    )


def _block(repo: SubjectRepository, label: Quarter) -> QuarterSyncState:
    state = repo.get_sync_state(label)
    assert state is not None
    with repo.transaction() as connection:
        repo.write_sync_state(
            connection,
            replace(state, facts_status="incomplete", covers_status="incomplete"),
        )
    return state


def _alternate_build(
    builder: UnifiedSiteBuilder, target: Path, *, previous: bool
) -> dict[str, str]:
    workspace = target / "workspace"
    shutil.copytree(builder.workspace_directory / "covers", workspace / "covers")
    site = target / "site"
    if previous:
        shutil.copytree(builder.site_directory, site)
        shutil.copyfile(builder.state_path, workspace / "build-state.json")
    alternate = UnifiedSiteBuilder(
        builder.root,
        builder.database,
        builder.tag_rules,
        workspace_directory=workspace,
        site_directory=site,
        changelog_path=builder.changelog_path,
    )
    alternate.build()
    validate_site(site, workspace=workspace)
    return _tree(site)


def test_three_generations_keep_only_referenced_assets(tmp_path: Path) -> None:
    first_root = tmp_path / "main"
    builder, database = _build_fixture(first_root)
    builder.root = first_root / "frontend"
    shutil.copytree(ROOT / "static", builder.root / "static")
    css = builder.root / "static/css/site.css"
    css.write_bytes(css.read_bytes() + b"\n/* generation A */\n")
    repository = SubjectRepository(database)
    october = Quarter(2026, 10)
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(
            connection, _subject(404, MediaFormat.TV, october)
        )
        repository.write_sync_state(
            connection,
            QuarterSyncState(
                october, "complete", "complete", 1, 1, "2026-10-01", "2026-10-01"
            ),
        )

    builder.build()
    site = builder.site_directory
    april_initial = _package(site, "2026-04")
    april_state = _block(repository, Quarter(2026, 4))
    css.write_bytes(css.read_bytes().replace(b"generation A", b"generation B"))
    builder.build()
    july_initial = _package(site, "2026-07")
    assert _package(site, "2026-04") == april_initial
    assert _css(april_initial) != _css(july_initial)
    assert (site / _css(april_initial)).stat().st_size == (
        site / _css(july_initial)
    ).stat().st_size
    july_state = _block(repository, Quarter(2026, 7))
    css.write_bytes(css.read_bytes().replace(b"generation B", b"generation C"))
    for relative in ("static/js/app.js", "static/js/pwa.js"):
        path = builder.root / relative
        path.write_bytes(path.read_bytes() + b"\n/* generation C */\n")
    reference = _alternate_build(builder, tmp_path / "reference", previous=True)
    build = builder.build(october)
    assert _tree(site) == reference
    assert build.patch.cover_files_read == 0
    assert _package(site, "2026-04") == april_initial
    assert _package(site, "2026-07") == july_initial
    assert len(set((site / "assets").glob("app.*.css"))) == 3
    validate_site(site, workspace=builder.workspace_directory)

    previous_times = {
        path: (site / path).stat().st_mtime_ns for path in _tree(site)
    }
    assert builder.build().patch.written == ()
    assert _tree(site) == reference
    assert previous_times == {
        path: (site / path).stat().st_mtime_ns for path in reference
    }
    with repository.transaction() as connection:
        repository.write_sync_state(connection, april_state)
        repository.write_sync_state(connection, july_state)
    builder.build()
    assert not (site / _css(april_initial)).exists()
    assert not (site / _css(july_initial)).exists()
    assert (
        _package(site, "2026-04")["data_revision"] == april_initial["data_revision"]
    )
    assert len(list((site / "assets").iterdir())) == 3
    assert _tree(site) == _alternate_build(
        builder, tmp_path / "clean", previous=False
    )


@pytest.mark.parametrize("change", ["same-size", "missing", "extra", "page-pin"])
def test_candidate_checks_closure_even_with_matching_build_state(
    tmp_path: Path, change: str
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()
    site = builder.site_directory
    if change == "same-size":
        target = site / _css(_package(site, "2026-04"))
        target.write_bytes(b"X" * target.stat().st_size)
        reason = "resource hash is invalid"
    elif change == "missing":
        (site / quarter_paths("2026-04")[2]).unlink()
        reason = "required artifact is missing"
    elif change == "extra":
        (site / "extra.txt").write_bytes(b"unexpected")
        reason = "unreferenced artifact"
    else:
        target = site / "2026-04/index.html"
        target.write_bytes(
            target.read_bytes().replace(b"data-data-url=", b"data-otherurl=")
        )
        reason = "offline page data binding is invalid"
    state = json.loads(builder.state_path.read_bytes())
    state["artifacts"] = _tree(site)
    state["artifact_sizes"] = {
        relative: (site / relative).stat().st_size
        for relative in state["artifacts"]
    }
    builder.state_path.write_bytes(json.dumps(state).encode("utf-8"))

    validate_build_state(site, builder.workspace_directory)
    with pytest.raises(SiteCandidateError, match=reason):
        validate_site(site, workspace=builder.workspace_directory)


def test_temporary_publish_with_retained_quarter_and_then_corrupt_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = create_release_project(tmp_path)
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    builder = make_builder(root)
    builder.build()
    site = builder.site_directory
    april = _package(site, "2026-04")
    repository = SubjectRepository(Database(builder.database.path))
    retained = Quarter(2026, 4)
    _block(repository, retained)
    _commit_and_push_source_change(root)
    assert workflow.prepare_release(root)
    released = workflow.publish_prepared_release(root)
    assert released.published
    assert _package(site, "2026-04") == april
    assert workflow.doctor(root, local_only=True).local.site_status == "valid"
    with repository.transaction() as connection:
        repository.replace_review_issues(
            connection,
            101,
            (
                ReviewIssue(
                    "TV_QUARTER_BOUNDARY", retained, "2026-03-31",
                    {"reason": "test"}, "2026-04-01T00:00:00Z",
                ),
            ),
        )
    report = workflow.doctor(root, local_only=True).audit
    assert report.passed and report.review_quarters == ("2026-04",)
    with pytest.raises(workflow.WorkflowError, match="unresolved REVIEW"):
        workflow.prepare_release(root)
    with repository.transaction() as connection:
        repository.replace_review_issues(connection, 101, ())
    prepared = workflow.prepare_release(root)
    prepared_bytes = prepared.state_path.read_bytes()

    relative = _css(april)
    path = site / relative
    altered = b"X" * path.stat().st_size
    path.write_bytes(altered)
    state = json.loads(builder.state_path.read_bytes())
    state["artifacts"][relative] = hashlib.sha256(altered).hexdigest()
    builder.state_path.write_bytes(json.dumps(state).encode("utf-8"))

    assert workflow.local_status(root).site_status == "valid"
    doctor = workflow.doctor(root, local_only=True)
    assert doctor.audit.passed and not doctor.audit.review_quarters
    assert doctor.local.site_status == "stale"
    with pytest.raises(workflow.WorkflowError, match="dependency closure"):
        workflow.publish_prepared_release(root)
    with pytest.raises(
        workflow.WorkflowError, match="cannot retain quarter 2026-04"
    ):
        workflow.prepare_release(root)
    assert prepared.state_path.read_bytes() == prepared_bytes
    assert git(root, "ls-remote", "origin", "refs/heads/gh-pages").split()[0] == (
        released.remote_commit
    )
    assert path.read_bytes() == altered


def test_legacy_build_with_blocked_quarter_requires_verified_inputs(
    tmp_path: Path,
) -> None:
    builder, database = _build_fixture(tmp_path)
    builder.build()
    site = builder.site_directory
    old = site / "data/offline/2026-04.json"
    old.parent.mkdir()
    (site / quarter_paths("2026-04")[2]).rename(old)
    prior_tree = _tree(site)
    prior_state = builder.state_path.read_bytes()
    repo = SubjectRepository(database)
    original = _block(repo, Quarter(2026, 4))
    with pytest.raises(
        BuildError, match="older offline package requires a rebuild"
    ):
        builder.build()
    assert _tree(site) == prior_tree
    assert builder.state_path.read_bytes() == prior_state

    with repo.transaction() as connection:
        repo.write_sync_state(connection, original)
    # Simulate missing old state when rebuilding all derived artifacts from facts.
    builder.state_path.unlink()
    builder.build()
    assert not old.exists()
    validate_site(site, workspace=builder.workspace_directory)
