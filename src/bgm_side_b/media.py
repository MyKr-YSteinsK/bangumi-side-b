"""The single final-cover pipeline for the clean archive fact store."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError

from bgm_side_b.api import BangumiApiError, ImageResponse
from bgm_side_b.repository import CoverRecord, SubjectSnapshot

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 36_000_000
MAX_IMAGE_DIMENSION = 12_000
MAX_COVER_EDGE = 1200
WEBP_QUALITY = 82
MAX_COVER_CONCURRENCY = 4


class CoverFetcher(Protocol):
    """The one bounded image operation the cover pipeline needs."""

    def fetch_image(self, url: str, *, max_bytes: int) -> ImageResponse:
        """Return a bounded binary image response."""


@dataclass(frozen=True)
class CoverResult:
    """One download/reuse/missing outcome without paths or response bodies."""

    status: str
    cover: CoverRecord | None
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class CoverRemovalBatch:
    """A recoverable set of cover moves waiting for a database commit."""

    quarantine: Path
    entries: tuple[tuple[Path, Path], ...]

    def restore(self) -> None:
        for source, parked in reversed(self.entries):
            if not parked.exists():
                if source.exists():
                    continue
                raise OSError(f"cover recovery source is missing: {source}")
            try:
                parked.replace(source)
            except OSError:
                if source.exists() and not parked.exists():
                    continue
                raise
        self._remove_quarantine()

    def finalize(self) -> None:
        self._remove_quarantine()

    def _remove_quarantine(self) -> None:
        try:
            shutil.rmtree(self.quarantine)
        except FileNotFoundError:
            return


class CoverStore:
    """Write only ``workspace/covers/<id>.webp`` after complete validation."""

    def __init__(
        self, covers_directory: Path, fetcher: CoverFetcher | None
    ) -> None:
        self.covers_directory = covers_directory
        self.fetcher = fetcher

    def sync_subject(
        self,
        snapshot: SubjectSnapshot,
        source_url: str | None,
        source_variant: str | None,
    ) -> CoverResult:
        """Reuse a valid matching final cover or atomically replace it with WebP."""
        if source_url is None or source_variant is None:
            return CoverResult("missing", None, "cover_missing", "no usable cover URL")
        existing = snapshot.cover
        destination = self._destination(snapshot.subject.subject_id)
        if (
            existing is not None
            and existing.source_url == source_url
            and existing.source_variant == source_variant
            and self._valid_existing(destination, existing)
        ):
            return CoverResult("reused", existing)
        try:
            if self.fetcher is None:
                raise OSError("cover fetcher is unavailable")
            response = self.fetcher.fetch_image(source_url, max_bytes=MAX_IMAGE_BYTES)
            content, width, height = _webp_bytes(response.content)
            cover = CoverRecord(
                source_url,
                source_variant,
                hashlib.sha256(content).hexdigest(),
                width,
                height,
                len(content),
            )
            self._atomic_write(destination, content)
        except (BangumiApiError, CoverValidationError, OSError) as error:
            code, summary = _cover_error(error)
            return CoverResult("failed", None, code, summary)
        return CoverResult("downloaded", cover)

    def remove_subject_cover(self, subject_id: int) -> None:
        """Remove only the known final cover path for a blacklisted subject."""
        self._destination(subject_id).unlink(missing_ok=True)

    def quarantine_subject_covers(
        self, subject_ids: set[int] | frozenset[int]
    ) -> CoverRemovalBatch:
        """Move known covers aside until the blacklist transaction commits."""
        quarantine = self.covers_directory / f".blacklist-{uuid.uuid4().hex}"
        quarantine.mkdir(parents=True, exist_ok=False)
        entries: list[tuple[Path, Path]] = []
        try:
            for subject_id in sorted(subject_ids):
                source = self._destination(subject_id)
                if not source.exists():
                    continue
                parked = quarantine / source.name
                try:
                    source.replace(parked)
                except OSError:
                    if parked.exists() and not source.exists():
                        entries.append((source, parked))
                        continue
                    raise
                entries.append((source, parked))
        except BaseException:
            batch = CoverRemovalBatch(quarantine, tuple(entries))
            try:
                batch.restore()
            except OSError as recovery_error:
                raise OSError(
                    "cover quarantine failed and recovery is incomplete"
                ) from recovery_error
            raise
        return CoverRemovalBatch(quarantine, tuple(entries))

    def _destination(self, subject_id: int) -> Path:
        if subject_id <= 0:
            raise ValueError("subject id must be positive")
        return self.covers_directory / f"{subject_id}.webp"

    def _valid_existing(self, path: Path, metadata: CoverRecord) -> bool:
        try:
            content = path.read_bytes()
            width, height = _validated_webp_dimensions(content)
        except (CoverValidationError, OSError):
            return False
        return (
            hashlib.sha256(content).hexdigest() == metadata.content_hash
            and len(content) == metadata.size_bytes
            and width == metadata.width
            and height == metadata.height
            and max(width, height) <= MAX_COVER_EDGE
        )

    def _atomic_write(self, destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.stem}-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            _validated_webp_dimensions(temporary.read_bytes())
            temporary.replace(destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


class CoverValidationError(RuntimeError):
    """A cover body could not become a valid final archive WebP."""

    def __init__(self, code: str, summary: str) -> None:
        self.code = code
        self.summary = summary
        super().__init__(summary)


def _webp_bytes(content: bytes) -> tuple[bytes, int, int]:
    if not content:
        raise CoverValidationError("cover_empty", "cover body is empty")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                _validate_decoded_dimensions(source)
                source.load()
                _validate_decoded_dimensions(source)
                image = source.convert(
                    "RGBA" if "A" in source.getbands() else "RGB"
                )
                _validate_decoded_dimensions(image)
    except CoverValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise CoverValidationError(
            "cover_dimensions", "cover decoded dimensions exceed safety limit"
        ) from error
    except (UnidentifiedImageError, OSError) as error:
        raise CoverValidationError(
            "cover_decode", "cover image cannot be decoded"
        ) from error
    if max(image.size) > MAX_COVER_EDGE:
        image.thumbnail((MAX_COVER_EDGE, MAX_COVER_EDGE), Image.Resampling.LANCZOS)
        _validate_decoded_dimensions(image)
    output = BytesIO()
    try:
        image.save(output, format="WEBP", quality=WEBP_QUALITY, method=6)
    except OSError as error:
        raise CoverValidationError(
            "cover_encode", "cover image cannot be encoded"
        ) from error
    encoded = output.getvalue()
    width, height = _validated_webp_dimensions(encoded)
    if max(width, height) > MAX_COVER_EDGE:
        raise CoverValidationError("cover_dimensions", "cover image exceeds final size")
    return encoded, width, height


def _validated_webp_dimensions(content: bytes) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format != "WEBP":
                    raise CoverValidationError("cover_format", "cover is not WebP")
                _validate_decoded_dimensions(image)
                image.load()
                _validate_decoded_dimensions(image)
                return image.size
    except CoverValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise CoverValidationError(
            "cover_dimensions", "cover decoded dimensions exceed safety limit"
        ) from error
    except (UnidentifiedImageError, OSError) as error:
        raise CoverValidationError(
            "cover_decode", "cover image cannot be decoded"
        ) from error


def _validate_decoded_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise CoverValidationError("cover_dimensions", "cover dimensions are invalid")
    if (
        width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise CoverValidationError(
            "cover_dimensions", "cover decoded dimensions exceed safety limit"
        )


def _cover_error(error: BaseException) -> tuple[str, str]:
    if isinstance(error, (BangumiApiError, CoverValidationError)):
        return error.code, error.summary
    return "cover_io", "cover file operation failed"
