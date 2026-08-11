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
from bgm_side_b.build.serve import (
    DEFAULT_BASE_PATH,
    ServeError,
    create_preview_server,
    serve_site,
)
from bgm_side_b.build.site_builder import (
    BuildBlocked,
    BuildError,
    SiteBuildRun,
    UnifiedSiteBuilder,
)
from bgm_side_b.build.site_projection import (
    ArchiveFacts,
    ArchiveFactsReader,
    ArchiveIndexProjection,
    ProjectionError,
    QuarterProjection,
    SubjectProjection,
    YearCatalogProjection,
    json_bytes,
    project_archive_index,
    project_quarter,
    project_year,
)
from bgm_side_b.build.writer import (
    ArtifactPlan,
    ArtifactSpec,
    BuildBlockedError,
    IncrementalSiteWriter,
    PatchResult,
    SiteRecoveryError,
    SiteWriteError,
)

__all__ = [
    "ArchiveFacts",
    "ArchiveFactsReader",
    "ArchiveIndexProjection",
    "ArtifactPlan",
    "ArtifactSpec",
    "BuildState",
    "BuildBlockedError",
    "BuildBlocked",
    "BuildDataError",
    "BuildProjection",
    "BuildQueries",
    "DEFAULT_BASE_PATH",
    "BuildError",
    "IncrementalSiteWriter",
    "PatchResult",
    "ProjectionError",
    "QuarterProjection",
    "SubjectProjection",
    "SiteWriteError",
    "SiteRecoveryError",
    "SiteBuildRun",
    "ServeError",
    "UnifiedSiteBuilder",
    "create_preview_server",
    "YearCatalogProjection",
    "DirtySet",
    "assign_fingerprints",
    "derive_dirty_set",
    "fingerprint",
    "json_bytes",
    "project_archive_index",
    "project_quarter",
    "project_year",
    "read_build_state",
    "shared_fingerprint",
    "serve_site",
]
