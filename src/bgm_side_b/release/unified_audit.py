"""Read-only release checks for the current SQLite archive contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bgm_side_b.archive_config import ArchiveSyncSettings
from bgm_side_b.build.site_projection import ArchiveFactsReader, ProjectionError
from bgm_side_b.database import Database, DatabaseError, UnknownSchemaError
from bgm_side_b.domain import MediaFormat


@dataclass(frozen=True)
class UnifiedAuditFailure:
    check: str
    count: int
    reason: str


@dataclass(frozen=True)
class UnifiedAuditResult:
    subject_count: int
    publishable_quarters: tuple[str, ...]
    review_quarters: tuple[str, ...]
    failures: tuple[UnifiedAuditFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def render(self) -> str:
        heading = "统一资料审计通过" if self.passed else "统一资料审计失败"
        lines = [
            heading,
            f"作品             {self.subject_count}",
            f"可发布季度       {', '.join(self.publishable_quarters) or 'none'}",
            f"待裁决季度       {', '.join(self.review_quarters) or 'none'}",
        ]
        if self.failures:
            lines.append("问题")
            lines.extend(
                f"- {failure.check} {failure.count}: {failure.reason}"
                for failure in self.failures
            )
        return "\n".join(lines)


class UnifiedReleaseAuditor:
    """Audit only the released SQLite schema; it never creates or mutates data."""

    def __init__(self, project_root: Path, settings: ArchiveSyncSettings) -> None:
        self.root = project_root.resolve()
        self.settings = settings
        self.database_path = self.root / "workspace" / "data" / "bangumi-side-b.sqlite3"

    def audit(self) -> UnifiedAuditResult:
        if not self.database_path.is_file():
            return _failed("workspace", "workspace database is missing")
        database = Database(self.database_path)
        try:
            facts = ArchiveFactsReader(database, self.root / "workspace").read(
                self.settings.excluded_subject_ids
            )
        except (DatabaseError, UnknownSchemaError, ProjectionError) as error:
            return _failed("schema", _safe_error(error))

        failures: list[UnifiedAuditFailure] = []
        if not facts.subjects:
            failures.append(UnifiedAuditFailure("subjects", 1, "no archive subjects"))
        review_quarters = tuple(
            sorted(
                f"{item.year:04d}-{item.month:02d}"
                for item in facts.review_quarters
            )
        )
        blocked_review = {
            item for item in facts.review_quarters
        }
        publishable = tuple(
            sorted(
                {
                    f"{quarter.year:04d}-{quarter.month:02d}"
                    for quarter, state in facts.state_by_quarter.items()
                    if state.facts_status == "complete"
                    and state.covers_status == "complete"
                    and quarter not in blocked_review
                    and any(
                        item[1].quarter == quarter
                        for item in facts.by_quarter.get(quarter, ())
                    )
                }
            )
        )
        if not publishable:
            failures.append(
                UnifiedAuditFailure("quarters", 1, "no publishable complete quarter")
            )
        unsupported = [
            subject.subject_id
            for subject in facts.subjects
            if subject.media_format
            not in {MediaFormat.TV.value, MediaFormat.MOVIE.value}
        ]
        if unsupported:
            failures.append(
                UnifiedAuditFailure(
                    "format", len(unsupported), "unsupported media format"
                )
            )
        return UnifiedAuditResult(
            len(facts.subjects), publishable, review_quarters, tuple(failures)
        )


def _failed(check: str, reason: str) -> UnifiedAuditResult:
    return UnifiedAuditResult(0, (), (), (UnifiedAuditFailure(check, 1, reason),))


def _safe_error(error: BaseException) -> str:
    value = str(error).strip()
    return value or "database cannot be read"
