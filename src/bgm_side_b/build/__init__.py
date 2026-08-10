"""Deterministic, read-only data models for static archive builds."""

from bgm_side_b.build.fingerprint import (
    BuildState,
    DirtySet,
    assign_fingerprints,
    derive_dirty_set,
    fingerprint,
    read_build_state,
    shared_fingerprint,
)
from bgm_side_b.build.projection import BuildProjection
from bgm_side_b.build.queries import BuildDataError, BuildQueries
from bgm_side_b.build.site_projection import (
    ArchiveFacts,
    ArchiveFactsReader,
    ArchiveIndexProjection,
    OfflineManifestProjection,
    ProjectionError,
    QuarterProjection,
    SubjectProjection,
    YearCatalogProjection,
    json_bytes,
    project_archive_index,
    project_offline_manifest,
    project_quarter,
    project_year,
)

__all__ = [
    "ArchiveFacts",
    "ArchiveFactsReader",
    "ArchiveIndexProjection",
    "BuildState",
    "BuildDataError",
    "BuildProjection",
    "BuildQueries",
    "OfflineManifestProjection",
    "ProjectionError",
    "QuarterProjection",
    "SubjectProjection",
    "YearCatalogProjection",
    "DirtySet",
    "assign_fingerprints",
    "derive_dirty_set",
    "fingerprint",
    "json_bytes",
    "project_archive_index",
    "project_offline_manifest",
    "project_quarter",
    "project_year",
    "read_build_state",
    "shared_fingerprint",
]
