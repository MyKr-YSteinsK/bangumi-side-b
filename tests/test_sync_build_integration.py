"""Ensure successful sync invokes the unified incremental build once."""

from __future__ import annotations

import shutil
from pathlib import Path

import bgm_side_b.cli as cli
from bgm_side_b.domain import Quarter
from bgm_side_b.sync import SyncRun, SyncScope


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(Path(__file__).parents[1] / "config", root / "config")
    (root / "pyproject.toml").write_text("[project]\nname = 'test'\n", "utf-8")
    (root / "workspace" / "reports").mkdir(parents=True)
    return root


class _Client:
    def __init__(self, **_: object) -> None:
        pass

    def close(self) -> None:
        pass


class _Sync:
    result: SyncRun

    def __init__(self, *_: object, **__: object) -> None:
        pass

    def run(self, scope: SyncScope) -> SyncRun:
        return self.result


def test_successful_sync_builds_and_failed_sync_does_not(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _root(tmp_path)
    report = root / "workspace" / "reports" / "sync.json"
    report.write_text("{}", encoding="utf-8")
    scope = SyncScope(Quarter(2026, 7), Quarter(2026, 7))
    builds: list[bool] = []
    fail_build = False
    clock_ns = 0

    def controlled_clock() -> int:
        nonlocal clock_ns
        clock_ns += 1_000_000
        return clock_ns

    class FakeBuild:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def build(self):
            builds.append(True)
            if fail_build:
                raise cli.SiteBuildError("synthetic build failure")
            return type(
                "Run",
                (),
                {"report_path": root / "workspace" / "reports" / "build.json"},
            )()

    monkeypatch.setattr(cli, "find_project_root", lambda: root)
    monkeypatch.setattr(cli, "BangumiApiClient", _Client)
    monkeypatch.setattr(cli, "ArchiveSynchronizer", _Sync)
    monkeypatch.setattr(cli, "UnifiedSiteBuilder", FakeBuild)
    monkeypatch.setattr(cli, "_monotonic_ns", controlled_clock)
    _Sync.result = SyncRun(scope, (), report, 0, 12, 3)
    assert cli.main(["sync", "2026", "7", "--quiet"]) == 0
    assert builds == [True]
    successful_output = capsys.readouterr().out
    assert "sync processing elapsed: 12 ms" in successful_output
    assert "sync report serialization elapsed: 3 ms" in successful_output
    assert "automatic incremental build invocation elapsed: 1 ms" in (
        successful_output
    )
    assert "incremental site build" in successful_output

    builds.clear()
    _Sync.result = SyncRun(scope, (), report, 1)
    assert cli.main(["sync", "2026", "7", "--quiet"]) == 1
    assert builds == []

    fail_build = True
    _Sync.result = SyncRun(scope, (), report, 0)
    assert cli.main(["sync", "2026", "7", "--quiet"]) == 1
    assert "automatic build failed after 1 ms" in capsys.readouterr().out
