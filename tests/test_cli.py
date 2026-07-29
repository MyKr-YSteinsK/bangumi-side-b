"""Tests for the initial command-line interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from bgm_side_b import __version__
from bgm_side_b.cli import build_parser, find_project_root, main


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

    assert scoped.scope == ["2022", "1"]
    assert scoped.target == "pages"
    assert not scoped.all
    assert all_quarters.all
