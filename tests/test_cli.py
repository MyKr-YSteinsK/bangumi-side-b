"""Tests for the initial command-line interface."""

from __future__ import annotations

import shutil
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bgm_side_b.cli as cli
from bgm_side_b import __version__
from bgm_side_b.api import ImageResponse, SubjectDetail
from bgm_side_b.cli import (
    _auto_blacklist_line,
    _relative_output_path,
    _sync_review_lines,
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
    assert __version__ == "0.6.0"

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
    serve_open = parser.parse_args(["serve", "--open"])
    prepare = parser.parse_args(["release", "prepare", "--quiet"])
    release_publish = parser.parse_args(["release", "publish", "--progress", "plain"])

    assert scoped.scope == ["2022", "1"]
    assert not scoped.all
    assert all_quarters.all
    assert doctor.local
    assert serve_open.open_browser
    assert prepare.release_command == "prepare"
    assert prepare.quiet
    assert release_publish.release_command == "publish"
    assert release_publish.progress == "plain"


def test_serve_prints_url_and_ctrl_c_instructions_after_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "find_project_root", lambda: tmp_path)

    def fake_serve(*args: object, **kwargs: object) -> None:
        callback = kwargs["ready_callback"]
        assert callable(callback)
        callback("http://127.0.0.1:8123/bangumi-side-b/")

    monkeypatch.setattr(cli, "serve_site", fake_serve)

    assert main(["serve", "--port", "8123"]) == 0
    assert capsys.readouterr().out == (
        "Bangumi Side B preview\n"
        "http://127.0.0.1:8123/bangumi-side-b/\n"
        "Press Ctrl+C to stop.\n"
    )


def test_serve_open_calls_browser_only_when_explicitly_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "find_project_root", lambda: tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    def fake_serve(*args: object, **kwargs: object) -> None:
        kwargs["ready_callback"]("http://127.0.0.1:8123/bangumi-side-b/")

    monkeypatch.setattr(cli, "serve_site", fake_serve)

    assert main(["serve"]) == 0
    assert opened == []
    capsys.readouterr()

    assert main(["serve", "--open"]) == 0
    assert opened == ["http://127.0.0.1:8123/bangumi-side-b/"]
    assert "warning:" not in capsys.readouterr().out


@pytest.mark.parametrize("browser_result", [False, RuntimeError("browser unavailable")])
def test_serve_open_browser_failure_is_only_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    browser_result: object,
) -> None:
    monkeypatch.setattr(cli, "find_project_root", lambda: tmp_path)

    def open_browser(url: str) -> bool:
        if isinstance(browser_result, Exception):
            raise browser_result
        return browser_result

    monkeypatch.setattr(cli.webbrowser, "open", open_browser)
    monkeypatch.setattr(
        cli,
        "serve_site",
        lambda *args, **kwargs: kwargs["ready_callback"](
            "http://127.0.0.1:8123/bangumi-side-b/"
        ),
    )

    assert main(["serve", "--open"]) == 0
    assert capsys.readouterr().out.endswith(
        "warning: could not open the default browser; open the URL manually\n"
    )


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
        "publish report: unavailable",
        "warning: remote published but local report finalization failed",
        "warning: remote published but local prepared state cleanup failed",
    ]


def test_release_publish_prints_an_existing_report_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "project"
    report = root / "workspace" / "reports" / "release-publish.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "find_project_root", lambda: root)
    monkeypatch.setattr(
        cli, "create_progress_reporter", lambda *args: nullcontext(object())
    )
    monkeypatch.setattr(
        cli,
        "publish_prepared_release",
        lambda *args: SimpleNamespace(report_path=report, warnings=()),
    )

    assert main(["release", "publish", "--progress", "off"]) == 0
    assert capsys.readouterr().out == (
        "publish report: workspace/reports/release-publish.json\n"
    )


