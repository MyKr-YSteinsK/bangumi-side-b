"""Release-number, changelog, and concise history helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_VERSION = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.(\d+)$")

_CHANGE_KIND_LABELS = {
    "initial": "首次发布",
    "system": "系统有变化",
    "data": "资料有变化",
    "system_and_data": "系统与资料均有变化",
    "both": "系统与资料均有变化",
    "none": "无结构化变化",
    "首次发布": "首次发布",
    "系统有变化": "系统有变化",
    "资料有变化": "资料有变化",
    "系统与资料均有变化": "系统与资料均有变化",
    "无结构化变化": "无结构化变化",
}


def change_kind_display(change_kind: object) -> str:
    """Return the public Chinese label while preserving unknown legacy values."""
    if isinstance(change_kind, str):
        return _CHANGE_KIND_LABELS.get(change_kind, change_kind)
    return str(change_kind)


def unreleased_changes(changelog: Path) -> tuple[str, ...]:
    """Read the bullet text below the current Chinese or English draft heading."""
    text = changelog.read_text(encoding="utf-8")
    match = re.search(
        r"^## (?:Unreleased|尚未发布)\s*$([\s\S]*?)(?=^## |\Z)",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise ValueError("CHANGELOG.md has no draft release section")
    return tuple(
        item.strip()
        for item in re.findall(r"^\s*-\s+(.+?)\s*$", match.group(1), re.MULTILINE)
        if item.strip()
    )


def next_release_version(previous: str | None, *, now: datetime | None = None) -> str:
    """Allocate the next UTC date sequence without persisting it yet."""
    date = (now or datetime.now(UTC)).astimezone(UTC).date()
    prefix = f"{date.year:04d}.{date.month:02d}.{date.day:02d}"
    if previous:
        match = _VERSION.fullmatch(previous)
        if match is None:
            raise ValueError("previous release version is invalid")
        if ".".join(match.groups()[:3]) == prefix:
            return f"{prefix}.{int(match.group(4)) + 1}"
    return f"{prefix}.1"


def read_history(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("release history is invalid") from error
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise ValueError("release history is invalid")
    return payload


def write_history(path: Path, history: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(
            history, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        stream.write("\n")
        temporary = stream.name
    os.replace(temporary, path)


def history_entry(
    *,
    release_version: str,
    app_version: str,
    content_hash: str,
    quarter_count: int,
    subject_count: int,
    total_bytes: int,
    change_kind: str,
    system_summary: tuple[str, ...],
    data_summary: dict[str, object],
    commit_sha: str,
    published_at: datetime | None = None,
) -> dict[str, object]:
    timestamp = (published_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    return {
        "release_version": release_version,
        "app_version": app_version,
        "published_at": timestamp,
        "content_hash": content_hash,
        "quarter_count": quarter_count,
        "subject_count": subject_count,
        "total_bytes": total_bytes,
        "change_kind": change_kind,
        "system_summary": list(system_summary),
        "data_summary": data_summary,
        "commit_sha": commit_sha,
    }
