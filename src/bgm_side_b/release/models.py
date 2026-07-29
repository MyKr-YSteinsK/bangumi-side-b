"""Small serialisable release-domain values."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FileEntry:
    """One immutable candidate file without a local filesystem path."""

    url: str
    sha256: str
    size_bytes: int
    content_type: str
    category: str

    def payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SnapshotManifest:
    """The complete cacheable file list for one release version."""

    release_version: str
    app_version: str
    deployment_path: str
    generated_at: str
    files: tuple[FileEntry, ...]
    content_hash: str

    def payload(self) -> dict[str, object]:
        files = [entry.payload() for entry in self.files]
        return {
            "schema": 1,
            "release_version": self.release_version,
            "app_version": self.app_version,
            "deployment_path": self.deployment_path,
            "generated_at": self.generated_at,
            "entry_count": len(files),
            "total_bytes": sum(entry.size_bytes for entry in self.files),
            "content_hash": self.content_hash,
            "files": files,
        }
