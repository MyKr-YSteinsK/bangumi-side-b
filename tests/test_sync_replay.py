"""Replay a managed two-quarter archive through the CLI using synthetic evidence."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
from contextlib import closing, redirect_stdout
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b import cli, sync
from bgm_side_b.archive_config import add_auto_excluded_subject
from bgm_side_b.discovery import DiscoveryBatch, DiscoveryFailure, DiscoverySource
from bgm_side_b.domain import MediaFormat, Quarter
from tests.test_archive_sync import (
    ROOT,
    FakeApi,
    _auto_settings_path,
    _candidate,
    _detail,
)

APRIL = Quarter(2026, 4)
JULY = Quarter(2026, 7)
RANGE = ["--from", "2026", "4", "--to", "2026", "7"]


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _rows(database: Path, commits: dict[str, int]) -> dict:
    with closing(sqlite3.connect(database)) as connection:
        names = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        result = {}
        for (name,) in names:
            rows = []
            for row in connection.execute(f'SELECT * FROM "{name}"'):
                if name == "database_metadata" and row[0] == "file_commit":
                    row = (row[0], commits.setdefault(row[1], len(commits)))
                rows.append(row)
            result[name] = sorted(rows, key=repr)
        return result


def replay(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Return per-step observations suitable for a cross-revision comparison."""
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    (root / "pyproject.toml").touch()
    shutil.copytree(ROOT / "config", root / "config")
    shutil.copytree(ROOT / "static", root / "static")
    shutil.copyfile(ROOT / "CHANGELOG.md", root / "CHANGELOG.md")
    settings = _auto_settings_path(root / "config")
    settings.write_bytes(
        settings.read_bytes().replace(
            b"\nexcluded_subject_ids = []", b"\nexcluded_subject_ids = [110]"
        )
    )
    for subject in (103, 104):
        add_auto_excluded_subject(settings, subject, name_cn="fixture")
    details = {
        subject: _detail(subject, cover=f"https://example.invalid/{subject}.png")
        for subject in (101, 103, 104, 105, 106, 110, 201, 501729)
    }
    details[104] = _detail(104, country="法国")
    details[105] = _detail(105, country=None, rating_count=5)
    details[106] = _detail(106, media=MediaFormat.MOVIE)
    details[107] = replace(_detail(107), platform=None)
    details[201] = _detail(201, air_date="2026-07-03")
    details[501729] = replace(details[501729], rating_total=5)
    api = FakeApi(details, episode_airdates={101: (date(2026, 7, 5),)})
    registry_calls = []
    details[103] = replace(details[103], eps=None)

    def episode_count(subject_id):
        registry_calls.append(subject_id)
        return 12

    monkeypatch.setattr(api, "get_main_episode_count", episode_count, raising=False)
    monkeypatch.setattr(api, "close", lambda: None, raising=False)
    monkeypatch.setattr(cli, "BangumiApiClient", lambda **_: api)
    monkeypatch.setattr(sync, "_monotonic_ns", lambda: 0)
    monkeypatch.setattr(cli, "_monotonic_ns", lambda: 0)
    calls = []
    commits = {}
    observations = {}

    def browse(_adapter, quarter):
        calls.append(("browse", quarter.label))
        if quarter == JULY:
            july_tv = _candidate(201, MediaFormat.TV, details[201].air_date)
            return DiscoveryBatch(
                (replace(july_tv, provenance=("browse:TV:2026-07",)),)
            )
        return DiscoveryBatch(
            tuple(
                _candidate(
                    subject,
                    MediaFormat.MOVIE if subject == 106 else MediaFormat.TV,
                    details[subject].air_date,
                )
                for subject in (101, 103, 104, 105, 106, 110, 501729)
            )
        )

    def search(_adapter, quarter):
        calls.append(("search", quarter.label))
        if quarter == APRIL:
            search_only = replace(
                _candidate(107, MediaFormat.TV),
                media_formats=frozenset(),
                provenance=("search:air_date:2026-03-25..2026-07-01",),
            )
            return DiscoveryBatch(
                (search_only,),
                (
                    DiscoveryFailure(
                        DiscoverySource.SEARCH, quarter.label, "timeout", "fixture"
                    ),
                ),
            )
        return DiscoveryBatch(())

    monkeypatch.setattr(sync.BrowseDiscoveryAdapter, "discover", browse)
    monkeypatch.setattr(sync.SearchDiscoveryAdapter, "discover", search)
    real_synchronizer = sync.ArchiveSynchronizer
    monkeypatch.setattr(
        cli,
        "ArchiveSynchronizer",
        lambda *args, **kwargs: real_synchronizer(
            *args, **kwargs, evaluation_date=date(2026, 7, 20)
        ),
    )
    workspace = root / "workspace"

    def run(name, command):
        stamp = f"2026-07-20T00:00:{len(observations):02d}Z"
        monkeypatch.setattr(sync, "_timestamp", lambda: stamp)
        for log in (
            calls,
            registry_calls,
            api.subject_calls,
            api.episode_calls,
            api.image_calls,
        ):
            log.clear()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main([*command, "--quiet"])
        output = stdout.getvalue()
        reports = {}
        for path in re.findall(r"workspace/reports/[^\s]+\.json", output):
            report = json.loads((root / path).read_bytes())
            report.pop("duration_seconds", None)
            reports[Path(path).name.split("-")[0]] = report
        observations[name] = {
            "exit": code,
            "stdout": re.sub(r"-\d{8}T\d{6}Z\.json", "-STAMP.json", output),
            "reports": reports,
            "discovery": list(calls),
            "details": list(api.subject_calls),
            "episodes": list(api.episode_calls),
            "episode_counts": list(registry_calls),
            "downloads": sorted(api.image_calls),
            "database": _rows(workspace / "data/bangumi-side-b.sqlite3", commits),
            "config": _hashes(root / "config"),
            "covers": _hashes(workspace / "covers"),
            "site": _hashes(root / "dist/site"),
        }

    run("july", ["sync", "2026", "7"])
    run("april_backfill", ["sync", "2026", "4"])
    run("skip", ["sync", *RANGE])
    details[106] = replace(details[106], infobox=())
    run("refresh_review", ["sync", *RANGE, "--refresh-existing"])
    details[106] = _detail(106, media=MediaFormat.MOVIE)
    api.failures = frozenset({103})
    run("failed_detail", ["sync", "2026", "4"])
    api.failures = frozenset()
    saved = details[103]
    details[103] = replace(saved, subject_id=999)
    run("invalid_detail", ["sync", "2026", "4"])
    details[103] = saved
    run("retry", ["sync", *RANGE])
    run("noop", ["build", "--all"])
    # Rebuild in the same fixture with its authoritative inputs intact.
    (root / "dist/site").rename(root / "dist/previous-site")
    (workspace / "build-state.json").rename(workspace / "previous-build-state.json")
    run("full", ["build", "--all"])
    return observations


