"""Tests for verified, recoverable workspace media caching."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from bgm_side_b.api import BangumiApiError, ImageResponse, RequestMetrics
from bgm_side_b.database import Database
from bgm_side_b.media import MAX_MEDIA_CONCURRENCY, MediaCache, MediaTarget
from bgm_side_b.repository import (
    CharacterRecord,
    CharacterVoiceRecord,
    PersonRecord,
    SubjectCharacterRecord,
    SubjectRecord,
    SubjectRepository,
)


class FakeImageApi:
    def __init__(self, images: dict[str, ImageResponse]) -> None:
        self.images = images
        self.calls: list[str] = []
        self.failures: set[str] = set()
        self.metrics = RequestMetrics()

    def fetch_image(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        request_label: str = "image",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> ImageResponse:
        self.calls.append(url)
        if url in self.failures:
            raise BangumiApiError("image_network", "image request failed")
        response = self.images[url]
        if max_bytes is not None and len(response.content) > max_bytes:
            raise BangumiApiError("image_too_large", "image exceeds byte limit")
        return response


@pytest.fixture
def repository(tmp_path: Path) -> SubjectRepository:
    database = Database(tmp_path / "workspace" / "data" / "facts.sqlite3")
    database.migrate()
    return SubjectRepository(database)


def _image_response(
    url: str,
    image_format: str = "PNG",
    content_type: str = "image/png",
    color: str = "navy",
) -> ImageResponse:
    output = BytesIO()
    Image.new("RGB", (2, 3), color=color).save(output, format=image_format)
    return ImageResponse(output.getvalue(), content_type, url)


def _subject(subject_id: int) -> SubjectRecord:
    return SubjectRecord(subject_id, "tv", None, None, None, None, None)


def test_verified_cover_and_character_cache_uses_actual_image_format(
    tmp_path: Path, repository: SubjectRepository
) -> None:
    cover_url = "https://img.example/cover.jpg"
    character_url = "https://img.example/character.jpeg"
    api = FakeImageApi(
        {
            cover_url: _image_response(cover_url),
            character_url: _image_response(character_url),
        }
    )
    cache = MediaCache(repository, api, tmp_path / "workspace")

    cover = cache.sync_target(MediaTarget("subject", 1, "cover", cover_url))
    character = cache.sync_target(
        MediaTarget("character", 10, "character_image", character_url)
    )

    assert cover.status == character.status == "downloaded"
    cover_record = repository.get_media_record("subject", 1, "cover")
    character_record = repository.get_media_record("character", 10, "character_image")
    assert cover_record is not None and character_record is not None
    assert cover_record.local_path == "media/covers/1.png"
    assert character_record.local_path == "media/characters/10.png"
    assert cover_record.mime_type == "image/png"
    assert (tmp_path / "workspace" / cover_record.local_path).is_file()
    assert (tmp_path / "workspace" / character_record.local_path).is_file()
    assert ":" not in cover_record.local_path
    assert MAX_MEDIA_CONCURRENCY == 3
    assert cache.max_concurrency == 1


def test_media_validation_retries_invalidates_corruption_and_preserves_old_file(
    tmp_path: Path, repository: SubjectRepository
) -> None:
    old_url = "https://img.example/old.png"
    new_url = "https://img.example/new.png"
    api = FakeImageApi(
        {
            old_url: _image_response(old_url),
            new_url: _image_response(new_url),
        }
    )
    workspace = tmp_path / "workspace"
    cache = MediaCache(repository, api, workspace)
    target = MediaTarget("subject", 1, "cover", old_url)
    assert cache.sync_target(target).status == "downloaded"
    record = repository.get_media_record("subject", 1, "cover")
    assert record is not None and record.local_path is not None
    original = (workspace / record.local_path).read_bytes()

    assert cache.sync_target(target).status == "skipped"
    assert api.calls == [old_url]
    (workspace / record.local_path).write_bytes(
        _image_response(old_url, color="green").content
    )
    assert cache.sync_target(target).status == "downloaded"
    assert api.calls == [old_url, old_url]

    api.failures.add(new_url)
    failed = cache.sync_target(MediaTarget("subject", 1, "cover", new_url))
    updated = repository.get_media_record("subject", 1, "cover")
    assert failed.status == "failed"
    assert updated is not None and updated.status == "failed"
    assert updated.source_url == new_url
    assert updated.local_path == record.local_path
    assert (workspace / record.local_path).read_bytes() == original
    assert not list((workspace / "tmp").glob("*.part"))


def test_media_skips_missing_urls_and_rejects_html_or_corrupt_bytes(
    tmp_path: Path, repository: SubjectRepository
) -> None:
    html_url = "https://img.example/not-image"
    corrupt_url = "https://img.example/corrupt-image"
    api = FakeImageApi(
        {
            html_url: ImageResponse(b"<html></html>", "text/html", html_url),
            corrupt_url: ImageResponse(b"not image bytes", "image/png", corrupt_url),
        }
    )
    cache = MediaCache(repository, api, tmp_path / "workspace")

    missing = cache.sync_target(MediaTarget("subject", 1, "cover", None))
    invalid = cache.sync_target(MediaTarget("subject", 1, "cover", html_url))
    corrupt = cache.sync_target(MediaTarget("subject", 1, "cover", corrupt_url))

    assert missing.status == "skipped"
    assert invalid.status == "failed"
    assert corrupt.status == "failed"
    record = repository.get_media_record("subject", 1, "cover")
    assert record is not None and record.status == "failed"
    assert record.local_path is None


def test_force_images_redownloads_and_orphan_cleanup_keeps_shared_character_media(
    tmp_path: Path, repository: SubjectRepository
) -> None:
    cover_url = "https://img.example/cover.png"
    character_url = "https://img.example/character.png"
    api = FakeImageApi(
        {
            cover_url: _image_response(cover_url),
            character_url: _image_response(character_url),
        }
    )
    workspace = tmp_path / "workspace"
    cache = MediaCache(repository, api, workspace)
    with repository.transaction() as connection:
        repository.upsert_subject(connection, _subject(1))
        repository.upsert_subject(connection, _subject(2))
        repository.upsert_character(connection, CharacterRecord(10, "Lead", None, None))
        repository.upsert_person(connection, PersonRecord(20, "Cast", None))
        for subject_id in (1, 2):
            repository.replace_roles_snapshot(
                connection,
                subject_id,
                [SubjectCharacterRecord(10, "main", 0)],
                [CharacterVoiceRecord(10, 20, None, 0)],
            )
    cover_result = cache.sync_target(MediaTarget("subject", 1, "cover", cover_url))
    assert cover_result.status == "downloaded"
    assert cache.sync_target(
        MediaTarget("character", 10, "character_image", character_url)
    ).status == "downloaded"
    assert cache.sync_target(
        MediaTarget("character", 10, "character_image", character_url),
        force_images=True,
    ).status == "downloaded"
    assert api.calls.count(character_url) == 2

    with repository.transaction() as connection:
        assert repository.delete_subject(connection, 1)
    cleanup = cache.cleanup_orphaned()
    assert cleanup.deleted == 1
    assert not (workspace / "media/covers/1.png").exists()
    assert repository.get_media_record("character", 10, "character_image") is not None

    with repository.transaction() as connection:
        assert repository.delete_subject(connection, 2)
    cleanup = cache.cleanup_orphaned()
    assert cleanup.deleted == 1
    assert not (workspace / "media/characters/10.png").exists()
    assert repository.get_media_record("character", 10, "character_image") is None
