"""Verified, workspace-local cache for subject covers and character images."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from bgm_side_b.api import BangumiApiError, ImageResponse
from bgm_side_b.progress import NullProgressReporter, ProgressReporter
from bgm_side_b.repository import MediaRecord, SubjectRepository, SyncState

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MEDIA_CONCURRENCY = 3

_FORMAT_EXTENSIONS = {
    "AVIF": "avif",
    "BMP": "bmp",
    "GIF": "gif",
    "ICO": "ico",
    "JPEG": "jpg",
    "PNG": "png",
    "TIFF": "tiff",
    "WEBP": "webp",
}


class ImageFetcher(Protocol):
    """The narrow API surface used by the on-disk media cache."""

    def fetch_image(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        request_label: str = "image",
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> ImageResponse:
        """Return an already bounded binary image response."""


class MediaValidationError(RuntimeError):
    """A safe cache validation failure without local path disclosure."""

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary
        super().__init__(summary)


@dataclass(frozen=True)
class MediaTarget:
    """One supported owner/kind pair and its currently advertised image URL."""

    owner_type: str
    owner_id: int
    media_kind: str
    source_url: str | None


@dataclass(frozen=True)
class MediaResult:
    """One cache attempt outcome suitable for sync aggregation."""

    status: str
    retries: int = 0
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class MediaCleanup:
    """Orphan cache cleanup counts and safe failure codes."""

    deleted: int
    failures: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _ImageFacts:
    content_hash: str
    size_bytes: int
    mime_type: str
    width: int
    height: int
    extension: str


class MediaCache:
    """Keep only decoded, hashed image bytes beneath one workspace directory.

    Calls are intentionally serial. One active download is below the maximum of
    three permitted concurrent image downloads, and keeps file/database changes
    simple and independently recoverable.
    """

    def __init__(
        self,
        repository: SubjectRepository,
        api: ImageFetcher,
        workspace_directory: Path,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.repository = repository
        self.api = api
        self.workspace_directory = workspace_directory.resolve()
        self.reporter = reporter or NullProgressReporter()
        self.max_concurrency = 1

    def sync_target(
        self, target: MediaTarget, *, force_images: bool = False
    ) -> MediaResult:
        """Verify or download one image without replacing a prior valid file early."""
        _validate_target(target)
        existing = self.repository.get_media_record(
            target.owner_type, target.owner_id, target.media_kind
        )
        source_url = target.source_url or (existing.source_url if existing else None)
        request_label = (
            "cover-image" if target.media_kind == "cover" else "character-image"
        )
        self.reporter.progress(
            stage=request_label,
            message="处理媒体",
            entity_type=target.owner_type,
            entity_id=target.owner_id,
        )
        if source_url is None:
            return MediaResult("skipped")
        if (
            not force_images
            and existing is not None
            and existing.status == "success"
            and existing.source_url == source_url
            and self._record_file_is_valid(existing)
        ):
            return MediaResult("skipped")

        retries_before = _image_retries(self.api)
        try:
            with self.reporter.activity(
                stage=request_label,
                message="等待图片下载",
                entity_type=target.owner_type,
                entity_id=target.owner_id,
            ):
                response = self.api.fetch_image(
                    source_url,
                    max_bytes=MAX_IMAGE_BYTES,
                    request_label=request_label,
                    entity_type=target.owner_type,
                    entity_id=target.owner_id,
                )
            facts = _image_facts(response)
            local_path = _local_path(target, facts.extension)
            self._write_verified_file(local_path, response.content)
            self._store_success(target, source_url, local_path, facts)
        except (BangumiApiError, MediaValidationError, OSError) as error:
            code, summary = _safe_error(error)
            self._store_failure(target, source_url, existing, code, summary)
            return MediaResult(
                "failed", _image_retries(self.api) - retries_before, code, summary
            )
        try:
            self._remove_replaced_file(existing, local_path)
        except OSError:
            return MediaResult(
                "downloaded",
                _image_retries(self.api) - retries_before,
                "media_cleanup_failed",
                "replaced media cleanup failed",
            )
        return MediaResult("downloaded", _image_retries(self.api) - retries_before)

    def cleanup_orphaned(self) -> MediaCleanup:
        """Delete files for database-orphaned media and always drop their records."""
        records = self.repository.list_orphaned_media_records()
        deleted = 0
        failures: list[tuple[str, str]] = []
        for record in records:
            try:
                self._remove_file(record.local_path)
            except OSError:
                failures.append((record.media_kind, "media_cleanup_failed"))
            with self.repository.transaction() as connection:
                self.repository.delete_media_record(connection, record)
            deleted += 1
        return MediaCleanup(deleted, tuple(failures))

    def _record_file_is_valid(self, record: MediaRecord) -> bool:
        if record.local_path is None or record.content_hash is None:
            return False
        try:
            path = self._workspace_path(record.local_path)
            content = path.read_bytes()
            facts = _image_facts_from_content(content)
        except (MediaValidationError, OSError):
            return False
        return (
            facts.content_hash == record.content_hash
            and facts.size_bytes == record.size_bytes
            and facts.mime_type == record.mime_type
            and facts.width == record.width
            and facts.height == record.height
        )

    def _write_verified_file(self, local_path: str, content: bytes) -> None:
        destination = self._workspace_path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_directory = self.workspace_directory / "tmp"
        tmp_directory.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=tmp_directory, prefix="image-", suffix=".part"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _store_success(
        self,
        target: MediaTarget,
        source_url: str,
        local_path: str,
        facts: _ImageFacts,
    ) -> None:
        timestamp = _utc_timestamp()
        with self.repository.transaction() as connection:
            self.repository.upsert_media_record(
                connection,
                MediaRecord(
                    target.owner_type,
                    target.owner_id,
                    target.media_kind,
                    source_url,
                    local_path,
                    facts.content_hash,
                    facts.size_bytes,
                    facts.mime_type,
                    facts.width,
                    facts.height,
                    timestamp,
                    timestamp,
                    "success",
                ),
            )
            self.repository.write_sync_state(
                connection,
                SyncState(
                    target.owner_type,
                    target.owner_id,
                    _state_data_type(target),
                    "success",
                    timestamp,
                    timestamp,
                ),
            )

    def _store_failure(
        self,
        target: MediaTarget,
        source_url: str,
        existing: MediaRecord | None,
        code: str,
        summary: str,
    ) -> None:
        previous = self.repository.get_sync_state(
            target.owner_type, target.owner_id, _state_data_type(target)
        )
        timestamp = _utc_timestamp()
        record = (
            replace(existing, source_url=source_url, status="failed", verified_at=None)
            if existing is not None
            else MediaRecord(
                target.owner_type,
                target.owner_id,
                target.media_kind,
                source_url,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "failed",
            )
        )
        with self.repository.transaction() as connection:
            self.repository.upsert_media_record(connection, record)
            self.repository.write_sync_state(
                connection,
                SyncState(
                    target.owner_type,
                    target.owner_id,
                    _state_data_type(target),
                    "failed",
                    timestamp,
                    failure_count=(previous.failure_count if previous else 0) + 1,
                    error_code=code,
                    error_summary=summary,
                ),
            )

    def _remove_replaced_file(
        self, existing: MediaRecord | None, current_local_path: str
    ) -> None:
        if existing is None or existing.local_path == current_local_path:
            return
        self._remove_file(existing.local_path)

    def _remove_file(self, local_path: str | None) -> None:
        if local_path is None:
            return
        self._workspace_path(local_path).unlink(missing_ok=True)

    def _workspace_path(self, local_path: str) -> Path:
        relative = PurePosixPath(local_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise MediaValidationError("unsafe_media_path", "media path is unsafe")
        candidate = (self.workspace_directory / Path(relative.as_posix())).resolve()
        if not candidate.is_relative_to(self.workspace_directory):
            raise MediaValidationError("unsafe_media_path", "media path is unsafe")
        return candidate


def _validate_target(target: MediaTarget) -> None:
    expected = "cover" if target.owner_type == "subject" else "character_image"
    if target.owner_type not in {"subject", "character"} or target.owner_id <= 0:
        raise ValueError("media target owner is invalid")
    if target.media_kind != expected:
        raise ValueError("media target kind does not match owner")


def _image_facts(response: ImageResponse) -> _ImageFacts:
    declared_mime = (response.content_type or "").split(";", 1)[0].strip().lower()
    if not declared_mime.startswith("image/"):
        raise MediaValidationError("invalid_content_type", "response is not an image")
    return _image_facts_from_content(response.content)


def _image_facts_from_content(content: bytes) -> _ImageFacts:
    if not content:
        raise MediaValidationError("invalid_image", "image content is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise MediaValidationError("image_too_large", "image exceeds byte limit")
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image.load()
            image_format = image.format
            width, height = image.size
    except (OSError, UnidentifiedImageError) as error:
        raise MediaValidationError(
            "invalid_image", "image could not be decoded"
        ) from error
    if image_format is None or image_format not in _FORMAT_EXTENSIONS:
        raise MediaValidationError("unsupported_image", "image format is unsupported")
    mime_type = Image.MIME.get(image_format)
    if mime_type is None or width <= 0 or height <= 0:
        raise MediaValidationError("invalid_image", "image metadata is invalid")
    return _ImageFacts(
        hashlib.sha256(content).hexdigest(),
        len(content),
        mime_type,
        width,
        height,
        _FORMAT_EXTENSIONS[image_format],
    )


def _local_path(target: MediaTarget, extension: str) -> str:
    folder = "covers" if target.owner_type == "subject" else "characters"
    return f"media/{folder}/{target.owner_id}.{extension}"


def _state_data_type(target: MediaTarget) -> str:
    return "cover_image" if target.owner_type == "subject" else "character_image"


def _image_retries(api: ImageFetcher) -> int:
    metrics = getattr(api, "metrics", None)
    value = getattr(metrics, "image_retries", 0)
    return value if isinstance(value, int) else 0


def _safe_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, (BangumiApiError, MediaValidationError)):
        return error.code, error.summary
    return "media_io_error", "media file operation failed"


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
