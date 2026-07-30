"""Read-only checks for the deliberately narrow first-release dataset."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bgm_side_b.config import ProjectSettings
from bgm_side_b.database import MIGRATIONS
from bgm_side_b.release.candidate import read_pages_build_marker
from bgm_side_b.rules import InfoboxItem, decide_country


@dataclass(frozen=True)
class AuditFailure:
    """One compact, countable audit violation without local-path disclosure."""

    check: str
    count: int
    reason: str


@dataclass(frozen=True)
class AuditResult:
    """The read-only first-release audit result and safe operator summary."""

    subject_count: int
    episode_count: int
    cover_count: int
    character_count: int
    out_of_scope_subjects: int
    build_marker_status: str
    failures: tuple[AuditFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def render(self) -> str:
        heading = "第一版资料审计通过" if self.passed else "第一版资料审计失败"
        lines = [
            heading,
            "季度 2026-04",
            f"日本 TV {self.subject_count}",
            f"章节 {self.episode_count}",
            f"封面 {self.cover_count}",
            f"角色 {self.character_count}",
            f"范围外 {self.out_of_scope_subjects}",
            f"构建标记 {self.build_marker_status}",
        ]
        if self.failures:
            lines.append("问题")
            lines.extend(
                f"- {failure.check} {failure.count}: {failure.reason}"
                for failure in self.failures
            )
        return "\n".join(lines)


class ReleaseDataAuditor:
    """Inspect one workspace without creating, migrating, or modifying it."""

    def __init__(self, project_root: Path, settings: ProjectSettings) -> None:
        self.project_root = project_root
        self.settings = settings
        self.workspace = project_root / "workspace"
        self.database_path = self.workspace / "data" / "bangumi-side-b.sqlite3"

    def audit(self) -> AuditResult:
        """Return all scoped data violations using a SQLite read-only connection."""
        if not self.database_path.is_file():
            return _failed_result("workspace", "workspace database is missing")
        try:
            connection = sqlite3.connect(
                f"{self.database_path.resolve().as_uri()}?mode=ro", uri=True
            )
        except sqlite3.Error:
            return _failed_result("workspace", "workspace database cannot be opened")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            return self._audit_connection(connection)
        except sqlite3.Error:
            return _failed_result("schema", "database schema cannot be read")
        finally:
            connection.close()

    def _audit_connection(self, connection: sqlite3.Connection) -> AuditResult:
        failures: list[AuditFailure] = []
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            failures.append(AuditFailure("integrity", 1, "integrity check failed"))
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            failures.append(
                AuditFailure("foreign_keys", len(foreign_keys), "foreign key violation")
            )
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        if version != MIGRATIONS[-1].version:
            failures.append(AuditFailure("schema", 1, "schema version is unsupported"))

        subjects = {
            row["id"]: row["media_format"]
            for row in connection.execute("SELECT id, media_format FROM subjects")
        }
        quarter_rows = tuple(
            connection.execute(
                "SELECT subject_id, year, month, appearance_kind FROM subject_quarters"
            )
        )
        configured = {
            (int(value[:4]), int(value[5:]))
            for value in self.settings.scope.release_quarters
        }
        valid_relation_ids = {
            row["subject_id"]
            for row in quarter_rows
            if (row["year"], row["month"]) in configured
            and row["appearance_kind"] == "new"
        }
        invalid_relation_ids = {
            row["subject_id"]
            for row in quarter_rows
            if (row["year"], row["month"]) not in configured
            or row["appearance_kind"] != "new"
        }
        out_of_scope_ids = (
            (set(subjects) - valid_relation_ids) | invalid_relation_ids
        )
        if out_of_scope_ids:
            failures.append(
                AuditFailure(
                    "scope", len(out_of_scope_ids), "subject is outside 2026-04 new"
                )
            )
        non_tv = [
            subject_id
            for subject_id, media_format in subjects.items()
            if media_format != "tv"
        ]
        if non_tv:
            failures.append(AuditFailure("format", len(non_tv), "subject is not TV"))

        country_failures = self._country_failures(connection, subjects)
        if country_failures:
            failures.append(
                AuditFailure(
                    "country",
                    len(country_failures),
                    "Japan evidence is missing or invalid",
                )
            )
        blacklisted = set(subjects) & self.settings.excluded_subject_ids
        if blacklisted:
            failures.append(
                AuditFailure(
                    "blacklist", len(blacklisted), "blacklisted subject remains"
                )
            )

        character_count = _count(connection, "characters") + _count(
            connection, "subject_characters"
        )
        person_count = _count(connection, "persons")
        voice_count = _count(connection, "character_voices")
        if character_count:
            failures.append(
                AuditFailure("characters", character_count, "role data remains")
            )
        if person_count:
            failures.append(
                AuditFailure("persons", person_count, "person data remains")
            )
        if voice_count:
            failures.append(
                AuditFailure("character_voices", voice_count, "voice relation remains")
            )

        cover_count, media_failures = self._media_summary(connection)
        failures.extend(media_failures)
        marker_status, marker_failure = self._marker_status()
        if marker_failure is not None:
            failures.append(marker_failure)
        return AuditResult(
            subject_count=len(subjects),
            episode_count=_count(connection, "episodes"),
            cover_count=cover_count,
            character_count=character_count,
            out_of_scope_subjects=len(out_of_scope_ids),
            build_marker_status=marker_status,
            failures=tuple(failures),
        )

    def _country_failures(
        self, connection: sqlite3.Connection, subjects: dict[int, str]
    ) -> set[int]:
        rows = connection.execute(
            "SELECT subject_id, item_key, value_json FROM subject_infobox_items "
            "ORDER BY subject_id, position"
        )
        infobox: defaultdict[int, list[InfoboxItem]] = defaultdict(list)
        for row in rows:
            value = _json_string(row["value_json"])
            if value is not None:
                infobox[row["subject_id"]].append(InfoboxItem(row["item_key"], value))
        return {
            subject_id
            for subject_id in subjects
            if not decide_country(
                infobox[subject_id], self.settings.country_filter
            ).included
        }

    def _media_summary(
        self, connection: sqlite3.Connection
    ) -> tuple[int, tuple[AuditFailure, ...]]:
        rows = tuple(
            connection.execute(
                "SELECT owner_type, media_kind, local_path, status FROM media_files"
            )
        )
        character_media = [
            row
            for row in rows
            if row["owner_type"] == "character"
            or row["media_kind"] == "character_image"
        ]
        unexpected = [
            row
            for row in rows
            if row["owner_type"] != "subject" or row["media_kind"] != "cover"
        ]
        unsafe = [
            row
            for row in rows
            if row["owner_type"] == "subject"
            and row["media_kind"] == "cover"
            and row["status"] == "success"
            and not _safe_cover_path(row["local_path"], self.workspace)
        ]
        failures: list[AuditFailure] = []
        if character_media:
            failures.append(
                AuditFailure(
                    "character_media", len(character_media), "character media remains"
                )
            )
        other_media = max(0, len(unexpected) - len(character_media))
        if other_media:
            failures.append(
                AuditFailure("media", other_media, "unsupported media record remains")
            )
        if unsafe:
            failures.append(
                AuditFailure(
                    "cover_paths", len(unsafe), "cover path is unsafe or missing"
                )
            )
        return (
            sum(
                row["owner_type"] == "subject"
                and row["media_kind"] == "cover"
                and row["status"] == "success"
                for row in rows
            ),
            tuple(failures),
        )

    def _marker_status(self) -> tuple[str, AuditFailure | None]:
        marker = self.workspace / "state" / "pages-build.json"
        if not marker.is_file():
            return "missing", None
        try:
            read_pages_build_marker(self.workspace)
        except ValueError:
            return "invalid", AuditFailure("build_marker", 1, "build marker is invalid")
        return "valid", None


def _failed_result(check: str, reason: str) -> AuditResult:
    return AuditResult(0, 0, 0, 0, 0, "missing", (AuditFailure(check, 1, reason),))


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _json_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) else None


def _safe_cover_path(value: object, workspace: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", normalized):
        return False
    relative = PurePosixPath(normalized)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != ("media", "covers")
    ):
        return False
    try:
        candidate = (workspace / Path(relative.as_posix())).resolve()
        return candidate.is_relative_to(workspace.resolve()) and candidate.is_file()
    except OSError:
        return False
