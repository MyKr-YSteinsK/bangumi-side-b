"""Deterministic, Git-trackable manual archive-quarter decisions."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path

from bgm_side_b.admission import QuarterOverride
from bgm_side_b.domain import Quarter


def load_quarter_overrides(path: Path) -> dict[int, QuarterOverride]:
    """Load and validate one active manual assignment per Subject ID."""
    if not path.exists():
        return {}
    with path.open("rb") as file:
        payload = tomllib.load(file)
    entries = payload.get("assignments", [])
    if not isinstance(entries, list):
        raise ValueError("quarter overrides assignments must be an array")
    assignments: dict[int, QuarterOverride] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("quarter override entry must be a table")
        subject_id = entry.get("subject_id")
        if not _positive_integer(subject_id):
            raise ValueError("quarter override subject_id must be positive")
        if subject_id in assignments:
            raise ValueError("quarter overrides must not contain duplicate subject IDs")
        reason = entry.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("quarter override reason must be a non-empty string")
        unassigned = entry.get("unassigned", False)
        if not isinstance(unassigned, bool):
            raise ValueError("quarter override unassigned must be boolean")
        has_year = "year" in entry
        has_month = "quarter_month" in entry
        if unassigned:
            if has_year or has_month:
                raise ValueError("unassigned override cannot include a quarter")
            assignments[subject_id] = QuarterOverride(None, reason)
            continue
        if not has_year or not has_month:
            raise ValueError("quarter override must include year and quarter_month")
        year = entry["year"]
        month = entry["quarter_month"]
        if not _positive_integer(year) or not _positive_integer(month):
            raise ValueError(
                "quarter override year and month must be positive integers"
            )
        assignments[subject_id] = QuarterOverride(Quarter(year, month), reason)
    return assignments


def save_quarter_overrides(
    path: Path, assignments: Mapping[int, QuarterOverride]
) -> None:
    """Atomically write stable TOML without reformatting unrelated configuration."""
    lines = [
        "# Manual archive-quarter decisions. One active entry per Bangumi Subject ID.",
        "",
    ]
    for subject_id, override in sorted(assignments.items()):
        if not _positive_integer(subject_id):
            raise ValueError("quarter override subject_id must be positive")
        lines.extend(("[[assignments]]", f"subject_id = {subject_id}"))
        if override.quarter is None:
            lines.append("unassigned = true")
        else:
            lines.extend(
                (
                    f"year = {override.quarter.year}",
                    f"quarter_month = {override.quarter.month}",
                )
            )
        if override.reason:
            lines.append(f"reason = {json.dumps(override.reason, ensure_ascii=False)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines))
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
