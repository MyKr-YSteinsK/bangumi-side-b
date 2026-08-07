"""Tests for the initial command-line interface."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bgm_side_b import __version__
from bgm_side_b.cli import _relative_output_path, build_parser, find_project_root, main


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--help"])

    assert result.value.code == 0
    assert "Local-first Bangumi archive tooling." in capsys.readouterr().out


def test_version_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as result:
        main(["--version"])

    assert result.value.code == 0
    assert capsys.readouterr().out == f"bgmb {__version__}\n"


def test_project_root_is_discovered_from_a_child_directory() -> None:
    root = Path(__file__).resolve().parents[1]

    assert find_project_root(root / "src" / "bgm_side_b") == root


def test_build_parser_accepts_scope_or_all_and_profile_target() -> None:
    parser = build_parser()
    scoped = parser.parse_args(["build", "2022", "1", "--target", "pages"])
    all_quarters = parser.parse_args(["build", "--all"])
    discarded = parser.parse_args(["build", "--all", "--discard-pending"])
    promote = parser.parse_args(["promote", "pages"])
    doctor = parser.parse_args(["doctor", "--local"])
    prepare = parser.parse_args(["release", "prepare", "--quiet"])
    release_publish = parser.parse_args(["release", "publish", "--progress", "plain"])

    assert scoped.scope == ["2022", "1"]
    assert scoped.target == "pages"
    assert not scoped.all
    assert all_quarters.all
    assert discarded.discard_pending
    assert promote.profile == "pages"
    assert doctor.local
    assert prepare.release_command == "prepare"
    assert prepare.quiet
    assert release_publish.release_command == "publish"
    assert release_publish.progress == "plain"


def test_sync_parser_requires_one_year_and_one_quarter() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["sync", "2026"])

    assert error.value.code == 2


def test_shared_progress_arguments_are_available_on_every_long_command() -> None:
    parser = build_parser()

    sync = parser.parse_args(["sync", "2022", "1", "--progress", "plain"])
    build = parser.parse_args(["build", "--all", "--quiet"])
    publish = parser.parse_args(["publish", "--verbose"])

    assert sync.progress == "plain"
    assert build.quiet
    assert publish.verbose


def test_quiet_and_verbose_are_rejected_together() -> None:
    with pytest.raises(SystemExit) as result:
        main(["build", "--all", "--quiet", "--verbose"])

    assert result.value.code == 2


def test_progress_off_and_verbose_are_rejected_together() -> None:
    with pytest.raises(SystemExit) as result:
        main(["build", "--all", "--progress", "off", "--verbose"])

    assert result.value.code == 2


def test_final_report_path_is_project_relative() -> None:
    root = Path("project")
    report = root / "workspace" / "reports" / "sync.json"

    assert _relative_output_path(root, report) == "workspace/reports/sync.json"


def test_sync_cli_rejects_every_scope_outside_2026_04(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(SystemExit) as error:
        main(["sync", "2026", "1"])

    assert error.value.code == 2
    assert "只允许 2026-04" in capsys.readouterr().err


def test_build_cli_rejects_every_scope_outside_2026_04(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(SystemExit) as error:
        main(["build", "2026", "1"])

    assert error.value.code == 2
    assert "只允许 2026-04" in capsys.readouterr().err
