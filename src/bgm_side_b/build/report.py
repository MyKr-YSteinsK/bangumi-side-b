"""Safe, compact build reports kept outside generated static output."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCOPE_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
_LOCAL_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|Users)/)")


@dataclass(frozen=True)
class ProfileBuildReport:
    """One profile's non-sensitive static-output counters."""

    profile: str
    quarters: int
    subjects: int
    details: int
    covers: int
    character_images: int
    missing_covers: int
    warnings: tuple[str, ...]
    output_bytes: int
    generated_files: int
    previous_output_preserved: bool
    failures: tuple[str, ...]


def write_build_report(
    reports_directory: Path,
    scope: str,
    started_at: datetime,
    finished_at: datetime,
    reports: tuple[ProfileBuildReport, ...],
) -> Path:
    """Atomically write one report without permitting local paths in its content."""
    safe_scope = _safe_scope(scope)
    payload = {
        "scope": safe_scope,
        "started_at": _format_utc(started_at),
        "finished_at": _format_utc(finished_at),
        "profiles": [asdict(report) for report in reports],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if _LOCAL_PATH_PATTERN.search(encoded):
        raise ValueError("build report cannot include a local path")
    reports_directory.mkdir(parents=True, exist_ok=True)
    destination = reports_directory / (
        f"build-{safe_scope}-{finished_at.astimezone(UTC):%Y%m%dT%H%M%SZ}.json"
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=reports_directory, delete=False, suffix=".tmp"
    ) as stream:
        stream.write(encoded)
        temporary = Path(stream.name)
    temporary.replace(destination)
    return destination


def _safe_scope(value: str) -> str:
    normalized = _SCOPE_PATTERN.sub("-", value.strip()).strip("-")
    if not normalized:
        raise ValueError("build scope is empty")
    return normalized


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("report timestamps must include timezone information")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