def test_cli_sync_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    steps = replay(tmp_path, monkeypatch)
    first = steps["april_backfill"]
    report = first["reports"]["sync"]
    assert report["auto_restored"] == [103]
    assert report["auto_reconciled"] == [104]
    assert report["new_auto_by_reason"] == {
        "outcome_dominated_low_rating": 1,
        "insufficient_airing_information": 1,
    }
    assert sorted(first["details"]) == [101, 103, 104, 105, 106, 107, 501729]
    assert steps["skip"]["details"] == steps["skip"]["downloads"] == []
    assert steps["skip"]["site"] == first["site"]
    assert steps["skip"]["reports"]["build"]["written_artifacts_count"] == 0
    assert first["episode_counts"] == [103]
    assert (
        101,
        2026,
        7,
        "continuing",
        "automatic",
        "main_episode_airdate",
        "2026-07-05",
    ) in first["database"]["subject_quarters"]
    reviews = steps["refresh_review"]["database"]["subject_review_issues"]
    assert [row[:4] for row in reviews] == [
        (106, "JAPANESE_CLASSIFICATION_UNRESOLVED", None, None)
    ]
    assert steps["refresh_review"]["reports"]["sync"]["persisted_review_count"] == 0
    before_failure = steps["refresh_review"]
    for name, error_code in (
        ("failed_detail", "timeout"),
        ("invalid_detail", "normalization"),
    ):
        failed = steps[name]
        assert failed["exit"] == 1
        errors = failed["reports"]["sync"]["quarters"][0]["errors"]
        assert errors[0]["code"] == error_code
        for key in ("config", "covers", "site"):
            assert failed[key] == before_failure[key]
        for table, rows in before_failure["database"].items():
            if table != "sync_states":
                assert failed["database"][table] == rows
    assert steps["retry"]["database"]["subject_review_issues"] == []
    assert all(
        step["exit"] == 0
        for name, step in steps.items()
        if name not in {"failed_detail", "invalid_detail"}
    )
    assert steps["full"]["site"] == steps["noop"]["site"] == steps["retry"]["site"]
    assert {"2026-04/index.html", "2026-07/index.html"} <= steps["full"]["site"].keys()
    assert steps["noop"]["reports"]["build"]["written_artifacts_count"] == 0
    full_writes = steps["full"]["reports"]["build"]["written_artifacts_count"]
    assert full_writes == len(steps["full"]["site"])
