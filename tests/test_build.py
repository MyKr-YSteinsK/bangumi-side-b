"""Risk-focused tests for deterministic, read-only build projection."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b.build import BuildProjection, BuildQueries
from bgm_side_b.config import SourceRules, TagRules, load_rules
from bgm_side_b.database import Database
from bgm_side_b.repository import (
    CharacterRecord,
    CharacterVoiceRecord,
    EpisodeRecord,
    MediaRecord,
    PersonRecord,
    RawTag,
    SubjectCharacterRecord,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectSource,
    SubjectTitle,
)


class CountingDatabase(Database):
    """A test-only database that records fixed read-query counts."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.read_statements = 0

    def connect(self):  # type: ignore[no-untyped-def]
        connection = super().connect()
        connection.set_trace_callback(self._trace)
        return connection

    def _trace(self, statement: str) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            self.read_statements += 1


@pytest.fixture
def build_context(
    tmp_path: Path,
) -> tuple[CountingDatabase, Path, TagRules, SourceRules]:
    workspace = tmp_path / "workspace"
    database = CountingDatabase(workspace / "data" / "facts.sqlite3")
    database.migrate()
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _seed_subject(repository, connection, 1, "tv", date(2022, 1, 2), 7.0, 100)
        _seed_subject(repository, connection, 2, "tv", date(2022, 1, 3), 7.0, 200)
        _seed_subject(repository, connection, 3, "movie", date(2022, 1, 8), 8.0, 10)
        _seed_subject(repository, connection, 4, "tv", date(2022, 1, 9), 6.0, 1)
        _seed_subject(repository, connection, 5, "tv", date(2022, 1, 4), None, None)
        repository.replace_titles(
            connection,
            1,
            [
                SubjectTitle("preferred", "中文标题"),
                SubjectTitle("original", "Original Title"),
                SubjectTitle("alias", " Alias "),
                SubjectTitle("alias", "Alias"),
            ],
        )
        for subject_id in (2, 3, 5):
            repository.replace_titles(
                connection,
                subject_id,
                [SubjectTitle("preferred", f"Subject {subject_id}")],
            )
        repository.replace_raw_tags(
            connection,
            1,
            [RawTag("搞笑", 10), RawTag("恋爱", 5), RawTag("未收录", 1)],
        )
        repository.replace_sources(
            connection,
            1,
            [
                SubjectSource("manga", "infobox", "漫画"),
                SubjectSource("manga", "tag", "漫画改编"),
                SubjectSource("game", "tag", "游戏改编"),
                SubjectSource("original", "tag", "原创"),
            ],
        )
        repository.replace_quarters(
            connection,
            1,
            [
                SubjectQuarter(2022, 1, "new", position=1),
                SubjectQuarter(
                    2022, 4, "continuing", "episode_air_date", "2022-04-02", 0
                ),
            ],
        )
        for subject_id, kind in ((2, "new"), (3, "movie"), (4, "new"), (5, "new")):
            repository.replace_quarters(
                connection,
                subject_id,
                [SubjectQuarter(2022, 1, kind, position=subject_id)],
            )
        repository.replace_main_episodes(
            connection,
            1,
            [
                _episode(101, 1, "第一集", "Episode One", date(2022, 1, 2)),
                _episode(102, 2, None, "Episode Two", date(2022, 1, 9)),
            ],
        )
        repository.upsert_character(
            connection, CharacterRecord(10, "Character", "角色", None)
        )
        repository.upsert_person(connection, PersonRecord(20, "Actor One", "声优一"))
        repository.replace_roles_snapshot(
            connection,
            1,
            [SubjectCharacterRecord(10, "main", 0)],
            [CharacterVoiceRecord(10, 20, None, 0)],
        )
        repository.upsert_person(connection, PersonRecord(21, "Actor Two", "声优二"))
        repository.replace_roles_snapshot(
            connection,
            2,
            [SubjectCharacterRecord(10, "main", 0)],
            [CharacterVoiceRecord(10, 21, None, 0)],
        )

    _write_media(repository, workspace, "subject", 1, "cover", "media/covers/1.bin")
    _write_media(
        repository,
        workspace,
        "character",
        10,
        "character_image",
        "media/characters/10.bin",
    )
    _, tag_rules, source_rules = load_rules(Path(__file__).parents[1] / "config")
    database.read_statements = 0
    return database, workspace, tag_rules, source_rules


