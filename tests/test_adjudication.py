"""Manual archive-quarter override and REVIEW workflow coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b.adjudication import ArchiveAdjudicator, AssignmentError, render_review
from bgm_side_b.admission import QuarterOverride
from bgm_side_b.database import Database
from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    MediaFormat,
    Quarter,
)
from bgm_side_b.overrides import load_quarter_overrides, save_quarter_overrides
from bgm_side_b.repository import (
    InfoboxItem,
    QuarterOwnership,
    ReviewIssue,
    SubjectRecord,
    SubjectRepository,
    SubjectSnapshot,
)


@pytest.fixture
def repository(tmp_path: Path) -> SubjectRepository:
    database = Database(tmp_path / "facts.sqlite3")
    database.initialize()
    return SubjectRepository(database)


def _snapshot(*, air_date: date = date(2026, 3, 31)) -> SubjectSnapshot:
    subject = SubjectRecord(
        101,
        "Original",
        "中文",
        None,
        MediaFormat.TV,
        air_date,
        None,
        12,
        7.5,
        100,
        JapaneseDecision(
            JapaneseClassification.ACCEPTED_JAPANESE,
            "infobox_country",
            '["日本"]',
        ),
    )
    return SubjectSnapshot(
        subject,
        infobox=(InfoboxItem("国家/地区", "日本"),),
        review_issues=(
            ReviewIssue(
                "TV_QUARTER_BOUNDARY",
                Quarter(2026, 4),
                "2026-03-31",
                {},
                "now",
            ),
        ),
    )


def _store(repository: SubjectRepository, snapshot: SubjectSnapshot) -> None:
    with repository.transaction() as connection:
        repository.replace_subject_snapshot(connection, snapshot)


def test_overrides_round_trip_and_reject_duplicate_or_invalid_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quarter-overrides.toml"
    assignments = {
        101: QuarterOverride(Quarter(2026, 4), "early broadcast"),
        102: QuarterOverride(None),
    }
    save_quarter_overrides(path, assignments)

    assert load_quarter_overrides(path) == assignments
    path.write_text(
        "[[assignments]]\nsubject_id = 101\nyear = 2026\nquarter_month = 4\n"
        "[[assignments]]\nsubject_id = 101\nunassigned = true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_quarter_overrides(path)


def test_assign_unassigned_and_clear_reconcile_quarter_review(
    repository: SubjectRepository, tmp_path: Path
) -> None:
    _store(repository, _snapshot())
    path = tmp_path / "quarter-overrides.toml"
    adjudicator = ArchiveAdjudicator(repository, path, frozenset())

    assigned = adjudicator.assign(101, QuarterOverride(Quarter(2026, 4), "early"))
    assert assigned.quarter == QuarterOwnership(
        Quarter(2026, 4), assigned.quarter.assignment_source, "early"
    )
    assert not assigned.review_issues
    assert load_quarter_overrides(path)[101].quarter == Quarter(2026, 4)

    cleared = adjudicator.clear(101)
    assert cleared.quarter is None
    assert cleared.review_issues[0].issue_code == "TV_QUARTER_BOUNDARY"
    assert load_quarter_overrides(path) == {}

    unassigned = adjudicator.assign(101, QuarterOverride(None, "not seasonal"))
    assert unassigned.quarter is None
    assert load_quarter_overrides(path)[101].quarter is None


def test_blacklist_and_japanese_unresolved_cannot_be_overridden(
    repository: SubjectRepository, tmp_path: Path
) -> None:
    _store(repository, _snapshot())
    path = tmp_path / "quarter-overrides.toml"
    with pytest.raises(AssignmentError, match="blacklisted"):
        ArchiveAdjudicator(repository, path, frozenset({101})).assign(
            101, QuarterOverride(Quarter(2026, 4))
        )
    unresolved = replace(
        _snapshot(),
        subject=replace(
            _snapshot().subject,
            japanese=JapaneseDecision(
                JapaneseClassification.UNRESOLVED,
                "unresolved_missing_infobox_country",
                "[]",
            ),
        ),
    )
    _store(repository, unresolved)
    with pytest.raises(AssignmentError, match="Japanese-only"):
        ArchiveAdjudicator(repository, path, frozenset()).assign(
            101, QuarterOverride(Quarter(2026, 4))
        )


def test_review_rendering_is_actionable_and_missing_subject_is_refused(
    repository: SubjectRepository, tmp_path: Path
) -> None:
    _store(repository, _snapshot())
    rendered = render_review(repository)
    assert "BGM ID       101" in rendered
    assert "bgmb assign 101 2026 4" in rendered
    with pytest.raises(AssignmentError, match="not stored"):
        adjudicator = ArchiveAdjudicator(
            repository, tmp_path / "quarter-overrides.toml", frozenset()
        )
        adjudicator.assign(202, QuarterOverride(Quarter(2026, 4)))
