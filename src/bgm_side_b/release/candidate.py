"""Safe Pages-build marker written only after successful output promotion."""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.release.manifest import candidate_content_hash, index_candidate


def write_pages_build_marker(
    workspace: Path,
    candidate: Path,
    *,
    project_root: Path,
    deployment_path: str,
    quarter_count: int,
    subject_count: int,
) -> Path:
    """Atomically record the exact tree that a later publish may consume."""
    entries = index_candidate(candidate, deployment_path)
    commit = _source_commit(project_root)
    payload = {
        "schema_version": 1,
        "app_version": __version__,
        "source_commit": commit or "unavailable",
        "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "profile": "pages",
        "quarter_count": quarter_count,
        "subject_count": subject_count,
        "generated_file_count": len(entries),
        "candidate_content_hash": candidate_content_hash(entries),
        "deployment_path": deployment_path,
    }
    destination = workspace / "state" / "pages-build.json"
    _atomic_json(destination, payload)
    return destination


def read_pages_build_marker(workspace: Path) -> dict[str, object]:
    """Read and minimally validate a marker without leaking implementation paths."""
    try:
        payload = json.loads(
            (workspace / "state" / "pages-build.json").read_text("utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("successful Pages build marker is missing") from error
    required = {
        "schema_version",
        "app_version",
        "source_commit",
        "profile",
        "candidate_content_hash",
        "deployment_path",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("Pages build marker is invalid")
    return payload


def _source_commit(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value if len(value) == 40 else None


def _atomic_json(destination: Path, payload: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(
            payload, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        stream.write("\n")
    temporary.replace(destination)
