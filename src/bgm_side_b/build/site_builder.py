# ruff: noqa: E501
"""Offline orchestration for the single deterministic ``dist/site`` tree."""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from bgm_side_b.build.fingerprint import (
    BuildState,
    DirtySet,
    assign_fingerprints,
    derive_dirty_set,
    fingerprint,
    read_build_state,
    shared_fingerprint,
)
from bgm_side_b.build.site_projection import (
    PROJECTION_VERSION,
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
    SiteWriteError,
)
from bgm_side_b.config import TagRules
from bgm_side_b.database import Database
from bgm_side_b.domain import Quarter
from bgm_side_b.progress import NullProgressReporter, ProgressReporter


class BuildError(RuntimeError):
    """Raised when a complete offline site cannot be produced safely."""


class BuildBlocked(BuildError):
    """Raised when a filesystem lock prevents an atomic site patch."""


APP_CSS_FALLBACK = """
:root { font-family: system-ui, sans-serif; color: #17201d; background: #f5f1e8; }
body { margin: 0 auto; max-width: 1100px; padding: 2rem; }
a { color: #682337; }
.subject-card {
  border: 1px solid #c9c6bb;
  background: #fffdf8;
  padding: 1rem;
  margin: .5rem 0;
}
.subject-card[hidden] { display: none; }
[data-media-mode="movie"] { border-color: #c95c32; }
nav { display: flex; gap: 1rem; flex-wrap: wrap; }
button { padding: .4rem .7rem; }
#detail-mount { border-top: 1px solid #c9c6bb; margin-top: 1rem; padding-top: 1rem; }
"""
APP_JS = """(() => {
  const root = document.querySelector('[data-quarter]');
  if (!root) return;
  const quarter = root.dataset.quarter;
  const mount = document.querySelector('#detail-mount');
  const dataUrl = `../data/quarters/${quarter}.json`;
  let payload = null;
  fetch(dataUrl)
    .then((response) => response.json())
    .then((value) => { payload = value; });
  document.querySelectorAll('[data-subject-id]').forEach((card) => {
    card.addEventListener('click', () => {
      if (!payload || !mount) return;
      const id = Number(card.dataset.subjectId);
      const groups = [
        payload.tv.premiere,
        payload.tv.continuing,
        payload.movie.premiere,
      ];
      const item = groups.flat().find((entry) => entry.subject_id === id);
      if (!item) return;
      mount.hidden = false;
      mount.textContent = item.display_summary || item.preferred_title;
    });
  });
})();
"""
_HREF_RE = re.compile(r"(?:href|src)=\"([^\"]+)\"")


