"""Real process death, reader visibility and retry against isolated local projects."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

import pytest

from bgm_side_b.build.serve import create_preview_server
from bgm_side_b.cli import main
from bgm_side_b.domain import Quarter
from bgm_side_b.overrides import load_quarter_overrides
from bgm_side_b.release.site_candidate import SiteCandidateError, validate_build_state
from bgm_side_b.release.workflow import local_status
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.write_recovery import pending_facts
from tests.release_fixture import ROOT, create_release_project, make_builder

# The child signals only after the selected product-side operation. The parent
# kills it from outside Python, bypassing context managers and finally blocks.
_PRELUDE = """
from pathlib import Path
import time
from bgm_side_b.cli import main
def pause(*args, **kwargs):
    Path('paused').touch()
    time.sleep(90)
"""


@contextmanager
def stopped_at(root: Path, program: str) -> Iterator[subprocess.Popen]:
    signal = root / "paused"
    signal.unlink(missing_ok=True)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (ROOT, ROOT / "src"))))
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", _PRELUDE + program],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while not signal.exists():
            if child.poll() is not None:
                pytest.fail(child.communicate()[0].decode("utf-8", "replace"))
            if time.monotonic() > deadline:
                pytest.fail("child did not reach the fault point")
            time.sleep(0.02)
        yield child
    finally:
        child.kill()
        child.wait(timeout=15)
        child.stdout.close()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, _ = create_release_project(tmp_path)
    (root / "pyproject.toml").touch()
    monkeypatch.chdir(root)
    return root


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_assign_death_lock_and_conflicting_user_edit(project, capsys):
    repo = SubjectRepository(make_builder(project).database)
    path = project / "config/quarter-overrides.toml"
    before = path.read_bytes() + b"\n# retain original bytes\n"
    path.write_bytes(before)
    with stopped_at(
        project,
        """
from bgm_side_b.repository import SubjectRepository
SubjectRepository.replace_subject_snapshot = pause
main(['assign', '101', '2026', '10'])
""",
    ):
        for command in (
            ["sync", "2026", "4"],
            ["assign", "202", "--clear"],
            ["classify", "202", "--japanese"],
            ["build", "--all"],
            ["release", "prepare"],
            ["release", "publish"],
        ):
            with pytest.raises(SystemExit) as rejected:
                main(command)
            assert rejected.value.code == 2
            assert "writer busy" in capsys.readouterr().err
        assert repo.get_subject_facts(101).premiere.quarter == Quarter(2026, 4)
        assert load_quarter_overrides(path)[101].quarter == Quarter(2026, 10)
    interrupted = path.read_bytes()
    # A future schema must fail before replaying even a valid local journal.
    with sqlite3.connect(repo.database.path) as db:
        db.execute("UPDATE database_metadata SET value='99' WHERE key='schema_version'")
    with pytest.raises(SystemExit):
        main(["build", "--all"])
    assert path.read_bytes() == interrupted and pending_facts(repo.database)
    with sqlite3.connect(repo.database.path) as db:
        db.execute("UPDATE database_metadata SET value='2' WHERE key='schema_version'")

    assert local_status(project).sqlite_status == "recovery pending"
    edited = interrupted + b"\n# new decision\n"
    path.write_bytes(edited)
    with pytest.raises(SystemExit):
        main(["build", "--all"])
    assert "fact recovery conflict" in capsys.readouterr().err
    assert path.read_bytes() == edited and pending_facts(repo.database)
    path.write_bytes(interrupted)
    assert main(["build", "--all"]) == 0
    assert path.read_bytes() == before and not pending_facts(repo.database)
    for _ in range(2):
        assert main(["assign", "101", "2026", "10"]) == 0
    assert (
        load_quarter_overrides(path)[101].quarter
        == repo.get_subject_facts(101).premiere.quarter
    )


@pytest.mark.parametrize("committed", [False, True])
def test_classify_config_and_cover_follow_one_sql_commit(project, committed):
    repo = SubjectRepository(make_builder(project).database)
    path = project / "config/japanese-overrides.toml"
    cover = project / "workspace/covers/101.webp"
    original = path.read_bytes(), cover.read_bytes()
    hook = (
        """
from bgm_side_b.repository import SubjectRepository
SubjectRepository.delete_subjects = pause
"""
        if not committed
        else """
import bgm_side_b.write_recovery as recovery
real = recovery.recover_facts
def stop_after_commit(db):
    if recovery.pending_facts(db):
        pause()
    return real(db)
recovery.recover_facts = stop_after_commit
"""
    )
    with stopped_at(project, hook + "main(['classify','101','--non-japanese'])"):
        assert not cover.exists()
    assert (repo.get_subject_facts(101) is None) == committed
    if not committed:
        with stopped_at(
            project,
            """
import bgm_side_b.write_recovery as recovery
original = recovery.replace_bytes
def interrupt_repair(path, content):
    original(path, content)
    pause()
