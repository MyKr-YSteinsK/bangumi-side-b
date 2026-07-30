"""Safe, shared console progress reporting for long-running commands."""

from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, Protocol, TextIO

ProgressLevel = Literal[
    "start", "stage", "progress", "retry", "warning", "error", "complete", "heartbeat"
]
CounterValue = str | int | float

_LEVELS = frozenset(
    {
        "start",
        "stage",
        "progress",
        "retry",
        "warning",
        "error",
        "complete",
        "heartbeat",
    }
)
_COMMAND_LABELS = {"sync": "同步", "build": "构建", "publish": "发布"}
_LEVEL_LABELS = {"retry": "重试", "warning": "警告", "error": "错误"}
_STAGE_LABELS = {
    "scope": "范围",
    "database": "SQLite schema",
    "blacklist-cleanup": "黑名单清理",
    "discovery": "候选发现",
    "candidate-summary": "候选汇总",
    "quarter-summary": "季度汇总",
    "subject-detail": "作品详情",
    "continuation": "续播刷新",
    "episodes": "章节",
    "roles": "角色",
    "character-detail": "角色详情",
    "person-detail": "声优详情",
    "cover": "封面",
    "cover-image": "封面图片",
    "character-images": "角色图片",
    "character-image": "角色图片",
    "facts": "SQLite facts",
    "view-model": "View Model",
    "local-staging": "local staging",
    "pages-staging": "Pages staging",
    "static-assets": "静态资源",
    "covers": "封面",
    "quarter-pages": "季度页",
    "detail-pages": "详情页",
    "pwa-shell": "PWA shell",
    "build-marker": "build marker",
    "validation": "staging 验证",
    "promotion": "原子替换",
    "report": "报告",
    "publish": "发布预检",
    "remote-release": "远端 gh-pages",
    "origin-main": "远端 origin/main",
    "publish-worktree": "临时 worktree",
    "publish-commit": "release commit",
    "publish-push": "推送 gh-pages",
    "publish-state": "本地发布状态",
    "publish-cleanup": "清理 worktree",
    "summary": "汇总",
    "interrupted": "中断",
}
_PATH_PATTERN = re.compile(r"(?i)(?:\b[a-z]:[\\/]|(?<!\w)/(?:[^/\s|]+/)+)[^\s|]*")
_URL_PATTERN = re.compile(r"https?://[^\s|]+", re.IGNORECASE)
_SECRET_PATTERN = re.compile(r"(?i)\b(authorization|token)\s*[:=]\s*[^\s|]+")


