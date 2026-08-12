"""Validation and deterministic identity for the one formal ``dist/site`` tree."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SiteCandidateError(ValueError):
    """Raised when a site tree is not safe or complete enough to publish."""


@dataclass(frozen=True)
class CandidateIdentity:
    """Stable facts used to bind a prepared release to a real site tree."""

    schema: int
    source_commit: str
    artifact_count: int
    total_bytes: int
    content_hash: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source_commit": self.source_commit,
            "artifact_count": self.artifact_count,
            "total_bytes": self.total_bytes,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class SiteCandidate:
    """A validated tree identity plus its public quarter scope."""

    identity: CandidateIdentity
    public_quarters: tuple[str, ...]
    files: tuple[str, ...]


_REQUIRED_FILES = (
    "index.html",
    "archive/index.html",
    "settings/index.html",
    "assets/app.css",
    "assets/app.js",
    "assets/pwa.js",
    "manifest.webmanifest",
    "sw.js",
    "data/archive-index.json",
    "data/pwa-shell.json",
)
_FORBIDDEN_PARTS = frozenset(
    {"workspace", "reports", "backups", ".git", ".venv", "__pycache__"}
)
_FORBIDDEN_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})
_ROLE_PARTS = frozenset(
    {"character", "characters", "person", "persons", "voice", "voices", "voice-actors"}
)
_ABSOLUTE_WINDOWS = re.compile(r"\\\\[A-Za-z0-9._-]+[\\/]")
_STACK_TRACE = re.compile(
    r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+"
)
_TOKEN_NAME = re.compile(
    r"(?i)(?:^|[._-])(authorization|access[-_]?token|"
    r"refresh[-_]?token|api[-_]?key|secret)(?:[._-]|$)"
)
_ABSOLUTE_DRIVE = re.compile(r"(?i)(?:^|[\s\"'(=])[a-z]:[\\/]")
_TEXT_SUFFIXES = frozenset(
    {".html", ".css", ".js", ".json", ".webmanifest", ".svg", ".txt", ".xml"}
)


def validate_site(site_root: Path, *, source_commit: str = "") -> SiteCandidate:
    """Validate and index the actual files that would be sent to Pages."""
    root = site_root.resolve()
    if not root.is_dir():
        raise SiteCandidateError("dist/site is missing")
    files = tuple(sorted(path for path in root.rglob("*") if path.is_file()))
    if not files:
        raise SiteCandidateError("dist/site is empty")
    relative_files: list[str] = []
    content_hash = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        _validate_path(relative)
        content = path.read_bytes()
        _validate_content(relative, content)
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        relative_files.append(relative)
        total_bytes += size
        content_hash.update(relative.encode("utf-8"))
        content_hash.update(b"\0")
        content_hash.update(digest.encode("ascii"))
        content_hash.update(b"\0")
        content_hash.update(str(size).encode("ascii"))
        content_hash.update(b"\n")

    missing = [
        relative for relative in _REQUIRED_FILES if relative not in relative_files
    ]
    if missing:
        raise SiteCandidateError("formal site is missing required artifacts")
    quarters = _public_quarters(root, set(relative_files))
    if not quarters:
        raise SiteCandidateError("formal site has no publishable quarter")
    identity = CandidateIdentity(
        schema=1,
        source_commit=source_commit,
        artifact_count=len(relative_files),
        total_bytes=total_bytes,
        content_hash=content_hash.hexdigest(),
    )
    return SiteCandidate(identity, quarters, tuple(relative_files))


def validate_build_state(site_root: Path, workspace: Path) -> None:
    """Require the derived build state to describe the exact site tree."""
    try:
        payload = json.loads((workspace / "build-state.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SiteCandidateError("build state is missing or invalid") from error
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    sizes = payload.get("artifact_sizes") if isinstance(payload, dict) else None
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if schema != 1 or not isinstance(artifacts, dict) or not isinstance(sizes, dict):
        raise SiteCandidateError("build state is missing or invalid")
    root = site_root.resolve()
    actual: dict[str, tuple[str, int]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            actual[path.relative_to(root).as_posix()] = (
                hashlib.sha256(content).hexdigest(),
                len(content),
            )
    expected = {
        str(relative): (str(sha), int(sizes[relative]))
        for relative, sha in artifacts.items()
        if (
            relative in sizes
            and isinstance(sha, str)
            and isinstance(sizes[relative], int)
        )
    }
    if expected != actual:
        raise SiteCandidateError("dist/site does not match build state")


def candidate_content_hash(site_root: Path) -> str:
    """Return the validated tree hash without requiring a Git checkout."""
    return validate_site(site_root).identity.content_hash


def _public_quarters(root: Path, files: set[str]) -> tuple[str, ...]:
    try:
        archive = json.loads((root / "data" / "archive-index.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SiteCandidateError("archive index is invalid") from error
    values = archive.get("quarters") if isinstance(archive, dict) else None
    if not isinstance(values, list):
        raise SiteCandidateError("archive index is invalid")
    quarters: list[str] = []
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("quarter"), str):
            raise SiteCandidateError("archive index quarter is invalid")
        quarter = item["quarter"]
        if not re.fullmatch(r"\d{4}-(?:01|04|07|10)", quarter):
            raise SiteCandidateError("archive index quarter is invalid")
        if (
            f"{quarter}/index.html" in files
            and f"data/quarters/{quarter}.json" in files
            and f"data/offline/{quarter}.json" in files
        ):
            quarters.append(quarter)
    return tuple(sorted(set(quarters)))


def _validate_path(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or set(path.parts) & _FORBIDDEN_PARTS:
        raise SiteCandidateError("formal site contains an unsafe path")
    if path.suffix.lower() in _FORBIDDEN_SUFFIXES:
        raise SiteCandidateError("formal site contains a database")
    if path.name.lower() == ".env" or _TOKEN_NAME.search(path.name):
        raise SiteCandidateError("formal site contains a secret-like file")
    if set(part.lower() for part in path.parts) & _ROLE_PARTS:
        raise SiteCandidateError("formal site contains legacy role media")


def _validate_content(relative: str, content: bytes) -> None:
    if PurePosixPath(relative).suffix.lower() not in _TEXT_SUFFIXES:
        return
    if b"Traceback (most recent call last)" in content or _STACK_TRACE.search(
        content.decode("utf-8", errors="ignore")
    ):
        raise SiteCandidateError("formal site contains a debug stack trace")
    text = content.decode("utf-8", errors="ignore")
    if _ABSOLUTE_WINDOWS.search(text) or _ABSOLUTE_DRIVE.search(text):
        raise SiteCandidateError("formal site contains an absolute Windows path")
