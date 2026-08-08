"""Risk-focused tests for the narrow, read-only static build projection."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bgm_side_b.build import BuildProjection, BuildQueries
from bgm_side_b.config import load_rules
from bgm_side_b.legacy_database import Database
from bgm_side_b.legacy_repository import (
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)


class TracingDatabase(Database):
    """Record build reads so role tables cannot return unnoticed."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.statements: list[str] = []

    def connect(self):  # type: ignore[no-untyped-def]
        connection = super().connect()
        connection.set_trace_callback(self.statements.append)
        return connection


def test_build_projection_keeps_only_japan_tv_new_subjects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    database = TracingDatabase(workspace / "data" / "facts.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
        _subject(repository, connection, 102, "movie", "new", "Japan")
        _subject(repository, connection, 103, "tv", "continuing", "Japan")
        _subject(repository, connection, 104, "tv", "new", "China")
        _subject(repository, connection, 106, "tv", "new", None, subject_type=2)
        repository.upsert_subject(
            connection,
            SubjectRecord(105, "tv", None, date(2025, 1, 1), 12, None, None),
        )
        repository.replace_titles(connection, 105, [SubjectTitle("preferred", "Old")])
        repository.replace_infobox(
            connection, 105, [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", "Japan")]
        )
        repository.replace_quarters(
            connection, 105, [SubjectQuarter(2025, 1, "new")]
        )

    settings, tags, sources = load_rules(Path(__file__).parents[1] / "config")
    database.statements.clear()
    facts = BuildQueries(database).load_quarter(
        2026, 4, navigation=((2026, 4),)
    )
    model = BuildProjection(
        tags,
        sources,
        workspace,
        country_filter=settings.country_filter,
    ).project_quarter(facts)

    subject_ids = [
        card.subject_id for section in model.sections for card in section.subjects
    ]
    assert subject_ids == [101, 106]
    assert model.metadata.subject_count == 2
    assert model.metadata.country_filtered_subjects == 1
    assert model.navigation[0].year == 2026
    assert all(
        name not in "\n".join(database.statements).lower()
        for name in ("subject_characters", "character_voices", "persons")
    )


def _subject(
    repository: SubjectRepository,
    connection: object,
    subject_id: int,
    media_format: str,
    appearance_kind: str,
    country: str | None,
    *,
    subject_type: int | None = None,
) -> None:
    repository.upsert_subject(
        connection,
        SubjectRecord(
            subject_id,
            media_format,
            None,
            date(2026, 4, 1),
            12,
            7.0,
            1,
            subject_type=subject_type,
        ),
    )
    repository.replace_titles(
        connection, subject_id, [SubjectTitle("preferred", f"Subject {subject_id}")]
    )
    repository.replace_infobox(
        connection,
        subject_id,
        (
            []
            if country is None
            else [SubjectInfoboxItem("\u56fd\u5bb6/\u5730\u533a", country)]
        ),
    )
    repository.replace_quarters(
        connection, subject_id, [SubjectQuarter(2026, 4, appearance_kind)]
    )