recovery.replace_bytes = interrupt_repair
main(['build','--all'])
""",
        ):
            assert pending_facts(repo.database)
    assert main(["build", "--all"]) == 0
    assert (repo.get_subject_facts(101) is None) == committed
    assert cover.exists() != committed
    if committed:
        assert b"non_japanese" in path.read_bytes()
    else:
        assert (path.read_bytes(), cover.read_bytes()) == original


@pytest.mark.parametrize("phase", ["mixed", "before_state", "after_state"])
def test_build_death_reader_and_byte_identical_retry(project, tmp_path, phase):
    builder = make_builder(project)
    builder.build()
    site = project / "dist/site"
    css = project / "static/css/site.css"
    css.write_bytes(css.read_bytes() + b"\n/* recovery fixture */\n")
    reference = make_builder(project)
    reference.site_directory = tmp_path / "reference-site"
    reference.workspace_directory = tmp_path / "reference-workspace"
    shutil.copytree(
        project / "workspace/covers", reference.workspace_directory / "covers"
    )
    reference.build()
    expected = _tree(reference.site_directory)
    old = _tree(site)
    hooks = {
        "mixed": """
import os
def partial(self, stage, desired, changed, stale):
    path = changed[0]
    os.replace(stage / path, self.site_directory / path)
    pause()
Writer._apply_files = partial
""",
        "before_state": "Writer._commit_state = pause\n",
        "after_state": """
original = Writer._commit_state
def after(self, state):
    original(self, state)
    pause()
Writer._commit_state = after
""",
    }
    program = "from bgm_side_b.build.writer import IncrementalSiteWriter as Writer\n"
    with stopped_at(project, program + hooks[phase] + "main(['build','--all'])"):
        with pytest.raises(SiteCandidateError, match="recovery pending"):
            validate_build_state(site, project / "workspace")
        assert local_status(project).site_status == "recovery pending"
        if phase == "mixed":
            mixed = _tree(site)
            assert mixed != expected and mixed != old
            server = create_preview_server(site, port=0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for name in expected:
                    if expected[name] != old[name]:
                        url = f"http://127.0.0.1:{server.server_port}/bangumi-side-b/{name}"
                        with urlopen(url, timeout=5) as response:
                            assert (
                                hashlib.sha256(response.read()).hexdigest()
                                == mixed[name]
                            )
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
    if phase == "mixed":
        name = next(p for p in old if old[p] != mixed[p])
        changed = (site / name).read_bytes()
        (site / name).write_bytes(b"edited during interruption")
        with pytest.raises(SystemExit):
            main(["build", "--all"])
        assert (site / name).read_bytes() == b"edited during interruption"
        (site / name).write_bytes(changed)
    if phase == "before_state":
        with stopped_at(
            project,
            """
import os
original = os.replace
def interrupt_repair(source, target):
    original(source, target)
    if Path(source).name == '.bgmb.restore':
        pause()
os.replace = interrupt_repair
main(['build','--all'])
""",
        ):
            with pytest.raises(SiteCandidateError):
                validate_build_state(site, project / "workspace")
    for _ in range(2):
        assert main(["build", "--all"]) == 0
        assert _tree(site) == expected
        validate_build_state(site, project / "workspace")


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_windows_open_handle_blocks_replace_until_released(project):
    builder = make_builder(project)
    builder.build()
    site = project / "dist/site"
    css = project / "static/css/site.css"
    css.write_bytes(css.read_bytes() + b"/* windows file lock */")
    target = site / "assets/app.css"
    old = target.read_bytes()
    with target.open("rb"):
        with pytest.raises(SystemExit):
            main(["build", "--all"])
        assert target.read_bytes() == old
    assert main(["build", "--all"]) == 0
    assert target.read_bytes() != old
    validate_build_state(site, project / "workspace")


def _install_fixture_api(monkeypatch):
    import bgm_side_b.cli as cli
    from bgm_side_b.discovery import DiscoveryBatch
    from bgm_side_b.domain import MediaFormat
    from bgm_side_b.sync import ArchiveSynchronizer
    from tests.test_archive_sync import FakeApi, FakeDiscovery, _candidate, _detail

    class Client(FakeApi):
        def __init__(self, **kwargs):
            super().__init__({subject: _detail(subject) for subject in (101, 909)})

        def close(self):
            pass

    def synchronizer(*args, **kwargs):
        return ArchiveSynchronizer(
            *args,
            **kwargs,
            browse=FakeDiscovery(DiscoveryBatch((_candidate(101, MediaFormat.TV),))),
            search=FakeDiscovery(DiscoveryBatch(())),
        )

    monkeypatch.setattr(cli, "BangumiApiClient", Client)
    monkeypatch.setattr(cli, "ArchiveSynchronizer", synchronizer)


def test_range_cover_death_resumes_only_incomplete_quarter(project, monkeypatch):
    repo = SubjectRepository(make_builder(project).database)
    january, april = Quarter(2026, 1), Quarter(2026, 4)
    prior = repo.get_sync_state(april)
    complete = replace(prior, quarter=january)
    with repo.transaction() as conn:
        repo.write_sync_state(conn, complete)
        repo.write_sync_state(conn, replace(prior, covers_status="incomplete"))
    old_cover = (project / "workspace/covers/101.webp").read_bytes()
    with stopped_at(
        project,
        """
