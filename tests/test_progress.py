"""Focused tests for the shared command progress reporter."""

from __future__ import annotations

import io
import threading
import time

import pytest

from bgm_side_b.progress import (
    ConsoleProgressReporter,
    NullProgressReporter,
    format_elapsed,
)


class _Stream(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_auto_mode_uses_tty_refresh_and_plain_mode_does_not() -> None:
    tty_stream = _Stream(tty=True)
    plain_stream = _Stream(tty=False)
    with ConsoleProgressReporter("sync", stream=tty_stream) as tty_reporter:
        tty_reporter.progress(message="正在处理作品", completed=1, total=2)
    with ConsoleProgressReporter("sync", stream=plain_stream) as plain_reporter:
        plain_reporter.progress(message="正在处理作品", completed=1, total=2)

    assert "\r" in tty_stream.getvalue()
    assert "\r" not in plain_stream.getvalue()
    assert "[同步 1/2]" in plain_stream.getvalue()


def test_tty_refresh_clears_a_previous_longer_line() -> None:
    stream = _Stream(tty=True)
    with ConsoleProgressReporter(
        "sync", stream=stream, refresh_interval_seconds=0
    ) as reporter:
        reporter.progress(message="一个足够长的作品标题", completed=1, total=2)
        reporter.progress(message="短", completed=2, total=2)

    assert stream.getvalue().count("\x1b[2K") == 2


def test_plain_off_and_elapsed_formatting() -> None:
    plain_stream = _Stream(tty=False)
    off_stream = _Stream(tty=False)
    with ConsoleProgressReporter(
        "build", mode="plain", stream=plain_stream
    ) as reporter:
        reporter.stage(message="读取事实")
    with ConsoleProgressReporter("build", mode="off", stream=off_stream) as reporter:
        reporter.stage(message="不应输出")

    assert "[构建] 读取事实" in plain_stream.getvalue()
    assert off_stream.getvalue() == ""
    assert format_elapsed(3661.9) == "01:01:01"


def test_plain_progress_is_throttled_but_keeps_batch_and_final_updates() -> None:
    stream = _Stream(tty=False)
    with ConsoleProgressReporter("sync", mode="plain", stream=stream) as reporter:
        for completed in range(1, 7):
            reporter.progress(message="正在处理作品", completed=completed, total=6)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 3
    assert "1/6" in lines[0]
    assert "5/6" in lines[1]
    assert "6/6" in lines[2]


def test_safe_output_truncates_and_omits_urls_and_absolute_paths() -> None:
    stream = _Stream(tty=False)
    with ConsoleProgressReporter("publish", mode="plain", stream=stream) as reporter:
        reporter.warning(
            message=(
                "标题" * 80
                + " https://cdn.example.test/image.webp?secret=1"
                + " C:\\Users\\name\\token.txt"
            )
        )

    output = stream.getvalue()
    assert "https://" not in output
    assert "C:\\Users" not in output
    assert "…" in output


def test_heartbeat_starts_for_an_activity_and_stops_on_close() -> None:
    stream = _Stream(tty=False)
    reporter = ConsoleProgressReporter(
        "sync", stream=stream, heartbeat_interval_seconds=0.01
    )
    with reporter:
        with reporter.activity(
            message="等待 Bangumi API", entity_type="subject", entity_id=1
        ):
            time.sleep(0.03)
    output = stream.getvalue()
    time.sleep(0.02)

    assert "仍在运行" in output
    assert stream.getvalue() == output


def test_threaded_events_stay_as_complete_lines() -> None:
    stream = _Stream(tty=False)
    with ConsoleProgressReporter(
        "sync", mode="plain", verbose=True, stream=stream
    ) as reporter:
        threads = [
            threading.Thread(
                target=reporter.progress, kwargs={"message": f"事件 {index}"}
            )
            for index in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 12
    assert all(line.startswith("[+") and "事件" in line for line in lines)


def test_null_reporter_is_silent_and_does_not_swallow_interrupts() -> None:
    reporter = NullProgressReporter()

    with pytest.raises(KeyboardInterrupt):
        with reporter.activity(message="等待"):
            raise KeyboardInterrupt
