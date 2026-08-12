"""Deterministic metadata used by the separate offline publishing command."""

from bgm_side_b.release.manifest import (
    FileEntry,
    build_snapshot_manifest,
    candidate_content_hash,
    index_candidate,
)
from bgm_side_b.release.site_candidate import (
    CandidateIdentity,
    SiteCandidate,
    SiteCandidateError,
    validate_build_state,
    validate_site,
)

__all__ = [
    "FileEntry",
    "build_snapshot_manifest",
    "candidate_content_hash",
    "index_candidate",
    "CandidateIdentity",
    "SiteCandidate",
    "SiteCandidateError",
    "validate_build_state",
    "validate_site",
]