from pytest import MonkeyPatch
from tests.test_write_recovery import _install_fixture_api
from bgm_side_b.sync import ArchiveSynchronizer
_install_fixture_api(MonkeyPatch())
ArchiveSynchronizer._sync_covers = pause
main(['sync', '--from','2026','1','--to','2026','4'])
""",
    ):
        state = repo.get_sync_state(april)
        assert (state.facts_status, state.covers_status) == ("complete", "incomplete")
    assert repo.get_sync_state(january) == complete
    assert (project / "workspace/covers/101.webp").read_bytes() == old_cover
    _install_fixture_api(monkeypatch)
    command = ["sync", "--from", "2026", "1", "--to", "2026", "4"]
    assert main(command) == 0
    assert repo.get_sync_state(april).covers_status == "complete"
    cover = repo.get_subject_facts(101).cover
    assert (
        hashlib.sha256((project / "workspace/covers/101.webp").read_bytes()).hexdigest()
        == cover.content_hash
    )
    assert repo.get_sync_state(january) == complete
    result = _tree(project / "dist/site")
    assert main(command) == 0
    assert _tree(project / "dist/site") == result
    validate_build_state(project / "dist/site", project / "workspace")


def test_cli_prepare_nested_build_and_stale_publish_rejected(project, monkeypatch):
    from bgm_side_b.release import workflow
    from tests.release_fixture import git

    with (project / ".git/info/exclude").open("a") as handle:
        handle.write("\n/pyproject.toml\n/paused\n")
    monkeypatch.setattr(workflow, "validate_release_origin", lambda _: "fixture")
    assert main(["release", "prepare"]) == 0
    prepared = project / "workspace/state/prepared-release.json"
    original = prepared.read_bytes()
    site = project / "dist/site"
    (site / "assets/app.css").write_bytes(b"other build")
    with pytest.raises(SystemExit):
        main(["release", "publish"])
    assert prepared.read_bytes() == original
    assert git(project, "ls-remote", "origin", "refs/heads/gh-pages") == ""


def test_import_death_before_sql_commit_restores_cover_and_decision(
    project, monkeypatch
):
    path = project / "config/quarter-overrides.toml"
    cover = project / "workspace/covers/909.webp"
    repo = SubjectRepository(make_builder(project).database)
    original = path.read_bytes()
    with stopped_at(
        project,
        """
from pytest import MonkeyPatch
from tests.test_write_recovery import _install_fixture_api
from bgm_side_b.repository import SubjectRepository
_install_fixture_api(MonkeyPatch())
SubjectRepository.replace_subject_snapshot = pause
main(['assign','909','2026','4'])
""",
    ):
        assert cover.exists() and 909 in load_quarter_overrides(path)
    assert main(["build", "--all"]) == 0
    assert repo.get_subject_facts(909) is None
    assert not cover.exists() and path.read_bytes() == original
    _install_fixture_api(monkeypatch)
    for _ in range(2):
        assert main(["assign", "909", "2026", "4"]) == 0
    snapshot = repo.get_subject_facts(909)
    assert snapshot.premiere.quarter == load_quarter_overrides(path)[909].quarter
    assert snapshot.cover.content_hash == hashlib.sha256(cover.read_bytes()).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_locked_cover_preserves_bytes_and_quarter_retry_repairs_metadata(
    project, monkeypatch
):
    _install_fixture_api(monkeypatch)
    repo = SubjectRepository(make_builder(project).database)
    cover = project / "workspace/covers/101.webp"
    old = cover.read_bytes()
    with cover.open("rb"):
        assert main(["sync", "2026", "4"]) == 0
        assert cover.read_bytes() == old
        state = repo.get_sync_state(Quarter(2026, 4))
        assert (state.facts_status, state.covers_status) == ("complete", "incomplete")
    assert main(["sync", "2026", "4"]) == 0
    assert repo.get_sync_state(Quarter(2026, 4)).covers_status == "complete"
    assert (
        hashlib.sha256(cover.read_bytes()).hexdigest()
        == repo.get_subject_facts(101).cover.content_hash
    )


def test_death_before_patch_manifest_keeps_valid_old_candidate(project):
    builder = make_builder(project)
    builder.build()
    workspace = project / "workspace"
    site = project / "dist/site"
    before = _tree(site)
    css = project / "static/css/site.css"
    css.write_bytes(css.read_bytes() + b"/* staged */")
    with stopped_at(project, """
import bgm_side_b.build.writer as writer
writer.begin_patch = pause
main(['build','--all'])
"""):
        validate_build_state(site, workspace)
        assert _tree(site) == before
    assert main(["build", "--all"]) == 0
    validate_build_state(site, workspace)
    assert _tree(site) != before
    assert not tuple((workspace / "build-staging").glob("recovery-*"))
