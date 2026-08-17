"""Focused semantics checks for the read-only release audit."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bgm_side_b.archive_config import load_archive_sync_settings
from bgm_side_b.release.unified_audit import QuarterAuditSummary, UnifiedReleaseAuditor
from tests.release_fixture import create_release_project


def _audit(root: Path):
    settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
    return UnifiedReleaseAuditor(root, settings).audit()


def test_audit_reports_database_subjects_and_quarter_appearances(
    tmp_path: Path,
) -> None:
    root, _ = create_release_project(tmp_path)

    result = _audit(root)

    assert result.passed
    assert result.subject_count == 2
    assert result.quarter_summaries == (
        QuarterAuditSummary("2026-04", 1, 0, 0),
        QuarterAuditSummary("2026-07", 0, 1, 1),
    )
    assert all(
        summary.total_appearances
        == summary.tv_premiere + summary.tv_continuing + summary.movie_premiere
        for summary in result.quarter_summaries
    )
    rendered = result.render()
    assert "数据库总作品     2" in rendered
    assert "季度条目 2026-04  TV首播=1  TV续播=0  剧场版=0  合计=1" in rendered
    assert "季度条目 2026-07  TV首播=0  TV续播=1  剧场版=1  合计=2" in rendered
    assert "作品             2" not in rendered


def test_review_blocked_quarter_has_no_publishable_composition(
    tmp_path: Path,
) -> None:
    root, _ = create_release_project(tmp_path)
    database = root / "workspace" / "data" / "bangumi-side-b.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO subject_review_issues
                (subject_id, issue_code, candidate_year, candidate_quarter,
                 observed_value, details_json, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (202, "AUDIT_REVIEW", 2026, 7, None, "{}", "2026-08-01T00:00:00Z"),
        )
        connection.commit()

    result = _audit(root)

    assert result.passed
    assert result.publishable_quarters == ("2026-04",)
    assert tuple(summary.quarter for summary in result.quarter_summaries) == (
        "2026-04",
    )
