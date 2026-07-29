"""File-tree indexing and snapshot-manifest generation without network access."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from bgm_side_b.release.models import FileEntry, SnapshotManifest

_EXCLUDED_FROM_SNAPSHOT = frozenset(
    {"release.json", "snapshot-manifest.json", "release-history.json"}
)
_FORBIDDEN_PARTS = frozenset({"workspace", ".git", "reports", "backups", "tmp"})


class ManifestError(ValueError):
    """Raised when a candidate tree cannot safely become a snapshot."""


def index_candidate(
    candidate: Path,
    deployment_path: str,
    *,
    exclude_control_files: bool = True,
) -> tuple[FileEntry, ...]:
    """Return a stable, path-safe index for an already-built Pages tree."""
    root = candidate.resolve()
    if not root.is_dir():
        raise ManifestError("Pages candidate is missing")
    prefix = _deployment_prefix(deployment_path)
    entries: list[FileEntry] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        _validate_relative(relative)
        if exclude_control_files and relative in _EXCLUDED_FROM_SNAPSHOT:
            continue
        if relative.startswith("media/characters/"):
            raise ManifestError("Pages candidate contains character media")
        content = path.read_bytes()
        entries.append(
            FileEntry(
                f"{prefix}{relative}",
                hashlib.sha256(content).hexdigest(),
                len(content),
                _content_type(path),
                _category(relative),
            )
        )
    if not entries:
        raise ManifestError("Pages candidate is empty")
    return tuple(sorted(entries, key=lambda entry: entry.url))


def candidate_content_hash(entries: tuple[FileEntry, ...]) -> str:
    """Hash the stable identity of a complete snapshot, not timestamps."""
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.url):
        digest.update(
            f"{entry.url}\0{entry.sha256}\0{entry.size_bytes}\n".encode()
        )
    return digest.hexdigest()


def build_snapshot_manifest(
    entries: tuple[FileEntry, ...],
    *,
    release_version: str,
    app_version: str,
    deployment_path: str,
    generated_at: datetime | None = None,
) -> SnapshotManifest:
    """Construct a manifest whose own file is deliberately absent from entries."""
    if not _RELEASE_VERSION.fullmatch(release_version):
        raise ManifestError("release version must use YYYY.MM.DD.N")
    timestamp = generated_at or datetime.now(UTC)
    return SnapshotManifest(
        release_version,
        app_version,
        _deployment_prefix(deployment_path),
        timestamp.isoformat().replace("+00:00", "Z"),
        tuple(sorted(entries, key=lambda entry: entry.url)),
        candidate_content_hash(entries),
    )


def manifest_json(manifest: SnapshotManifest) -> str:
    """Return compact stable JSON suitable for hashing and browser verification."""
    return (
        json.dumps(
            manifest.payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def validate_manifest_payload(payload: object) -> dict[str, object]:
    """Reject malformed or self-referential manifest JSON before publication."""
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ManifestError("snapshot manifest schema is invalid")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ManifestError("snapshot manifest files are invalid")
    entries: list[FileEntry] = []
    for item in files:
        if not isinstance(item, dict):
            raise ManifestError("snapshot manifest entry is invalid")
        try:
            entry = FileEntry(
                str(item["url"]),
                str(item["sha256"]),
                int(item["size_bytes"]),
                str(item["content_type"]),
                str(item["category"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestError("snapshot manifest entry is invalid") from error
        if entry.size_bytes < 0 or not _SHA256.fullmatch(entry.sha256):
            raise ManifestError("snapshot manifest entry hash is invalid")
        entries.append(entry)
    if len({entry.url for entry in entries}) != len(entries):
        raise ManifestError("snapshot manifest has duplicate URLs")
    if payload.get("entry_count") != len(entries):
        raise ManifestError("snapshot manifest entry count is invalid")
    if payload.get("total_bytes") != sum(entry.size_bytes for entry in entries):
        raise ManifestError("snapshot manifest byte count is invalid")
    if payload.get("content_hash") != candidate_content_hash(tuple(entries)):
        raise ManifestError("snapshot manifest content hash is invalid")
    return payload


def _deployment_prefix(value: str) -> str:
    path = value.strip().replace("\\", "/")
    if not path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise ManifestError("deployment path is invalid")
    return path.rstrip("/") + "/"


def _validate_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or set(path.parts) & _FORBIDDEN_PARTS:
        raise ManifestError("candidate path escapes publish scope")
    if path.suffix in {".sqlite", ".sqlite3", ".db"}:
        raise ManifestError("candidate contains a database")


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _category(relative: str) -> str:
    if relative.startswith("media/covers/"):
        return "cover"
    if relative.startswith("icons/") or relative.endswith("favicon.svg"):
        return "icon"
    if relative.endswith(".html"):
        return "html"
    if relative.endswith((".webmanifest", ".json")):
        return "metadata"
    return "shell"


_RELEASE_VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}\.\d+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
