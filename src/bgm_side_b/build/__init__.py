"""Deterministic, read-only data models for static archive builds."""

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
    "BuildDataError",
    "BuildProjection",
    "BuildQueries",
    "OfflineManifestProjection",
    "ProjectionError",
    "QuarterProjection",
    "SubjectProjection",
    "YearCatalogProjection",
    "json_bytes",
    "project_archive_index",
    "project_offline_manifest",
    "project_quarter",
    "project_year",
]