@dataclass(frozen=True)
class ProgressEvent:
    """A compact, safe event emitted by long-running command code."""

    timestamp_monotonic: float
    level: ProgressLevel
    command: str
    stage: str = ""
    message: str = ""
    current: str | None = None
    completed: int | None = None
    total: int | None = None
    entity_type: str | None = None
    entity_id: int | str | None = None
    quarter: str | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    retry_delay_seconds: float | None = None
    counters: Mapping[str, CounterValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Keep the immutable event compact and safe for console output."""
        if self.level not in _LEVELS:
            raise ValueError(f"unsupported progress level: {self.level}")
        object.__setattr__(self, "command", _safe_text(self.command, limit=24))
        object.__setattr__(self, "stage", _safe_text(self.stage, limit=48))
        object.__setattr__(self, "message", _safe_text(self.message, limit=120))
        object.__setattr__(
            self,
            "current",
            _safe_text(self.current, limit=80) if self.current is not None else None,
        )
        object.__setattr__(
            self,
            "entity_type",
            _safe_text(self.entity_type, limit=24)
            if self.entity_type is not None
            else None,
        )
        object.__setattr__(
            self,
            "quarter",
            _safe_text(self.quarter, limit=16) if self.quarter is not None else None,
        )
        object.__setattr__(
            self,
            "counters",
            MappingProxyType(
                {
                    _safe_text(str(key), limit=32): _safe_counter_value(value)
                    for key, value in sorted(
                        self.counters.items(), key=lambda item: str(item[0])
                    )
                }
            ),
        )


class ProgressReporter(Protocol):
    """The only console-facing API used by command and business layers."""

    def start(self, **kwargs: object) -> None: ...

    def stage(self, **kwargs: object) -> None: ...

    def progress(self, **kwargs: object) -> None: ...

    def retry(self, **kwargs: object) -> None: ...

    def warning(self, **kwargs: object) -> None: ...

    def error(self, **kwargs: object) -> None: ...

    def complete(self, **kwargs: object) -> None: ...

    def activity(self, **kwargs: object) -> ProgressActivity: ...


class ProgressActivity:
    """Set a heartbeat activity without changing exception propagation."""

    def __init__(
        self,
        begin: Callable[[ProgressEvent], None] | None = None,
        end: Callable[[ProgressEvent], None] | None = None,
        event_factory: Callable[[], ProgressEvent] | None = None,
    ) -> None:
        self._begin = begin
        self._end = end
        self._event_factory = event_factory
        self._event: ProgressEvent | None = None

    def __enter__(self) -> ProgressActivity:
        if self._begin is not None and self._event_factory is not None:
            self._event = self._event_factory()
            self._begin(self._event)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self._event is not None and self._end is not None:
            self._end(self._event)
        return False


class NullProgressReporter:
    """A no-op reporter for libraries and explicitly quiet CLI invocations."""

    def __enter__(self) -> NullProgressReporter:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    def close(self) -> None:
        """Match the console reporter lifecycle without emitting output."""

    def start(self, **kwargs: object) -> None:
        pass

    def stage(self, **kwargs: object) -> None:
        pass

    def progress(self, **kwargs: object) -> None:
        pass

    def retry(self, **kwargs: object) -> None:
        pass

    def warning(self, **kwargs: object) -> None:
        pass

    def error(self, **kwargs: object) -> None:
        pass

    def complete(self, **kwargs: object) -> None:
        pass

    def activity(self, **kwargs: object) -> ProgressActivity:
        return ProgressActivity()


class ConsoleProgressReporter:
    """Thread-safe stderr reporter with TTY refreshes and plain output."""

    def __init__(
        self,
        command: str,
        *,
        mode: Literal["auto", "plain", "off"] = "auto",
        verbose: bool = False,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_interval_seconds: float = 10.0,
        refresh_interval_seconds: float = 0.25,
    ) -> None:
        if mode not in {"auto", "plain", "off"}:
            raise ValueError("progress mode must be auto, plain, or off")
        if verbose and mode == "off":
            raise ValueError("--progress off cannot be combined with --verbose")
        if heartbeat_interval_seconds <= 0 or refresh_interval_seconds < 0:
            raise ValueError("progress intervals must be positive")
        self.command = command
        self.mode = mode
        self.verbose = verbose
        self.stream = stream or sys.stderr
        self._clock = clock
        self._started_at = clock()
        self._heartbeat_interval = heartbeat_interval_seconds
        self._refresh_interval = refresh_interval_seconds
        self._lock = threading.RLock()
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._activity: ProgressEvent | None = None
        self._last_event_at = self._started_at
        self._last_refresh_at = self._started_at
        self._last_plain_progress_at: float | None = None
        self._dynamic_line = False
        self._closed = False

    def __enter__(self) -> ConsoleProgressReporter:
        if self.mode != "off":
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"bgmb-{self.command}-progress",
                daemon=True,
            )
            self._heartbeat_thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Stop heartbeat output and finish any active TTY line."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._activity = None
            self._stop_heartbeat.set()
            if self._dynamic_line:
                self.stream.write("\n")
                self.stream.flush()
                self._dynamic_line = False
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1)

    def start(self, **kwargs: object) -> None:
        self._emit("start", **kwargs)

    def stage(self, **kwargs: object) -> None:
        self._emit("stage", **kwargs)

    def progress(self, **kwargs: object) -> None:
        self._emit("progress", **kwargs)

    def retry(self, **kwargs: object) -> None:
        self._emit("retry", **kwargs)

    def warning(self, **kwargs: object) -> None:
        self._emit("warning", **kwargs)

    def error(self, **kwargs: object) -> None:
        self._emit("error", **kwargs)

    def complete(self, **kwargs: object) -> None:
        self._emit("complete", **kwargs)

    def activity(self, **kwargs: object) -> ProgressActivity:
        return ProgressActivity(
            self._begin_activity,
            self._end_activity,
            lambda: self._event("stage", **kwargs),
        )

    def _emit(self, level: ProgressLevel, **kwargs: object) -> None:
        if self.mode == "off":
            return
        event = self._event(level, **kwargs)
        with self._lock:
            if self._closed:
                return
            self._last_event_at = event.timestamp_monotonic
            if self._should_throttle_plain_progress(event):
                return
            dynamic = (
                self._uses_tty_refresh() and level == "progress" and not self.verbose
            )
            if dynamic and self._dynamic_line and (
                event.timestamp_monotonic - self._last_refresh_at
                < self._refresh_interval
            ):
                return
            self._write_event(event, dynamic=dynamic)
            if dynamic:
                self._last_refresh_at = event.timestamp_monotonic

    def _should_throttle_plain_progress(self, event: ProgressEvent) -> bool:
        if event.level != "progress" or self.verbose or self._uses_tty_refresh():
            return False
        if self._last_plain_progress_at is None:
            self._last_plain_progress_at = event.timestamp_monotonic
            return False
        is_final = event.completed is not None and event.completed == event.total
        is_batch = event.completed is not None and event.completed % 5 == 0
        if is_final or is_batch or (
            event.timestamp_monotonic - self._last_plain_progress_at >= 5
        ):
            self._last_plain_progress_at = event.timestamp_monotonic
            return False
        return True

    def _event(self, level: ProgressLevel, **kwargs: object) -> ProgressEvent:
        return ProgressEvent(
            timestamp_monotonic=self._clock(),
            level=level,
            command=self.command,
            stage=_string_value(kwargs, "stage"),
            message=_string_value(kwargs, "message"),
            current=_optional_string_value(kwargs, "current"),
            completed=_optional_int_value(kwargs, "completed"),
            total=_optional_int_value(kwargs, "total"),
            entity_type=_optional_string_value(kwargs, "entity_type"),
            entity_id=_optional_identifier_value(kwargs, "entity_id"),
            quarter=_optional_string_value(kwargs, "quarter"),
            attempt=_optional_int_value(kwargs, "attempt"),
            max_attempts=_optional_int_value(kwargs, "max_attempts"),
            retry_delay_seconds=_optional_float_value(kwargs, "retry_delay_seconds"),
            counters=_counter_values(kwargs.get("counters")),
        )

    def _begin_activity(self, event: ProgressEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._activity = event
            self._last_event_at = event.timestamp_monotonic

    def _end_activity(self, event: ProgressEvent) -> None:
        with self._lock:
            if self._activity == event:
                self._activity = None

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(self._heartbeat_interval):
            with self._lock:
                if self._closed or self._activity is None:
                    continue
                now = self._clock()
                if now - self._last_event_at < self._heartbeat_interval:
                    continue
                activity = self._activity
                waiting = format_elapsed(now - activity.timestamp_monotonic)
                event = replace(
                    activity,
                    timestamp_monotonic=now,
                    level="heartbeat",
                    message=f"仍在运行｜{activity.message}｜已等待 {waiting}",
                )
                self._last_event_at = now
                self._write_event(event, dynamic=False)

    def _uses_tty_refresh(self) -> bool:
        is_tty = getattr(self.stream, "isatty", lambda: False)
        return self.mode == "auto" and bool(is_tty())

    def _write_event(self, event: ProgressEvent, *, dynamic: bool) -> None:
        line = _format_event(event, self._started_at)
        if dynamic:
            self.stream.write(f"\r{line}")
            self.stream.flush()
            self._dynamic_line = True
            return
        if self._dynamic_line:
            self.stream.write("\n")
            self._dynamic_line = False
        self.stream.write(f"{line}\n")
        self.stream.flush()


def create_progress_reporter(
    args: object, command: str, *, stream: TextIO | None = None
) -> ConsoleProgressReporter | NullProgressReporter:
    """Build one command-scoped reporter from the shared CLI arguments."""
    mode = getattr(args, "progress", "auto")
    verbose = bool(getattr(args, "verbose", False))
    if bool(getattr(args, "quiet", False)):
        mode = "off"
    if mode == "off":
        return NullProgressReporter()
    return ConsoleProgressReporter(command, mode=mode, verbose=verbose, stream=stream)


def format_elapsed(seconds: float) -> str:
    """Render monotonic elapsed time without relying on wall-clock time."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_event(event: ProgressEvent, started_at: float) -> str:
    elapsed = format_elapsed(event.timestamp_monotonic - started_at)
    label = _LEVEL_LABELS.get(
        event.level, _COMMAND_LABELS.get(event.command, event.command)
    )
    if event.completed is not None and event.total is not None:
        label = f"{label} {event.completed}/{event.total}"
    parts = []
    if event.stage:
        parts.append(_STAGE_LABELS.get(event.stage, event.stage))
    if event.message:
        parts.append(event.message)
    if event.quarter:
        parts.append(event.quarter)
    if event.current:
        parts.append(event.current)
    if event.entity_type and event.entity_id is not None:
        parts.append(f"{event.entity_type} {event.entity_id}")
    if event.attempt is not None and event.max_attempts is not None:
        parts.append(f"第 {event.attempt}/{event.max_attempts} 次")
    if event.retry_delay_seconds is not None:
        parts.append(f"{event.retry_delay_seconds:.2f} 秒后继续")
    parts.extend(f"{key} {value}" for key, value in event.counters.items())
    return f"[+{elapsed}] [{label}] {'｜'.join(parts) or event.stage or '进行中'}"


def _safe_text(value: str | None, *, limit: int) -> str:
    text = " ".join((value or "").split())
    text = _URL_PATTERN.sub("[链接已省略]", text)
    text = _SECRET_PATTERN.sub(r"\1=[已省略]", text)
    text = _PATH_PATTERN.sub("[本机路径已省略]", text)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _safe_counter_value(value: CounterValue) -> CounterValue:
    return _safe_text(value, limit=48) if isinstance(value, str) else value


def _string_value(values: Mapping[str, object], name: str) -> str:
    value = values.get(name, "")
    return value if isinstance(value, str) else str(value)


def _optional_string_value(values: Mapping[str, object], name: str) -> str | None:
    value = values.get(name)
    return value if isinstance(value, str) else None


def _optional_identifier_value(
    values: Mapping[str, object], name: str
) -> int | str | None:
    value = values.get(name)
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        return value
    return None


def _optional_int_value(values: Mapping[str, object], name: str) -> int | None:
    value = values.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float_value(values: Mapping[str, object], name: str) -> float | None:
    value = values.get(name)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _counter_values(value: object) -> Mapping[str, CounterValue]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (str, int, float)) and not isinstance(item, bool)
    }
