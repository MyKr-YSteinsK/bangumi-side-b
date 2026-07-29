"""Deterministic metadata used by the separate offline publishing command."""

from bgm_side_b.release.manifest import (
    FileEntry,
    build_snapshot_manifest,
    candidate_content_hash,
    index_candidate,
)

__all__ = [
    "FileEntry",
    "build_snapshot_manifest",
    "candidate_content_hash",
    "index_candidate",
]
