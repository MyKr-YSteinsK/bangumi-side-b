"""Tests for the initial command-line interface."""

from __future__ import annotations

import shutil
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import bgm_side_b.cli as cli
from bgm_side_b import __version__
from bgm_side_b.api import ImageResponse, SubjectDetail
from bgm_side_b.cli import (
    _relative_output_path,
    _sync_summary_lines,
    build_parser,
    find_project_root,
    main,
)
from bgm_side_b.database import Database
from bgm_side_b.domain import Quarter
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import QuarterSyncResult

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0"
    b"\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def test_build_parser_exposes_only_unified_scope_and_release_commands() -> None:
    parser = build_parser()
    scoped = parser.parse_args(["build", "2022", "1"])
    all_quarters = parser.parse_args(["build", "--all"])
    doctor = parser.parse_args(["doctor", "--local"])
    prepare = parser.parse_args(["release", "prepare", "--quiet"])
    release_publish = parser.parse_args(["release", "publish", "--progress", "plain"])

    assert scoped.scope == ["2022", "1"]
    assert not scoped.all
    assert all_quarters.all
    assert doctor.local
    assert prepare.release_command == "prepare"
    assert prepare.quiet
    assert release_publish.release_command == "publish"
    assert release_publish.progress == "plain"


def test_sync_parser_requires_one_year_and_one_quarter() -> None:
    with pytest.raises(SystemExit) as error:
        main(["sync", "2026"])

    assert error.value.code == 2


def test_review_and_assign_parsers_accept_manual_workflow() -> None:
    parser = build_parser()

    review = parser.parse_args(["review", "2026", "4"])
    assign = parser.parse_args(["assign", "101", "2026", "4"])
    unassigned = parser.parse_args(["assign", "101", "--unassigned"])
    clear = parser.parse_args(["assign", "101", "--clear"])
    range_sync = parser.parse_args(
        ["sync", "--from", "2026", "4", "--to", "2026", "7"]
    )

    assert review.scope == ["2026", "4"]
    assert assign.assignment == ["2026", "4"]
    assert unassigned.unassigned
    assert clear.clear
    assert range_sync.range_start == ["2026", "4"]
    assert range_sync.range_end == ["2026", "7"]


def test_shared_progress_arguments_are_available_on_every_long_command() -> None:
    parser = build_parser()

    sync = parser.parse_args(["sync", "2022", "1", "--progress", "plain"])
    build = parser.parse_args(["build", "--all", "--quiet"])
    publish = parser.parse_args(["release", "publish", "--verbose"])

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


def test_release_publish_prints_post_publish_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "project"
    report = root / "workspace" / "reports" / "release-publish.json"
    monkeypatch.setattr(cli, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cli, "create_progress_reporter", lambda *args: nullcontext(object())
    )
    monkeypatch.setattr(
        cli,
        "publish_prepared_release",
        lambda *args: SimpleNamespace(
            report_path=report,
            warnings=(
                "remote published but local report finalization failed",
                "remote published but local prepared state cleanup failed",
            ),
        ),
    )

    assert main(["release", "publish", "--progress", "off"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "publish report: workspace/reports/release-publish.json",
        "warning: remote published but local report finalization failed",
        "warning: remote published but local prepared state cleanup failed",
    ]


def test_sync_summary_is_compact_and_lists_early_premiere_evidence() -> None:
    result = QuarterSyncResult(
        Quarter(2026, 4),
        "complete",
        "complete",
        3,
        2,
        1,
        0,
        0,
        (),
        (),
        ({"code": "continuing_not_confirmed", "summary": "none"},),
        (),
        continuing_end_date=1,
        continuing_episode=2,
        early_premieres=(
            {
                "subject_id": 101,
                "air_date": "2026-03-28",
                "premiere_quarter": "2026-04",
                "evidence": "2026年4月:448",
            },
        ),
    )

    assert _sync_summary_lines(result) == (
        "NEW TV 2 | CONTINUING TV 3 | MOVIE 1",
        "continuing evidence: end_date=1, main_episode=2, unresolved=0",
        "AUTO PREMIERE 101: 2026-03-28 -> 2026-04 (2026年4月:448)",
        "exceptions: warnings=1, errors=0",
    )


def test_sync_cli_rejects_malformed_scope_before_any_network_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(SystemExit) as error:
        main(["sync", "2026"])

    assert error.value.code == 2
    assert "requires YEAR QUARTER_MONTH" in capsys.readouterr().err


def test_assign_missing_subject_uses_one_fake_official_detail_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    calls: list[int] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def get_subject(self, subject_id: int) -> SubjectDetail:
            calls.append(subject_id)
            return SubjectDetail.from_payload(
                {
                    "id": subject_id,
                    "type": 2,
                    "name": "Original",
                    "platform": "TV",
                    "date": "2026-04-02",
                    "infobox": [{"key": "国家/地区", "value": "日本"}],
                    "images": {"large": "https://images.example/cover.png"},
                }
            )

        def fetch_image(self, url: str, *, max_bytes: int) -> ImageResponse:
            assert url == "https://images.example/cover.png"
            assert len(_PNG) <= max_bytes
            return ImageResponse(_PNG, "image/png", url)

        def close(self) -> None:
            pass

    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "BangumiApiClient", FakeClient)

    assert main(["assign", "101", "2026", "4"]) == 0

    assert calls == [101]
    assert "assignment saved: 101 -> 2026-04" in capsys.readouterr().out
    facts = SubjectRepository(
        Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    ).get_subject_facts(101)
    assert facts is not None
    assert facts.premiere is not None
    assert facts.premiere.quarter.year == 2026
    assert "subject_id = 101" in (
        root / "config" / "quarter-overrides.toml"
    ).read_text(encoding="utf-8")


def test_build_cli_rejects_missing_database_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    monkeypatch.chdir(root)

    with pytest.raises(SystemExit) as error:
        main(["build", "2026", "1"])

    assert error.value.code == 2
    assert "database is missing" in capsys.readouterr().err