def test_active_documentation_matches_the_final_cli_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    documents = (
        root / "README.md",
        root / "docs" / "USER_GUIDE.md",
        root / "docs" / "project-requirements-baseline.md",
        root / "docs" / "development.md",
        root / "docs" / "static-build.md",
        root / "docs" / "pwa.md",
        root / "docs" / "publish.md",
        root / "docs" / "releases.md",
    )
    active = "\n".join(path.read_text("utf-8") for path in documents)
    for removed in (
        "bgmb publish",
        "bgmb promote",
        "build --target",
        "build --discard-pending",
        "dist/pages",
        "snapshot-manifest",
    ):
        assert removed not in active
    for current in (
        "bgmb sync 2026 7",
        "bgmb build --all",
        "bgmb serve --port 8000",
        "bgmb release prepare",
        "bgmb release publish",
        "dist/site",
    ):
        assert current in active

    parser = build_parser()
    assert parser.parse_args(["sync", "2026", "7"]).command == "sync"
    assert parser.parse_args(
        ["sync", "--from", "2026", "4", "--to", "2026", "7"]
    ).range_start == ["2026", "4"]
    assert parser.parse_args(["build", "--all"]).all
    assert parser.parse_args(["serve", "--port", "8000"]).port == 8000
    assert parser.parse_args(
        ["release", "prepare"]
    ).release_command == "prepare"
    assert parser.parse_args(
        ["release", "publish"]
    ).release_command == "publish"


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


def test_sync_review_summary_uses_scoped_persisted_count() -> None:
    issue = SimpleNamespace(
        issue_code="JAPANESE_CLASSIFICATION_UNRESOLVED",
    )
    result = QuarterSyncResult(
        Quarter(2026, 4),
        "complete",
        "complete",
        12,
        1,
        0,
        0,
        0,
        (issue,) * 12,
        (),
        (),
        (),
        persisted_review_count=0,
    )

    assert _sync_review_lines(result, SimpleNamespace(), Quarter(2026, 4)) == ()
    result = replace(result, persisted_review_count=12)
    assert _sync_review_lines(result, SimpleNamespace(), Quarter(2026, 4)) == (
        "12 persisted REVIEW items; run bgmb review for the complete local queue",
    )


def test_sync_summary_exposes_unresolved_cold_blacklist_reason() -> None:
    assert _auto_blacklist_line(
        {
            "subject_id": 659091,
            "title": "冷门电影",
            "reason": "insufficient_airing_information",
            "issue_code": "MOVIE_DATE_UNRESOLVED",
        }
    ) == (
        "AUTO BLACKLISTED 659091: 冷门电影 "
        "reason=insufficient_airing_information issue=MOVIE_DATE_UNRESOLVED"
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


def test_assign_missing_subject_keeps_override_when_report_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def get_subject(self, subject_id: int) -> SubjectDetail:
            return SubjectDetail.from_payload(
                {
                    "id": subject_id,
                    "type": 2,
                    "name": "Original",
                    "platform": "TV",
                    "date": "2026-04-02",
                    "infobox": [{"key": "国家/地区", "value": "日本"}],
                }
            )

        def fetch_image(self, url: str, *, max_bytes: int) -> ImageResponse:
            return ImageResponse(_PNG, "image/png", url)

        def close(self) -> None:
            pass

    def fail_report(*_: object, **__: object) -> Path:
        raise OSError("report volume unavailable")

    monkeypatch.chdir(root)
    monkeypatch.setattr(cli, "BangumiApiClient", FakeClient)
    monkeypatch.setattr(
        "bgm_side_b.sync.ArchiveSynchronizer._write_single_import_report",
        fail_report,
    )

    assert main(["assign", "101", "2026", "4"]) == 0

    output = capsys.readouterr().out
    assert "assignment saved: 101 -> 2026-04" in output
    assert "manual import report unavailable" in output
    facts = SubjectRepository(
        Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    ).get_subject_facts(101)
    assert facts is not None
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


def test_build_cli_reports_invalid_database_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    database = root / "workspace" / "data" / "bangumi-side-b.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not a sqlite database")
    monkeypatch.chdir(root)

    with pytest.raises(SystemExit) as error:
        main(["build", "2026", "4"])

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "database" in stderr.lower()
    assert "traceback" not in stderr.lower()
