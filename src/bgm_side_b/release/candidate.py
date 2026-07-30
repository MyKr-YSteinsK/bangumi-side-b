"""Atomic build-bound facts for a Pages candidate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.release.manifest import candidate_content_hash, index_candidate
from bgm_side_b.release.snapshot import snapshot_index


def write_pages_build_marker(
    workspace: Path,
    candidate: Path,
    *,
    project_root: Path,
    deployment_path: str,
    quarter_count: int,
    subject_count: int,
    models: tuple[object, ...] = (),
) -> Path:
    """Atomically bind a promoted Pages tree to the facts used to render it."""
    entries = index_candidate(candidate, deployment_path)
    source_commit = _source_commit(project_root) or "unavailable"
    rules_hash, blacklist_hash = _rule_hashes(project_root / "config")
    facts = snapshot_index(models, rules_hash=rules_hash, blacklist_hash=blacklist_hash)
    facts_hash = _json_hash(facts)
    business_hash = candidate_content_hash(entries)
    data_generation = read_data_generation(workspace)
    candidate_id = _json_hash(
        {
            "source_commit": source_commit,
            "business_content_hash": business_hash,
            "facts_snapshot_hash": facts_hash,
            "rules_hash": rules_hash,
            "blacklist_hash": blacklist_hash,
        }
    )
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    marker = {
        "schema": 2,
        "candidate_id": candidate_id,
        "source_commit": source_commit,
        "app_version": __version__,
        "built_at": timestamp,
        "deployment_path": deployment_path,
        "business_content_hash": business_hash,
        "facts_snapshot_hash": facts_hash,
        "rules_hash": rules_hash,
        "blacklist_hash": blacklist_hash,
        "data_generation": data_generation,
        "quarter_count": quarter_count,
        "subject_count": subject_count,
        "generated_file_count": len(entries),
        "profile": "pages",
    }
    snapshot = {
        "schema": 2,
        "candidate_id": candidate_id,
        "source_commit": source_commit,
        "facts_snapshot_hash": facts_hash,
        "rules_hash": rules_hash,
        "blacklist_hash": blacklist_hash,
        "data_generation": data_generation,
        "facts": facts,
    }
    state = workspace / "state"
    _replace_build_pair(
        state / "pages-build.json",
        marker,
        state / "pages-snapshot.json",
        snapshot,
    )
    return state / "pages-build.json"


def read_pages_build_marker(workspace: Path) -> dict[str, object]:
    """Read the marker without accepting a legacy, unbound candidate."""
    payload = _read_json(workspace / "state" / "pages-build.json", "Pages build marker")
    required = {
        "schema",
        "candidate_id",
        "source_commit",
        "app_version",
        "profile",
        "business_content_hash",
        "facts_snapshot_hash",
        "rules_hash",
        "blacklist_hash",
        "data_generation",
        "deployment_path",
    }
    if payload.get("schema") != 2 or not required.issubset(payload):
        raise ValueError("Pages build marker is invalid; rebuild Pages")
    return payload


def read_pages_build_snapshot(workspace: Path) -> dict[str, object]:
    """Read the compact build-bound facts used by publish, never the live database."""
    payload = _read_json(
        workspace / "state" / "pages-snapshot.json", "Pages facts snapshot"
    )
    facts = payload.get("facts")
    if (
        payload.get("schema") != 2
        or not isinstance(payload.get("candidate_id"), str)
        or not isinstance(payload.get("facts_snapshot_hash"), str)
        or not isinstance(facts, dict)
        or _json_hash(facts) != payload["facts_snapshot_hash"]
    ):
        raise ValueError("Pages facts snapshot is invalid; rebuild Pages")
    return payload


def read_data_generation(workspace: Path) -> int:
    try:
        payload = json.loads(
            (workspace / "state" / "data-generation.json").read_text("utf-8")
        )
    except FileNotFoundError:
        return 0
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("data generation state is invalid") from error
    generation = payload.get("generation") if isinstance(payload, dict) else None
    if not isinstance(generation, int) or generation < 0:
        raise ValueError("data generation state is invalid")
    return generation


def advance_data_generation(workspace: Path) -> int:
    """Persist one monotonic successful-sync generation without mtimes."""
    generation = read_data_generation(workspace) + 1
    _atomic_json(
        workspace / "state" / "data-generation.json",
        {"schema": 1, "generation": generation},
    )
    return generation


def mark_data_generation_dirty(workspace: Path) -> None:
    """Block publication until a complete sync verifies its mutated facts."""
    _atomic_json(
        workspace / "state" / "data-generation-dirty.json",
        {"schema": 1},
    )


def clear_data_generation_dirty(workspace: Path) -> None:
    """Clear the conservative sync-failure marker after a complete sync."""
    (workspace / "state" / "data-generation-dirty.json").unlink(missing_ok=True)


def data_generation_is_dirty(workspace: Path) -> bool:
    """Return whether facts might contain a partial or interrupted sync."""
    path = workspace / "state" / "data-generation-dirty.json"
    try:
        payload = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("data generation verification state is invalid") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("data generation verification state is invalid")
    return True


def _replace_build_pair(
    marker_path: Path,
    marker: dict[str, object],
    snapshot_path: Path,
    snapshot: dict[str, object],
) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    previous = {
        path: path.read_bytes() if path.exists() else None
        for path in (marker_path, snapshot_path)
    }
    temporary_marker = _temporary_json(marker_path.parent, marker)
    temporary_snapshot = _temporary_json(snapshot_path.parent, snapshot)
    try:
        os.replace(temporary_snapshot, snapshot_path)
        os.replace(temporary_marker, marker_path)
    except OSError:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_suffix(path.suffix + ".restore")
                temporary.write_bytes(content)
                os.replace(temporary, path)
        raise
    finally:
        for temporary in (temporary_marker, temporary_snapshot):
            Path(temporary).unlink(missing_ok=True)


def _temporary_json(directory: Path, payload: dict[str, object]) -> str:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False
    ) as stream:
        json.dump(
            payload, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        stream.write("\n")
        return stream.name


def _atomic_json(destination: Path, payload: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_json(destination.parent, payload)
    os.replace(temporary, destination)


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is missing") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} is invalid")
    return payload


def _rule_hashes(config: Path) -> tuple[str, str]:
    digest = hashlib.sha256()
    for path in sorted(item for item in config.rglob("*") if item.is_file()):
        digest.update(path.relative_to(config).as_posix().encode())
        digest.update(path.read_bytes())
    blacklist = hashlib.sha256((config / "bangumi.toml").read_bytes()).hexdigest()
    return digest.hexdigest(), blacklist


def _json_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