class UnifiedSiteBuilder:
    """Build the one public site from the clean SQLite fact store."""

    def __init__(
        self,
        root: Path,
        database: Database,
        tag_rules: TagRules,
        *,
        workspace_directory: Path | None = None,
        site_directory: Path | None = None,
        reports_directory: Path | None = None,
        excluded_subject_ids: frozenset[int] = frozenset(),
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.root = root.resolve()
        self.database = database
        self.tag_rules = tag_rules
        self.workspace_directory = (
            workspace_directory or self.root / "workspace"
        ).resolve()
        self.site_directory = (site_directory or self.root / "dist" / "site").resolve()
        self.reports_directory = (
            reports_directory or self.workspace_directory / "reports"
        ).resolve()
        self.excluded_subject_ids = excluded_subject_ids
        self.reporter = reporter or NullProgressReporter()

    @property
    def state_path(self) -> Path:
        return self.workspace_directory / "build-state.json"

    def build(self, quarter: Quarter | None = None) -> SiteBuildRun:
        """Build all managed quarters, optionally validating a requested quarter."""
        started = monotonic()
        scope_label = "all" if quarter is None else _quarter_label(quarter)
        self.reporter.start(
            stage="scope",
            message="building unified static site",
            current=scope_label,
        )
        try:
            facts = self._read_facts()
            previous = read_build_state(self.state_path)
            managed_quarters = tuple(
                sorted(_quarter_label(item) for item in facts.state_by_quarter)
            )
            appearance_quarters = {
                _quarter_label(item) for item in facts.by_quarter
            }
            available = managed_quarters
            if quarter is not None and _quarter_label(quarter) not in available:
                if previous is None or _quarter_label(quarter) not in previous.quarters:
                    raise BuildError(
                        f"quarter is not managed: {_quarter_label(quarter)}"
                    )
            blocked = _blocked_quarters(facts)
            eligible = tuple(label for label in managed_quarters if label not in blocked)
            retained_blocked, omitted = self._retained_quarters(previous, blocked)
            projection_labels = eligible
            retained_scope: tuple[str, ...] = ()
            if quarter is not None and previous is not None:
                target = _quarter_label(quarter)
                other_eligible = tuple(label for label in eligible if label != target)
                reusable, missing = self._retained_quarters(previous, other_eligible)
                if not missing:
                    projection_labels = (target,) if target in eligible else ()
                    retained_scope = reusable
            retained = tuple(sorted(set(retained_blocked) | set(retained_scope)))
            unmanaged = tuple(sorted(appearance_quarters - set(managed_quarters)))
            quarter_projections = self._project_quarters(facts, projection_labels)
            years = self._project_years(quarter_projections)
            archive = project_archive_index(quarter_projections)
            css, js = self._shared_assets()
            shared = shared_fingerprint(
                stylesheet=css,
                script=js,
                tag_rules=self.tag_rules,
                excluded_subject_ids=self.excluded_subject_ids,
            )
            quarter_projections, years, archive, current = assign_fingerprints(
                quarter_projections,
                years,
                archive,
                shared=shared,
            )
            years, archive = self._merge_retained_indexes(years, archive, retained)
            current = _merge_retained_state(current, previous, retained, years, archive)
            dirty = derive_dirty_set(
                previous,
                current,
                available_quarters=tuple(item.quarter for item in quarter_projections),
                requested_quarters=(
                    None if quarter is None else (_quarter_label(quarter),)
                ),
            )
            dirty = replace(
                dirty,
                skipped_quarters=tuple(
                    sorted(set(dirty.skipped_quarters) | set(retained) | set(blocked))
                ),
            )
            plan = self._plan_site(
                facts,
                quarter_projections,
                years,
                archive,
                css,
                js,
                retained=retained,
                previous=previous,
                dirty=dirty,
            )
            _validate_plan_contract(
                plan,
                quarter_projections,
                archive,
                self.excluded_subject_ids,
            )
            write_state = replace(
                current,
                artifacts={} if previous is None else previous.artifacts,
                artifact_sizes={} if previous is None else previous.artifact_sizes,
            )
            writer = IncrementalSiteWriter(
                self.site_directory,
                self.workspace_directory,
            )
            result = writer.apply(
                plan,
                write_state,
                validate_staged=lambda mapping: self._validate_desired(mapping, plan),
                validate_final=lambda site: self._validate_final(
                    site, plan, full=previous is None
                ),
            )
            warnings = tuple(
                warning
                for item in quarter_projections
                for warning in item.warnings
            )
            warnings += tuple(
                f"quarter {label} is blocked; retained last-known-good artifacts"
                for label in retained
            )
            warnings += tuple(
                f"quarter {label} is blocked; no last-known-good artifacts available"
                for label in omitted
            )
            warnings += tuple(
                f"quarter {label} is not managed by sync_state; omitted from public site"
                for label in unmanaged
            )
            return self._write_report(
                scope_label,
                dirty,
                result,
                warnings,
                (),
                started,
                current,
                retained_quarters=retained,
                blocked_new_quarters=omitted,
            )
        except BuildBlockedError as error:
            raise BuildBlocked(str(error)) from error
        except (ProjectionError, SiteWriteError) as error:
            raise BuildError(str(error)) from error

    def _read_facts(self) -> ArchiveFacts:
        self.reporter.stage(stage="database", message="reading SQLite facts")
        try:
            return ArchiveFactsReader(
                self.database, self.workspace_directory
            ).read(self.excluded_subject_ids)
        except ProjectionError:
            raise

    def _project_quarters(
        self, facts: ArchiveFacts, eligible: tuple[str, ...]
    ) -> tuple[QuarterProjection, ...]:
        allowed = set(eligible)
        projections: list[QuarterProjection] = []
        for quarter in sorted(facts.state_by_quarter):
            label = _quarter_label(quarter)
            if label not in allowed:
                continue
            self.reporter.progress(
                stage="projection", current=label, completed=len(projections) + 1
            )
            projections.append(
                project_quarter(
                    facts,
                    quarter,
                    self.tag_rules,
                    self.workspace_directory,
                )
            )
        return tuple(projections)

    @staticmethod
    def _project_years(
        quarters: tuple[QuarterProjection, ...],
    ) -> tuple[YearCatalogProjection, ...]:
        grouped: defaultdict[int, list[QuarterProjection]] = defaultdict(list)
        for quarter in quarters:
            grouped[int(quarter.quarter[:4])].append(quarter)
        return tuple(
            project_year(year, tuple(values))
            for year, values in sorted(grouped.items())
        )

    def _retained_quarters(
        self, previous: BuildState | None, blocked: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Separate blocked quarters with a usable last-good tree from omissions."""
        if previous is None:
            return (), tuple(sorted(blocked))
        retained: list[str] = []
        omitted: list[str] = []
        for label in sorted(blocked):
            if self._retained_quarter_is_complete(previous, label):
                retained.append(label)
            else:
                omitted.append(label)
        return tuple(retained), tuple(omitted)

    def _retained_quarter_is_complete(
        self, previous: BuildState, label: str
    ) -> bool:
        """Require the complete last-good dependency closure before retaining it."""
        if label not in previous.quarters:
            return False
        core = (
            f"{label}/index.html",
            f"data/quarters/{label}.json",
            f"data/offline/{label}.json",
        )
        for relative in core:
            path = self.site_directory / Path(relative)
            expected_size = previous.artifact_sizes.get(relative)
            if (
                relative not in previous.artifacts
                or expected_size is None
                or not path.is_file()
            ):
                return False
            try:
                if path.stat().st_size != expected_size:
                    return False
            except OSError:
                return False

        quarter = _read_required_site_json(
            self.site_directory / "data" / "quarters" / f"{label}.json"
        )
        manifest = _read_required_site_json(
            self.site_directory / "data" / "offline" / f"{label}.json"
        )
        archive = _read_required_site_json(
            self.site_directory / "data" / "archive-index.json"
        )
        year = _read_required_site_json(
            self.site_directory / "data" / "catalog" / f"{label[:4]}.json"
        )
        if None in (quarter, manifest, archive, year):
            return False
        assert quarter is not None
        assert manifest is not None
        assert archive is not None
        assert year is not None

        archive_entries = archive.get("quarters")
        year_records = year.get("records")
        if not isinstance(archive_entries, list) or not isinstance(year_records, list):
            return False
        if not any(
            isinstance(item, dict) and item.get("quarter") == label
            for item in archive_entries
        ):
            return False
        retained_records = tuple(
            item
            for item in year_records
            if isinstance(item, dict) and item.get("quarter") == label
        )
        if len(retained_records) != len(_quarter_items(quarter)):
            return False

        resources = manifest.get("resources")
        if manifest.get("quarter") != label or not isinstance(resources, list):
            return False
        cover_resources: dict[str, Mapping[str, object]] = {}
        for item in resources:
            if not isinstance(item, dict):
                return False
            relative = item.get("url")
            if isinstance(relative, str) and relative.startswith("covers/"):
                cover_resources[relative] = item
        expected_covers = {
            cover.split("?", 1)[0]
            for item in _quarter_items(quarter)
            if isinstance((cover := item.get("cover_url")), str)
        }
        if any(
            re.fullmatch(r"covers/[1-9][0-9]*\.webp", relative) is None
            for relative in expected_covers
        ) or expected_covers != set(cover_resources):
            return False
        for relative, item in cover_resources.items():
            path = self.site_directory / Path(relative)
            content_hash = item.get("content_hash")
            size = item.get("size_bytes")
            if (
                not isinstance(content_hash, str)
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or previous.artifacts.get(relative) != content_hash
                or previous.artifact_sizes.get(relative) != size
                or not path.is_file()
            ):
                return False
            try:
                if path.stat().st_size != size:
                    return False
            except OSError:
                return False
        return True

    def _merge_retained_indexes(
        self,
        years: tuple[YearCatalogProjection, ...],
        archive: ArchiveIndexProjection,
        retained: tuple[str, ...],
    ) -> tuple[tuple[YearCatalogProjection, ...], ArchiveIndexProjection]:
        """Merge retained last-good index records without reading uncertain facts."""
        if not retained:
            return years, archive
        old_archive = _read_site_json(self.site_directory / "data" / "archive-index.json")
        old_entries = {
            str(item.get("quarter")): item
            for item in old_archive.get("quarters", [])
            if isinstance(item, dict) and isinstance(item.get("quarter"), str)
        }
        merged_entries = {
            str(item.get("quarter")): item
            for item in archive.quarters
            if isinstance(item, dict) and isinstance(item.get("quarter"), str)
        }
        for label in retained:
            entry = old_entries.get(label)
            if entry is not None:
                merged_entries[label] = entry
        ordered_entries = tuple(merged_entries[label] for label in sorted(merged_entries))
        old_year_records: dict[int, tuple[dict[str, object], ...]] = {}
        old_year_revisions: dict[int, str] = {}
        for year in sorted({int(label[:4]) for label in retained}):
            payload = _read_site_json(
                self.site_directory / "data" / "catalog" / f"{year:04d}.json"
            )
            records = tuple(
                item
                for item in payload.get("records", [])
                if isinstance(item, dict) and str(item.get("quarter")) in retained
            )
            old_year_records[year] = records
            revision = payload.get("revision")
            if isinstance(revision, str):
                old_year_revisions[year] = revision
        current_by_year = {item.year: item for item in years}
        merged_years: list[YearCatalogProjection] = []
        for year in sorted(set(current_by_year) | set(old_year_records)):
            current = current_by_year.get(year)
            retained_records = old_year_records.get(year, ())
            if current is None:
                merged_years.append(
                    YearCatalogProjection(
                        year,
                        retained_records,
                        old_year_revisions.get(year, ""),
                    )
                )
                continue
            if not retained_records:
                merged_years.append(current)
                continue
            records = tuple(sorted(
                (*retained_records, *current.records),
                key=lambda item: (str(item.get("quarter", "")), int(item.get("id", 0)), str(item.get("appearance", ""))),
            ))
            merged_years.append(
                YearCatalogProjection(
                    year,
                    records,
                    fingerprint(
                        {
                            "projection": PROJECTION_VERSION,
                            "year": year,
                            "records": records,
                            "quarters": sorted({str(item.get("quarter")) for item in records}),
                        }
                    ),
                )
            )
        merged_year_labels = tuple(item.year for item in merged_years)
        latest = sorted(merged_entries)[-1] if merged_entries else None
        old_shape = {
            "years": old_archive.get("years"),
            "quarters": old_archive.get("quarters"),
            "latest_quarter": old_archive.get("latest_quarter"),
        }
        new_shape = {
            "years": list(merged_year_labels),
            "quarters": list(ordered_entries),
            "latest_quarter": latest,
        }
        revision = old_archive.get("revision") if old_shape == new_shape else None
        archive_revision = (
            revision
            if isinstance(revision, str)
            else fingerprint(
                {
                    "projection": PROJECTION_VERSION,
                    **new_shape,
                }
            )
        )
        return tuple(merged_years), ArchiveIndexProjection(
            merged_year_labels,
            ordered_entries,
            latest,
            archive_revision,
        )

    def _shared_assets(self) -> tuple[bytes, bytes]:
        css_path = self.root / "static" / "css" / "site.css"
        css = css_path.read_bytes() if css_path.is_file() else APP_CSS_FALLBACK.encode()
        js_path = self.root / "static" / "js" / "app.js"
        js = js_path.read_bytes() if js_path.is_file() else APP_JS.encode()
        return css, js

    def _plan_site(
        self,
        facts: ArchiveFacts,
        quarters: tuple[QuarterProjection, ...],
        years: tuple[YearCatalogProjection, ...],
        archive: ArchiveIndexProjection,
        css: bytes,
        js: bytes,
        *,
        retained: tuple[str, ...],
        previous: BuildState | None,
        dirty: DirtySet,
    ) -> ArtifactPlan:
        """Plan only affected artifacts; content is materialized for dirty paths."""
        specs: dict[str, ArtifactSpec] = {}
        previous_artifacts = {} if previous is None else dict(previous.artifacts)
        previous_sizes = {} if previous is None else dict(previous.artifact_sizes)

        def needs(path: str, scope_dirty: bool) -> bool:
            return (
                previous is None
                or scope_dirty
                or path not in previous_artifacts
                or not (self.site_directory / Path(path)).is_file()
            )

        def generated(
            path: str,
            kind: str,
            producer: Callable[[], bytes],
            scope_dirty: bool,
        ) -> None:
            if not needs(path, scope_dirty):
                specs[path] = ArtifactSpec(
                    path,
                    previous_artifacts[path],
                    previous_sizes.get(path),
                    kind,
                )
                return
            value = producer()
            specs[path] = ArtifactSpec(
                path,
                hashlib.sha256(value).hexdigest(),
                len(value),
                kind,
                value,
            )

        generated("assets/app.css", "shared", lambda: css, dirty.shared_dirty)
        generated("assets/app.js", "shared", lambda: js, dirty.shared_dirty)
        generated(
            "index.html",
            "root",
            lambda: _root_html(archive),
            dirty.archive_dirty or dirty.shared_dirty,
        )
        generated(
            "archive/index.html",
            "archive-shell",
            lambda: _archive_html(archive),
            dirty.archive_dirty or dirty.shared_dirty,
        )
        generated(
            "settings/index.html",
            "shared-shell",
            _settings_html,
            dirty.shared_dirty,
        )
        generated(
            "data/archive-index.json",
            "archive-index",
            lambda: json_bytes(archive.to_dict()),
            dirty.archive_dirty,
        )
        for year in years:
            generated(
                f"data/catalog/{year.year:04d}.json",
                "year-catalog",
                lambda year=year: json_bytes(year.to_dict()),
                str(year.year) in dirty.dirty_years,
            )

        subject_by_id = {subject.subject_id: subject for subject in facts.subjects}
        quarter_dirty = set(dirty.dirty_quarters)
        for quarter in quarters:
            label = quarter.quarter
            generated(
                f"{label}/index.html",
                "quarter-html",
                lambda quarter=quarter: _quarter_html(quarter),
                label in quarter_dirty or dirty.shared_dirty,
            )
            generated(
                f"data/quarters/{label}.json",
                "quarter-json",
                lambda quarter=quarter: json_bytes(quarter.to_dict()),
                label in quarter_dirty or dirty.shared_dirty,
            )
            for item in (
                *quarter.tv_premiere,
                *quarter.tv_continuing,
                *quarter.movie_premiere,
            ):
                if not item.cover_hash:
                    continue
                subject = subject_by_id.get(item.subject_id)
                if subject is None or subject.cover is None:
                    continue
                relative = f"covers/{item.subject_id}.webp"
                if relative in specs:
                    continue
                cover = subject.cover
                reusable = (
                    relative in previous_artifacts
                    and previous_artifacts[relative] == cover.content_hash
                    and (self.site_directory / Path(relative)).is_file()
                    and (
                        previous_sizes.get(relative) is None
                        or previous_sizes[relative] == cover.size_bytes
                    )
                )
                specs[relative] = ArtifactSpec(
                    relative,
                    cover.content_hash,
                    cover.size_bytes,
                    "cover",
                    None if reusable else cover.source_path,
                )
            manifest_path = f"data/offline/{label}.json"
            generated(
                manifest_path,
                "offline-manifest",
                lambda quarter=quarter: _offline_manifest_bytes(quarter, specs),
                label in quarter_dirty or dirty.shared_dirty,
            )
        for label in retained:
            for relative in (
                f"{label}/index.html",
                f"data/quarters/{label}.json",
                f"data/offline/{label}.json",
            ):
                if relative in specs:
                    continue
                specs[relative] = _reused_spec(
                    relative,
                    previous_artifacts,
                    previous_sizes,
                )
            payload = _read_site_json(
                self.site_directory / "data" / "quarters" / f"{label}.json"
            )
            for item in _quarter_items(payload):
                cover = item.get("cover_url")
                if not isinstance(cover, str):
                    continue
                relative = cover.split("?", 1)[0]
                if relative not in specs:
                    specs[relative] = _reused_spec(
                        relative,
                        previous_artifacts,
                        previous_sizes,
                    )
        return ArtifactPlan(specs)

    def _validate_desired(
        self, desired: Mapping[str, bytes], plan: ArtifactPlan
    ) -> None:
        if len(desired) == len(plan.specs):
            _validate_site_mapping(desired, self.excluded_subject_ids)
            return
        _validate_dirty_mapping(
            desired,
            plan,
            self.site_directory,
            self.excluded_subject_ids,
        )

    def _validate_final(
        self, site: Path, plan: ArtifactPlan, *, full: bool = False
    ) -> None:
        if full:
            mapping = {
                path.relative_to(site).as_posix(): path.read_bytes()
                for path in site.rglob("*")
                if path.is_file()
            }
            _validate_site_mapping(mapping, self.excluded_subject_ids)
            return
        _validate_scoped_site(site, plan, self.excluded_subject_ids)

    def _write_report(
        self,
        scope: str,
        dirty: DirtySet,
        result: PatchResult,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        started: float,
        state: BuildState | None = None,
        *,
        retained_quarters: tuple[str, ...] = (),
        blocked_new_quarters: tuple[str, ...] = (),
    ) -> SiteBuildRun:
        self.reports_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = self.reports_directory / f"build-{scope}-{stamp}.json"
        report = {
            "scope": scope,
            "dirty_quarters": list(dirty.dirty_quarters),
            "skipped_quarters": list(dirty.skipped_quarters),
            "dirty_years": list(dirty.dirty_years),
            "archive_dirty": dirty.archive_dirty,
            "shared_dirty": dirty.shared_dirty,
            "written_artifacts_count": len(result.written),
            "written_files_sample": list(result.written[:20]),
            "deleted_artifacts_count": len(result.deleted),
            "deleted_files_sample": list(result.deleted[:20]),
            "reused_files_sample": list(result.reused[:20]),
            "planned_dirty_files": len(result.staged),
            "generated_small_files": result.generated_small_files,
            "cover_files_read": result.cover_files_read,
            "cover_files_copied": result.cover_files_copied,
            "stale_files_deleted": len(result.deleted),
            "reused_artifacts_count": len(result.reused),
            "retained_quarters": list(retained_quarters),
            "blocked_new_quarters": list(blocked_new_quarters),
            "warnings": list(warnings),
            "errors": list(errors),
            "fingerprints": {
                "shared": state.shared if state else None,
                "quarters": dict(state.quarters) if state else {},
                "years": dict(state.years) if state else {},
                "archive": state.archive if state else None,
            },
            "duration_seconds": round(monotonic() - started, 3),
        }
        report_path.write_bytes(json_bytes(report))
        self.reporter.complete(
            stage="summary",
            message="unified static site build complete",
            counters={
                "written": len(result.written),
                "deleted": len(result.deleted),
                "skipped": len(dirty.skipped_quarters),
            },
        )
        return SiteBuildRun(scope, report_path, dirty, result, warnings, errors)


class SiteBuildRun:
    """Summary returned by one offline build invocation."""

    def __init__(
        self,
        scope: str,
        report_path: Path,
        dirty: DirtySet,
        patch: PatchResult,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> None:
        self.scope = scope
        self.report_path = report_path
        self.dirty = dirty
        self.patch = patch
        self.warnings = warnings
        self.errors = errors


def _validate_site_mapping(
    mapping: Mapping[str, bytes], excluded_subject_ids: frozenset[int]
) -> None:
    required = {
        "index.html",
        "archive/index.html",
        "settings/index.html",
        "assets/app.css",
        "assets/app.js",
        "data/archive-index.json",
    }
    missing = sorted(required - set(mapping))
    if missing:
        raise BuildError(f"required site artifact is missing: {missing[0]}")
    if any(
        item.startswith(("subjects/", "episodes/", "characters/", "persons/"))
        for item in mapping
    ):
        raise BuildError("forbidden detail or entity artifact exists")
    archive = _load_json(mapping, "data/archive-index.json")
    quarter_labels = {
        item["quarter"]
        for item in archive.get("quarters", [])
        if isinstance(item, dict)
    }
    for path, content in mapping.items():
        if path.startswith("data/quarters/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            label = payload.get("quarter")
            if label not in quarter_labels:
                raise BuildError("quarter data is not listed in archive index")
            for item in _quarter_items(payload):
                subject_id = item.get("subject_id")
                if subject_id in excluded_subject_ids:
                    raise BuildError("blacklisted subject appears in site output")
                cover = item.get("cover_url")
                if cover:
                    cover_path = str(cover).split("?", 1)[0]
                    if cover_path not in mapping:
                        raise BuildError(f"cover artifact is missing: {cover_path}")
    for path, content in mapping.items():
        if path.startswith("data/offline/") and path.endswith(".json"):
            payload = _load_json(mapping, path)
            for resource in payload.get("resources", []):
                if not isinstance(resource, dict):
                    raise BuildError("offline resource entry is invalid")
                url = resource.get("url")
                if not isinstance(url, str) or url.startswith(("http:", "https:", "/")):
                    raise BuildError("offline manifest contains a non-local URL")
                if url not in mapping:
                    raise BuildError(f"offline resource is missing: {url}")
                expected_hash = resource.get("content_hash")
                expected_size = resource.get("size_bytes")
                if expected_hash != hashlib.sha256(mapping[url]).hexdigest():
                    raise BuildError(f"offline resource hash is invalid: {url}")
                if expected_size != len(mapping[url]):
                    raise BuildError(f"offline resource size is invalid: {url}")
    for path, content in mapping.items():
        if path.endswith(".html"):
            for href in _HREF_RE.findall(content.decode("utf-8")):
                if href.startswith(("https://bgm.tv/subject/", "#", "mailto:")):
                    continue
                target = href.split("?", 1)[0].split("#", 1)[0]
                if not target or target.startswith("/"):
                    raise BuildError("site HTML contains an unsafe absolute URL")
                candidate = Path(path).parent / Path(target)
                normalized = posixpath.normpath(candidate.as_posix())
                if normalized == ".":
                    normalized = "index.html"
                if normalized not in mapping:
                    raise BuildError(f"site HTML references missing artifact: {target}")


def _validate_dirty_mapping(
    mapping: Mapping[str, bytes],
    plan: ArtifactPlan,
    site: Path,
    excluded_subject_ids: frozenset[int],
) -> None:
    """Validate only newly materialized files and their cheap dependencies."""
    for path, content in mapping.items():
        spec = plan.specs.get(path)
        if spec is None:
            raise BuildError(f"artifact is not in the current plan: {path}")
        if spec.kind == "cover":
            if spec.content_hash != hashlib.sha256(content).hexdigest():
                raise BuildError(f"cover artifact hash is invalid: {path}")
            if spec.size_bytes is not None and spec.size_bytes != len(content):
                raise BuildError(f"cover artifact size is invalid: {path}")
        if path.endswith(".html"):
            _validate_html_links(path, content, plan, site)
        if path.startswith("data/quarters/") and path.endswith(".json"):
            _validate_quarter_payload(path, content, plan, site, excluded_subject_ids)
        if path.startswith("data/catalog/") and path.endswith(".json"):
            _validate_year_payload(path, content, plan, site)
        if path == "data/archive-index.json":
            _validate_archive_payload(content, plan, site)
        if path.startswith("data/offline/") and path.endswith(".json"):
            _validate_offline_payload(content, mapping, plan, site)


def _validate_scoped_site(
    site: Path, plan: ArtifactPlan, excluded_subject_ids: frozenset[int]
) -> None:
    """Check retained artifacts by metadata and dirty artifacts by their sources."""
    source_bytes = {
        path: spec.source
        for path, spec in plan.specs.items()
        if isinstance(spec.source, bytes)
    }
    _validate_dirty_mapping(source_bytes, plan, site, excluded_subject_ids)
    for relative, spec in plan.specs.items():
        target = site / Path(relative)
        if not target.is_file():
            raise BuildError(f"planned artifact is missing: {relative}")
        if spec.size_bytes is not None:
            try:
                if target.stat().st_size != spec.size_bytes:
                    raise BuildError(f"planned artifact size is invalid: {relative}")
            except OSError as error:
                raise BuildError(f"planned artifact cannot be inspected: {relative}") from error


def _validate_html_links(
    path: str, content: bytes, plan: ArtifactPlan, site: Path
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildError(f"generated HTML is not UTF-8: {path}") from error
    for href in _HREF_RE.findall(text):
        if href.startswith(("https://bgm.tv/subject/", "#", "mailto:")):
            continue
        target = href.split("?", 1)[0].split("#", 1)[0]
        if not target or target.startswith("/"):
            raise BuildError("site HTML contains an unsafe absolute URL")
        candidate = Path(path).parent / Path(target)
        normalized = posixpath.normpath(candidate.as_posix())
        if normalized == ".":
            normalized = "index.html"
        if normalized not in plan.specs and not (site / Path(normalized)).is_file():
            raise BuildError(f"site HTML references missing artifact: {target}")


def _validate_quarter_payload(
    path: str,
    content: bytes,
    plan: ArtifactPlan,
    site: Path,
    excluded_subject_ids: frozenset[int],
) -> None:
    payload = _load_json({path: content}, path)
    expected = Path(path).stem
    if payload.get("quarter") != expected:
        raise BuildError(f"quarter payload label is invalid: {path}")
    movie = payload.get("movie")
    if isinstance(movie, dict) and movie.get("continuing"):
        raise BuildError(f"movie continuing appearance in {path}")
    for item in _quarter_items(payload):
        subject_id = item.get("subject_id")
        if subject_id in excluded_subject_ids:
            raise BuildError("blacklisted subject appears in site output")
        cover = item.get("cover_url")
        if isinstance(cover, str):
            relative = cover.split("?", 1)[0]
            if relative not in plan.specs and not (site / Path(relative)).is_file():
                raise BuildError(f"cover artifact is missing: {relative}")


def _validate_year_payload(
    path: str, content: bytes, plan: ArtifactPlan, site: Path
) -> None:
    payload = _load_json({path: content}, path)
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise BuildError(f"year catalog records are invalid: {path}")
    for item in records:
        if not isinstance(item, dict):
            raise BuildError(f"year catalog record is invalid: {path}")
        quarter = item.get("quarter")
        if isinstance(quarter, str):
            target = f"data/quarters/{quarter}.json"
            if target not in plan.specs and not (site / Path(target)).is_file():
                raise BuildError(f"year catalog references missing quarter: {quarter}")


def _validate_archive_payload(
    content: bytes, plan: ArtifactPlan, site: Path
) -> None:
    payload = _load_json({"data/archive-index.json": content}, "data/archive-index.json")
    quarters = payload.get("quarters", [])
    if not isinstance(quarters, list):
        raise BuildError("archive index quarters are invalid")
    for item in quarters:
        if not isinstance(item, dict) or not isinstance(item.get("quarter"), str):
            raise BuildError("archive index entry is invalid")
        target = f"data/quarters/{item['quarter']}.json"
        if target not in plan.specs and not (site / Path(target)).is_file():
            raise BuildError(f"archive index references missing quarter: {item['quarter']}")


def _validate_offline_payload(
    content: bytes,
    mapping: Mapping[str, bytes],
    plan: ArtifactPlan,
    site: Path,
) -> None:
    payload = _load_json({"offline.json": content}, "offline.json")
    resources = payload.get("resources", [])
    if not isinstance(resources, list):
        raise BuildError("offline manifest resources are invalid")
    for resource in resources:
        if not isinstance(resource, dict):
            raise BuildError("offline resource entry is invalid")
        url = resource.get("url")
        if not isinstance(url, str) or url.startswith(("http:", "https:", "/")):
            raise BuildError("offline manifest contains a non-local URL")
        if url in mapping:
            data = mapping[url]
            if resource.get("content_hash") != hashlib.sha256(data).hexdigest():
                raise BuildError(f"offline resource hash is invalid: {url}")
            if resource.get("size_bytes") != len(data):
                raise BuildError(f"offline resource size is invalid: {url}")
            continue
        spec = plan.specs.get(url)
        target = site / Path(url)
        if spec is None and not target.is_file():
            raise BuildError(f"offline resource is missing: {url}")
        expected_hash = spec.content_hash if spec is not None else None
        expected_size = spec.size_bytes if spec is not None else None
        if expected_hash is not None and resource.get("content_hash") != expected_hash:
            raise BuildError(f"offline resource hash is invalid: {url}")
        if expected_size is not None and resource.get("size_bytes") != expected_size:
            raise BuildError(f"offline resource size is invalid: {url}")


def _validate_plan_contract(
    plan: ArtifactPlan,
    quarters: tuple[QuarterProjection, ...],
    archive: ArchiveIndexProjection,
    excluded_subject_ids: frozenset[int],
) -> None:
    required = {
        "index.html",
        "archive/index.html",
        "settings/index.html",
        "assets/app.css",
        "assets/app.js",
        "data/archive-index.json",
    }
    missing = sorted(required - set(plan.specs))
    if missing:
        raise BuildError(f"required site artifact is missing: {missing[0]}")
    if any(
        path.startswith(("subjects/", "episodes/", "characters/", "persons/"))
        for path in plan.specs
    ):
        raise BuildError("forbidden detail or entity artifact exists")
    archive_labels = {
        str(item.get("quarter"))
        for item in archive.quarters
        if isinstance(item, dict)
    }
    for quarter in quarters:
        if quarter.quarter not in archive_labels:
            raise BuildError(f"quarter is not listed in archive index: {quarter.quarter}")
        if any(
            item.subject_id in excluded_subject_ids
            for group in (
                quarter.tv_premiere,
                quarter.tv_continuing,
                quarter.movie_premiere,
            )
            for item in group
        ):
            raise BuildError("blacklisted subject appears in site output")
        if quarter.movie_premiere and any(
            item.appearance_kind == "continuing" for item in quarter.movie_premiere
        ):
            raise BuildError(f"movie continuing appearance in {quarter.quarter}")


def _load_json(mapping: Mapping[str, bytes], path: str) -> dict[str, object]:
    try:
        value = json.loads(mapping[path].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"invalid JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise BuildError(f"JSON artifact must be an object: {path}")
    return value


def _quarter_items(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    values: list[Mapping[str, object]] = []
    for group in (payload.get("tv"), payload.get("movie")):
        if not isinstance(group, dict):
            continue
        for records in group.values():
            if isinstance(records, list):
                values.extend(item for item in records if isinstance(item, dict))
    return tuple(values)


def _root_html(archive: ArchiveIndexProjection) -> bytes:
    latest = archive.latest_quarter
    if latest:
        body = (
            f'<meta http-equiv="refresh" content="0;url={latest}/index.html">'
            f'<p class="root-redirect"><a href="{latest}/index.html">'
            f'Open latest quarter {html.escape(latest)}</a></p>'
        )
    else:
        body = '<p class="root-redirect" data-empty-archive>Archive is empty.</p>'
    return _page("Bangumi Side B", body, "assets/app.css", "assets/app.js")


def _archive_html(archive: ArchiveIndexProjection) -> bytes:
    body = (
        _site_header("../index.html", "../archive/index.html", "../settings/index.html", "ARCHIVE")
        + '<main class="archive-page" data-archive-app data-page="archive" data-workspace-mode="scope" '
        'data-archive-index-url="../data/archive-index.json" data-site-root="../">'
        '<section class="archive-intro">'
        '<p class="archive-intro__code">ARCHIVE / INDEX</p>'
        '<div><h1>播出档案</h1><p class="archive-intro__summary">'
        '按季度坐标回看已核验的电视与剧场版资料。</p></div></section>'
        '<section class="archive-selector" aria-label="Archive scope">'
        '<div class="scope-tabs" role="tablist" aria-label="时间范围">'
        '<button type="button" role="tab" data-scope-choice="quarter" aria-selected="true">QUARTER</button>'
        '<button type="button" role="tab" data-scope-choice="year" aria-selected="false">YEAR</button>'
        '<button type="button" role="tab" data-scope-choice="range" aria-selected="false">YEAR RANGE</button>'
        '</div>'
        '<div class="archive-selector__panel" data-archive-quarter-selector role="tabpanel">'
        '<p class="loading-state">正在读取季度索引…</p></div>'
        '<div class="archive-selector__panel" data-archive-year-selector role="tabpanel" hidden>'
        '<label class="field-label" for="archive-year">年份</label>'
        '<select id="archive-year" data-archive-year-select></select></div>'
        '<div class="archive-selector__panel archive-range" data-archive-range-selector role="tabpanel" hidden>'
        '<label class="field-label" for="archive-from">起始年份</label>'
        '<input id="archive-from" data-archive-from inputmode="numeric" type="number">'
        '<label class="field-label" for="archive-to">结束年份</label>'
        '<input id="archive-to" data-archive-to inputmode="numeric" type="number">'
        '<div class="archive-range__shortcuts"><button type="button" data-range-shortcut="5">5Y</button>'
        '<button type="button" data-range-shortcut="10">10Y</button><button type="button" data-range-shortcut="all">ALL</button></div>'
        '<button type="button" class="button button--ink" data-range-apply>显示范围</button></div>'
        '</section>'
        '<section class="archive-browser" data-archive-browser hidden>'
        '<div class="archive-browser__toolbar">'
        '<div class="mode-switch" role="tablist" aria-label="媒体类型">'
        '<button type="button" role="tab" data-media-mode="tv" aria-selected="true">TV</button>'
        '<button type="button" role="tab" data-media-mode="movie" aria-selected="false">MOVIE</button></div>'
        '<div class="archive-browser__scope" data-archive-scope-label></div></div>'
        '<div class="archive-layout" data-archive-layout>'
        '<section class="master-pane" aria-label="Archive results">'
        '<div class="browser-controls">'
        '<label class="search-field"><span class="sr-only">搜索作品</span>'
        '<input type="search" data-search placeholder="搜索标题、别名或 Bangumi ID" autocomplete="off"></label>'
        '<button type="button" class="control-button" data-filter-toggle aria-expanded="false" aria-controls="filter-panel">筛选 <span data-filter-count></span></button>'
        '<button type="button" class="control-button" data-sort-toggle aria-expanded="false" aria-controls="sort-popover">评分：高到低</button>'
        '<label class="page-size"><span class="sr-only">每页数量</span><select data-page-size aria-label="每页数量"></select></label></div>'
        '<div class="sort-popover" id="sort-popover" data-sort-popover hidden></div>'
        '<div class="active-filter-strip" data-active-filters hidden></div>'
        '<p class="results-summary" data-results-summary></p>'
        '<div class="results-sections" data-list-sections></div>'
        '<p class="no-results" data-no-results hidden><strong>NO MATCH / 00</strong><br>没有符合条件的资料。<button type="button" class="text-button" data-clear-all>清除筛选</button></p>'
        '<nav class="pager" data-pager aria-label="结果分页"></nav></section>'
        '<aside class="workspace" data-workspace aria-live="polite">'
        '<section class="workspace-panel workspace-panel--scope" data-scope-panel></section>'
        '<section class="workspace-panel" id="detail-panel" data-detail-panel hidden></section>'
        '<section class="workspace-panel" id="filter-panel" data-filter-panel hidden></section>'
        '</aside></div></section></main>'
        '<footer class="site-footer"><p>事实来自已核验的本地 Archive；此页面只读取同源静态文件。</p></footer>'
    )
    return _page(
        "Archive · Bangumi Side B",
        body,
        "../assets/app.css",
        "../assets/app.js",
        body_class="season-archive",
        data_attrs={"data-page": "archive"},
    )


def _settings_html() -> bytes:
    body = (
        _site_header("../index.html", "../archive/index.html", "../settings/index.html", "SETTINGS")
        + '<main class="reference-page"><section class="archive-intro">'
        '<p class="archive-intro__code">SETTINGS / LOCAL</p><div><h1>设置</h1>'
        '<p class="archive-intro__summary">当前页面只统一站点导航与排版；浏览运行时不连接 SQLite、Bangumi API 或第三方服务。</p>'
        '</div></section></main>'
        '<footer class="site-footer"><p>档案页面保持同源静态资源与可复现构建边界。</p></footer>'
    )
    return _page(
        "Settings · Bangumi Side B",
        body,
        "../assets/app.css",
        "../assets/app.js",
        body_class="season-archive",
        data_attrs={"data-page": "settings"},
    )


def _quarter_html(quarter: QuarterProjection) -> bytes:
    label = html.escape(quarter.quarter)
    sections = (
        ("tv", "premiere", "本季度新番", quarter.tv_premiere),
        ("tv", "continuing", "跨季度续播", quarter.tv_continuing),
        ("movie", "premiere", "剧场版", quarter.movie_premiere),
    )
    rendered = "".join(
        _result_section(mode, kind, title, records)
        for mode, kind, title, records in sections
    )
    counts = {
        "tv": len(quarter.tv_premiere) + len(quarter.tv_continuing),
        "movie": len(quarter.movie_premiere),
        "premiere": len(quarter.tv_premiere) + len(quarter.movie_premiere),
        "continuing": len(quarter.tv_continuing),
    }
    body = (
        _site_header("../index.html", "../archive/index.html", "../settings/index.html", f"QUARTER / {quarter.quarter}")
        + f'<main class="quarter-page season-{html.escape(quarter.quarter[-2:])}" '
        f'data-archive-app data-page="quarter" data-quarter="{label}" data-workspace-mode="scope" '
        f'data-data-url="../data/quarters/{label}.json" data-site-root="../" '
        f'data-count-tv="{counts["tv"]}" data-count-movie="{counts["movie"]}" '
        f'data-count-premiere="{counts["premiere"]}" data-count-continuing="{counts["continuing"]}">'
        '<section class="archive-intro archive-intro--quarter">'
        f'<p class="archive-intro__code">QUARTER / {label}</p><div>'
        f'<h1>{label[:4]}<span>—</span>{label[-2:]}</h1>'
        '<p class="archive-intro__summary">日本播出档案 · 已核验资料</p></div></section>'
        '<section class="archive-layout" data-quarter-layout>'
        '<section class="master-pane" aria-label="Quarter results">'
        '<div class="browser-controls">'
        '<div class="mode-switch" role="tablist" aria-label="媒体类型">'
        '<button type="button" role="tab" data-media-mode="tv" aria-selected="true">TV</button>'
        '<button type="button" role="tab" data-media-mode="movie" aria-selected="false">MOVIE</button></div>'
        '<label class="search-field"><span class="sr-only">搜索作品</span>'
        '<input type="search" data-search placeholder="搜索标题、别名或 Bangumi ID" autocomplete="off"></label>'
        '<button type="button" class="control-button" data-filter-toggle aria-expanded="false" aria-controls="filter-panel">筛选 <span data-filter-count></span></button>'
        '<button type="button" class="control-button" data-sort-toggle aria-expanded="false" aria-controls="sort-popover">评分：高到低</button>'
        '<label class="page-size"><span class="sr-only">每页数量</span><select data-page-size aria-label="每页数量"></select></label></div>'
        '<div class="sort-popover" id="sort-popover" data-sort-popover hidden></div>'
        '<div class="active-filter-strip" data-active-filters hidden></div>'
        '<p class="results-summary" data-results-summary></p>'
        f'<div class="results-sections" data-list-sections>{rendered}</div>'
        '<p class="no-results" data-no-results hidden><strong>NO MATCH / 00</strong><br>没有符合条件的资料。<button type="button" class="text-button" data-clear-all>清除筛选</button></p>'
        '<nav class="pager" data-pager aria-label="结果分页"></nav></section>'
        '<aside class="workspace" data-workspace aria-live="polite">'
        '<section class="workspace-panel workspace-panel--scope" data-scope-panel></section>'
        '<section class="workspace-panel" id="detail-panel" data-detail-panel hidden></section>'
        '<section class="workspace-panel" id="filter-panel" data-filter-panel hidden></section>'
        '</aside></section></main>'
        '<footer class="site-footer"><p>数据来自已核验的本地 Archive；运行时只读取同源静态文件。</p></footer>'
    )
    return _page(
        f"{quarter.quarter} · Bangumi Side B",
        body,
        "../assets/app.css",
        "../assets/app.js",
        body_class=f"season-{quarter.quarter[-2:]}",
        data_attrs={"data-page": "quarter"},
    )


def _result_section(
    mode: str,
    kind: str,
    title: str,
    records: tuple[SubjectProjection, ...],
) -> str:
    rows = "".join(_subject_row(item, index + 1) for index, item in enumerate(records))
    return (
        f'<section class="result-section" data-list-section="{mode}" '
        f'data-appearance-section="{kind}"><header class="result-section__header">'
        f'<p class="result-section__code">{mode.upper()} / {kind.upper()}</p>'
        f'<h2>{html.escape(title)}</h2><span data-section-count>{len(records):02d}</span>'
        f'</header><div class="result-list" data-list>{rows}</div></section>'
    )


def _subject_row(item: SubjectProjection | Mapping[str, object], sequence: int) -> str:
    if isinstance(item, SubjectProjection):
        value = item.to_dict()
        value["id"] = item.subject_id
        value["media"] = item.media_format
        value["appearance"] = item.appearance_kind
        value["quarter"] = item.quarter
        cover = item.cover_url
    else:
        value = dict(item)
        cover = value.get("cover") or value.get("cover_url")
    subject_id = int(value.get("id") or value.get("subject_id") or 0)
    media = str(value.get("media") or value.get("media_format") or "TV").upper()
    appearance = str(value.get("appearance") or value.get("appearance_kind") or "premiere")
    quarter = str(value.get("quarter") or "")
    preferred = str(value.get("preferred_title") or "")
    original = str(value.get("original_title") or "")
    aliases = value.get("aliases") if isinstance(value.get("aliases"), list) else []
    source = str(value.get("source") or "unknown")
    source_label = "来源未知" if source == "unknown" else source
    tags = value.get("allowed_tags") if isinstance(value.get("allowed_tags"), list) else []
    search = " ".join([preferred, original, *(str(alias) for alias in aliases), str(subject_id)])
    tag_value = "|".join(str(tag) for tag in tags)
    record_key = "@".join((str(subject_id), quarter, appearance))
    score = value.get("score", value.get("rating_score"))
    score_label = "—" if score is None else f"{float(score):.1f}"
    rating_count = value.get("rating_count")
    air_date = str(value.get("air_date") or "")
    cover_html = _cover_markup(cover, sequence, subject_id)
    tags_html = "".join(f'<span class="tag">{html.escape(str(tag))}</span>' for tag in tags[:2])
    return (
        f'<article class="subject-row" role="listitem" data-subject-id="{subject_id}" '
        f'data-record-key="{html.escape(record_key, quote=True)}" data-media="{media.lower()}" '
        f'data-appearance="{html.escape(appearance, quote=True)}" '
        f'data-search-text="{html.escape(search, quote=True)}" data-source="{html.escape(source, quote=True)}" '
        f'data-tags="{html.escape(tag_value, quote=True)}" data-air-date="{html.escape(air_date, quote=True)}" '
        f'data-score="{html.escape(str(score if score is not None else ""), quote=True)}" '
        f'data-rating-count="{html.escape(str(rating_count if rating_count is not None else ""), quote=True)}" '
        f'data-quarter="{html.escape(quarter, quote=True)}">'
        f'<button type="button" class="subject-row__open" data-open-subject '
        f'aria-label="打开 {html.escape(preferred)}" aria-controls="detail-panel" '
        f'aria-expanded="false">'
        f'<span class="subject-row__sequence" aria-hidden="true">{sequence:03d}</span>'
        f'{cover_html}<span class="subject-row__content"><strong class="subject-row__title">'
        f'{html.escape(preferred)}</strong>'
        f'<span class="subject-row__original">{html.escape(original)}</span>'
        f'<span class="subject-row__meta">{html.escape(media)}'
        f'{(" · " + html.escape(str(value.get("episode_count")) + "话") if value.get("episode_count") else "")}'
        f'{(" · " + html.escape(air_date) if air_date else "")}'
        f'{(" · " + html.escape(source_label) if source_label else "")}'
        f'{(" · " + html.escape(quarter) if quarter and not isinstance(item, SubjectProjection) else "")}</span>'
        f'<span class="subject-row__tags">{tags_html}</span></span>'
        f'<span class="subject-row__score"><b>{score_label}</b>'
        f'<small>{html.escape(str(rating_count)) if rating_count is not None else "—"}</small></span>'
        f'</button></article>'
    )


def _cover_markup(cover: object, sequence: int, subject_id: int) -> str:
    if not cover:
        return '<span class="subject-row__cover subject-row__cover--missing" aria-label="缺少封面"><span>ARCHIVE</span></span>'
    path = str(cover)
    loading = "eager" if sequence <= 10 else "lazy"
    return (
        '<span class="subject-row__cover"><img width="52" height="74" '
        f'loading="{loading}" src="../{html.escape(path, quote=True)}" '
        f'alt="" data-cover-subject="{subject_id}"></span>'
    )


def _site_header(home_href: str, archive_href: str, settings_href: str, code: str) -> str:
    return (
        '<header class="site-header"><div class="site-header__rule">'
        '<p>Bangumi Side B / 日本播出档案</p>'
        f'<p>{html.escape(code)}</p></div><div class="site-header__brand">'
        f'<a class="brand" href="{html.escape(home_href, quote=True)}">Bangumi Side B</a>'
        '<nav class="site-nav" aria-label="主导航">'
        f'<a href="{html.escape(archive_href, quote=True)}">Archive</a>'
        f'<a href="{html.escape(settings_href, quote=True)}">Settings</a></nav></div></header>'
    )


def _page(
    title: str,
    body: str,
    css_href: str,
    js_href: str,
    *,
    body_class: str = "",
    data_attrs: Mapping[str, str] | None = None,
) -> bytes:
    attributes = "".join(
        f' {key}="{html.escape(value, quote=True)}"'
        for key, value in (data_attrs or {}).items()
    )
    class_attr = f' class="{html.escape(body_class, quote=True)}"' if body_class else ""
    content = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f'<link rel="stylesheet" href="{css_href}"></head>'
        f"<body{class_attr}{attributes}>{body}<script src=\"{js_href}\" defer></script></body></html>"
    )
    return content.encode("utf-8")


def _quarter_label(quarter: Quarter) -> str:
    return f"{quarter.year:04d}-{quarter.month:02d}"


def _blocked_quarters(facts: ArchiveFacts) -> tuple[str, ...]:
    reviews = {_quarter_label(item) for item in facts.review_quarters}
    return tuple(
        sorted(
            _quarter_label(quarter)
            for quarter, state in facts.sync_states
            if state.facts_status != "complete" or _quarter_label(quarter) in reviews
        )
    )


def _merge_retained_state(
    current: BuildState,
    previous: BuildState | None,
    retained: tuple[str, ...],
    years: tuple[YearCatalogProjection, ...],
    archive: ArchiveIndexProjection,
) -> BuildState:
    quarter_values = dict(current.quarters)
    statuses = dict(current.quarter_status)
    if previous is not None:
        for label in retained:
            if label in previous.quarters:
                quarter_values[label] = previous.quarters[label]
                statuses[label] = "retained"
    return replace(
        current,
        quarters=quarter_values,
        years={str(item.year): item.fingerprint for item in years},
        archive=archive.fingerprint,
        quarter_status=statuses,
    )


def _read_site_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_required_site_json(path: Path) -> dict[str, object] | None:
    value = _read_site_json(path)
    return value or None


def _reused_spec(
    relative: str,
    previous_artifacts: Mapping[str, str],
    previous_sizes: Mapping[str, int],
) -> ArtifactSpec:
    return ArtifactSpec(
        relative,
        previous_artifacts.get(relative),
        previous_sizes.get(relative),
        "retained",
        None,
    )


def _offline_manifest_bytes(
    quarter: QuarterProjection, specs: Mapping[str, ArtifactSpec]
) -> bytes:
    label = quarter.quarter
    required = [
        f"{label}/index.html",
        f"data/quarters/{label}.json",
        "assets/app.css",
        "assets/app.js",
    ]
    required.extend(
        sorted(
            {
                item.cover_url.split("?", 1)[0]
                for group in (
                    quarter.tv_premiere,
                    quarter.tv_continuing,
                    quarter.movie_premiere,
                )
                for item in group
                if item.cover_url
            }
        )
    )
    resources: list[dict[str, object]] = []
    for relative in required:
        spec = specs.get(relative)
        if spec is None or spec.content_hash is None or spec.size_bytes is None:
            raise ProjectionError(f"offline resource metadata is missing: {relative}")
        resources.append(
            {
                "url": relative,
                "content_hash": spec.content_hash,
                "size_bytes": spec.size_bytes,
            }
        )
    return json_bytes(
        {
            "quarter": label,
            "revision": quarter.fingerprint,
            "resources": resources,
        }
    )


__all__ = [
    "BuildBlocked",
    "BuildError",
    "SiteBuildRun",
    "UnifiedSiteBuilder",
]
