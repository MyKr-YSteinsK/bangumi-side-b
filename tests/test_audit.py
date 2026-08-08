"""Read-only checks for the reduced first-release workspace."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from bgm_side_b.audit import ReleaseDataAuditor
from bgm_side_b.cli import main
from bgm_side_b.config import load_rules
from bgm_side_b.legacy_database import Database
from bgm_side_b.legacy_repository import (
    CharacterRecord,
    MediaRecord,
    RawTag,
    SubjectInfoboxItem,
    SubjectQuarter,
    SubjectRecord,
    SubjectRepository,
    SubjectTitle,
)

ROOT = Path(__file__).parents[1]


def test_audit_reports_missing_workspace_without_creating_it(tmp_path: Path) -> None:
    result = _auditor(tmp_path).audit()

    assert not result.passed
    assert result.failures[0].check == "workspace"
    assert not (tmp_path / "workspace").exists()


def test_empty_database_fails_without_creating_media(tmp_path: Path) -> None:
    database = _database(tmp_path)
    before = database.path.read_bytes()

    result = _auditor(tmp_path).audit()

    assert not result.passed
    assert {failure.check for failure in result.failures} == {"subjects"}
    assert result.build_marker_status == "missing"
    assert result.character_count == 0
    assert database.path.read_bytes() == before
    assert not (tmp_path / "workspace" / "media").exists()


def test_audit_accepts_only_a_japan_tv_subject_and_remains_read_only(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
    before = database.path.read_bytes()

    result = _auditor(tmp_path).audit()

    assert result.passed
    assert result.subject_count == 1
    assert result.out_of_scope_subjects == 0
    assert "第一版资料审计通过" in result.render()
    assert database.path.read_bytes() == before


def test_audit_cli_returns_a_non_mutating_success_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'audit'\n", "utf-8")
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
    monkeypatch.chdir(tmp_path)

    assert main(["audit"]) == 0
    assert "第一版资料审计通过" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("media_format", "appearance_kind", "country", "check"),
    [
        ("tv", "new", "Japan", "scope"),
        ("movie", "new", "Japan", "format"),
        ("tv", "new", "China", "country"),
        ("tv", "continuing", "Japan", "scope"),
    ],
)
def test_audit_rejects_scope_format_country_and_continuation_data(
    tmp_path: Path,
    media_format: str,
    appearance_kind: str,
    country: str,
    check: str,
) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(
            repository,
            connection,
            101,
            media_format,
            appearance_kind,
            country,
            year=2025 if check == "scope" and appearance_kind == "new" else 2026,
        )

    result = _auditor(tmp_path).audit()

    assert not result.passed
    assert check in {failure.check for failure in result.failures}


def test_audit_accepts_a_seasonal_tv_default_without_country_infobox(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(
            repository,
            connection,
            101,
            "tv",
            "new",
            None,
            subject_type=2,
        )

    result = _auditor(tmp_path).audit()

    assert result.passed
    assert result.subject_count == 1


def test_audit_rejects_role_data_and_character_media(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
        repository.upsert_character(
            connection, CharacterRecord(10, "Character", None, None)
        )
        repository.upsert_media_record(
            connection,
            MediaRecord(
                owner_type="character",
                owner_id=10,
                media_kind="character_image",
                source_url=None,
                local_path=None,
                content_hash=None,
                size_bytes=None,
                mime_type=None,
                width=None,
                height=None,
                downloaded_at=None,
                verified_at=None,
                status="failed",
            ),
        )

    result = _auditor(tmp_path).audit()

    checks = {failure.check for failure in result.failures}
    assert {"characters", "character_media"}.issubset(checks)


def test_audit_rejects_blacklist_residue_and_an_invalid_build_marker(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
    marker = tmp_path / "workspace" / "state" / "pages-build.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", "utf-8")

    result = _auditor(tmp_path, excluded_subject_ids=frozenset({101})).audit()

    checks = {failure.check for failure in result.failures}
    assert {"blacklist", "build_marker"}.issubset(checks)
    assert result.build_marker_status == "invalid"


def test_audit_reports_only_format_validity_for_a_build_marker(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
    marker = tmp_path / "workspace" / "state" / "pages-build.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema": 2,
                "candidate_id": "candidate",
                "source_commit": "commit",
                "app_version": "version",
                "profile": "pages",
                "business_content_hash": "content",
                "facts_snapshot_hash": "facts",
                "rules_hash": "rules",
                "blacklist_hash": "blacklist",
                "data_generation": 0,
                "deployment_path": "./",
            }
        ),
        "utf-8",
    )

    result = _auditor(tmp_path).audit()

    assert result.passed
    assert result.build_marker_status == "format-valid"
    assert "构建标记 format-valid" in result.render()


def test_audit_rejects_unsafe_cover_path_and_foreign_key_violation(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    repository = SubjectRepository(database)
    with repository.transaction() as connection:
        _subject(repository, connection, 101, "tv", "new", "Japan")
        connection.execute(
            """
            INSERT INTO media_files (
                owner_type, owner_id, media_kind, local_path, size_bytes, mime_type,
                width, height, status
            ) VALUES ('subject', 101, 'cover', '../outside.png', 1, 'image/png', 1, 1,
                      'success')
            """
        )
    connection = sqlite3.connect(database.path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO subject_titles (subject_id, title_kind, title, position) "
            "VALUES (999, 'preferred', 'orphan', 0)"
        )
        connection.commit()
    finally:
        connection.close()

    result = _auditor(tmp_path).audit()

    checks = {failure.check for failure in result.failures}
    assert {"cover_paths", "foreign_keys"}.issubset(checks)


def _database(root: Path) -> Database:
    database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
    database.migrate()
    return database


def _auditor(
    root: Path, *, excluded_subject_ids: frozenset[int] = frozenset()
) -> ReleaseDataAuditor:
    settings, _, _ = load_rules(ROOT / "config")
    settings = replace(settings, excluded_subject_ids=excluded_subject_ids)
    return ReleaseDataAuditor(root, settings)


def _subject(
    repository: SubjectRepository,
    connection: object,
    subject_id: int,
    media_format: str,
    appearance_kind: str,
    country: str | None,
    *,
    year: int = 2026,
    tags: tuple[str, ...] = (),
    subject_type: int | None = None,
) -> None:
    repository.upsert_subject(
        connection,
        SubjectRecord(
            subject_id,
            media_format,
            None,
            date(year, 4, 1),
            12,
            None,
            None,
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
    repository.replace_raw_tags(
        connection, subject_id, [RawTag(tag, 1) for tag in tags]
    )
    repository.replace_quarters(
        connection, subject_id, [SubjectQuarter(year, 4, appearance_kind)]
    )