def test_quarter_projection_rebuilds_rules_and_exposes_only_subject_scoped_details(
    build_context: tuple[CountingDatabase, Path, TagRules, SourceRules],
) -> None:
    database, workspace, tag_rules, source_rules = build_context
    facts = BuildQueries(database).load_quarter(2022, 1)
    model = BuildProjection(tag_rules, source_rules, workspace).project_quarter(facts)

    assert [section.kind for section in model.sections] == ["new", "movie"]
    assert model.metadata.subject_count == 4
    assert model.metadata.warnings == ("subject 4 has no usable title",)
    first = _card(model, 1)
    assert first.original_title == "Original Title"
    assert first.aliases == ("Alias",)
    assert [source.source for source in first.sources] == ["manga", "game", "original"]
    assert first.source_overflow_count == 1
    assert [tag.name for tag in first.tags] == ["喜剧", "恋爱"]
    assert first.declared_episode_count == 12
    assert first.total_episode_count == 13
    assert first.stored_main_episode_count == 2
    assert "未收录" not in first.search_text
    assert first.cover.is_available

    ranks = {
        card.subject_id: card for section in model.sections for card in section.subjects
    }
    assert ranks[2].sort_score_desc == 0
    assert ranks[1].sort_score_desc == 1
    assert ranks[5].sort_score_desc == 2
    assert ranks[2].sort_score_asc == 0
    assert ranks[1].sort_score_asc == 1
    assert ranks[5].sort_score_asc == 2
    assert ranks[2].sort_votes_desc == 0
    assert ranks[1].sort_votes_desc == 1
    assert ranks[5].sort_votes_desc == 2
    assert ranks[1].sort_votes_asc == 0
    assert ranks[2].sort_votes_asc == 1
    assert ranks[5].sort_votes_asc == 2

    detail = next(page for page in model.details if page.drawer.card.subject_id == 1)
    assert detail.drawer.permanent_year == 2022
    assert detail.drawer.permanent_month == 1
    assert [episode.chinese_title for episode in detail.episodes] == ["第一集", None]
    assert [actor.person_id for actor in detail.characters[0].voice_actors] == [20]
    assert detail.characters[0].image.is_available
    assert database.read_statements <= 12


def test_continuing_projection_retains_permanent_ownership_and_rejects_unsafe_media(
    build_context: tuple[CountingDatabase, Path, TagRules, SourceRules],
) -> None:
    database, workspace, tag_rules, source_rules = build_context
    queries = BuildQueries(database)
    continuing = BuildProjection(tag_rules, source_rules, workspace).project_quarter(
        queries.load_quarter(2022, 4)
    )
    card = _card(continuing, 1)
    assert card.section == "continuing"
    detail = continuing.details[0]
    assert (detail.drawer.permanent_year, detail.drawer.permanent_month) == (2022, 1)

    connection = database.connect()
    try:
        connection.execute(
            (
                "UPDATE media_files SET local_path = ? "
                "WHERE owner_type = ? AND owner_id = ?"
            ),
            ("../../outside.bin", "subject", 1),
        )
        connection.commit()
    finally:
        connection.close()
    unsafe = BuildProjection(tag_rules, source_rules, workspace).project_quarter(
        queries.load_quarter(2022, 1)
    )
    assert not _card(unsafe, 1).cover.is_available


def _seed_subject(
    repository: SubjectRepository,
    connection: object,
    subject_id: int,
    media_format: str,
    air_date: date,
    rating_score: float | None,
    rating_count: int | None,
) -> None:
    repository.upsert_subject(
        connection,
        SubjectRecord(
            subject_id,
            media_format,
            "完整简介",
            air_date,
            12,
            rating_score,
            rating_count,
            total_episode_count=13,
            end_date=date(2022, 3, 31),
        ),
    )


def _episode(
    episode_id: int,
    episode_number: int,
    name_cn: str | None,
    name: str,
    air_date: date,
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id,
        episode_number,
        episode_number,
        name,
        name_cn,
        air_date,
        None,
        None,
        episode_number - 1,
    )


def _write_media(
    repository: SubjectRepository,
    workspace: Path,
    owner_type: str,
    owner_id: int,
    media_kind: str,
    relative_path: str,
) -> None:
    content = f"{owner_type}-{owner_id}".encode()
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    with repository.transaction() as connection:
        repository.upsert_media_record(
            connection,
            MediaRecord(
                owner_type,
                owner_id,
                media_kind,
                "https://example.invalid/image",
                relative_path,
                digest,
                len(content),
                "image/png",
                1,
                1,
                None,
                None,
                "success",
            ),
        )


def _card(model, subject_id: int):  # type: ignore[no-untyped-def]
    return next(
        card
        for section in model.sections
        for card in section.subjects
        if card.subject_id == subject_id
    )
