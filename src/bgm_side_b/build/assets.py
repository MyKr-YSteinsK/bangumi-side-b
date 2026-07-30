"""Hashed static resources and profile-specific media publication."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from bgm_side_b.build.models import MediaView
from bgm_side_b.build.profiles import BuildProfile

_PAGES_MAX_LONG_SIDE = 1200
_PAGES_WEBP_QUALITY = 89
_PAGES_WEBP_METHOD = 4


class AssetError(RuntimeError):
    """Raised when a build resource cannot be safely published."""


@dataclass(frozen=True)
class PublishedMedia:
    """A profile output path and dimensions, never an absolute local source."""

    relative_path: str
    width: int
    height: int
    mime_type: str


def publish_static_assets(
    source_directory: Path, output_directory: Path
) -> dict[str, str]:
    """Copy static files to content-hashed names without a frontend bundler."""
    if not source_directory.is_dir():
        raise AssetError("static source directory is missing")
    assets_directory = output_directory / "assets"
    published: dict[str, str] = {}
    sources = sorted(path for path in source_directory.rglob("*") if path.is_file())
    for source in sources:
        relative = source.relative_to(source_directory).as_posix()
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()[:12]
        suffix = source.suffix
        stem = source.stem
        target_name = f"{stem}.{digest}{suffix}"
        target = assets_directory / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        published[relative] = f"assets/{target_name}"
    return published


class MediaPublisher:
    """Copy local media or derive Pages covers from already verified source files."""

    def __init__(self, workspace_directory: Path, output_directory: Path) -> None:
        self.workspace_directory = workspace_directory.resolve()
        self.output_directory = output_directory

    def publish_cover(
        self, subject_id: int, media: MediaView, profile: BuildProfile
    ) -> PublishedMedia | None:
        """Publish one local original or a bounded Pages WebP derivative."""
        if subject_id <= 0 or not media.is_available:
            return None
        source = _workspace_media_path(self.workspace_directory, media)
        if source is None:
            return None
        if profile.derive_cover_webp:
            return self._derive_pages_cover(source, media)
        return self._copy_original(source, media)

    def _copy_original(self, source: Path, media: MediaView) -> PublishedMedia:
        relative = PurePosixPath(media.relative_path or "")
        target = self.output_directory / Path(relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if media.width is None or media.height is None or media.mime_type is None:
            raise AssetError("verified media dimensions are missing")
        return PublishedMedia(
            relative.as_posix(), media.width, media.height, media.mime_type
        )

    def _derive_pages_cover(self, source: Path, media: MediaView) -> PublishedMedia:
        digest = media.content_hash or _file_hash(source)
        relative = PurePosixPath(f"media/covers/{digest}.1200.q89.webp")
        target = self.output_directory / Path(relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            try:
                with Image.open(source) as image:
                    image.load()
                    copy = image.copy()
            except (OSError, UnidentifiedImageError) as error:
                raise AssetError("verified cover cannot be derived") from error
            copy.thumbnail(
                (_PAGES_MAX_LONG_SIDE, _PAGES_MAX_LONG_SIDE), Image.Resampling.LANCZOS
            )
            copy.save(
                target,
                format="WEBP",
                quality=_PAGES_WEBP_QUALITY,
                method=_PAGES_WEBP_METHOD,
            )
        try:
            with Image.open(target) as derived:
                width, height = derived.size
        except (OSError, UnidentifiedImageError) as error:
            raise AssetError("Pages cover derivation failed") from error
        return PublishedMedia(relative.as_posix(), width, height, "image/webp")


def assert_pages_media_policy(output_directory: Path) -> None:
    """Fail closed if a Pages tree contains forbidden character-media output."""
    characters = output_directory / "media" / "characters"
    if characters.exists():
        raise AssetError("Pages output contains character media")


def generate_pwa_icons(output_directory: Path) -> dict[str, str]:
    """Create deterministic geometric Side B icons without fonts or third-party art."""
    icons = {
        "icon-192.png": (192, False),
        "icon-512.png": (512, False),
        "icon-512-maskable.png": (512, True),
    }
    destination = output_directory / "icons"
    destination.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}
    for filename, (size, maskable) in icons.items():
        image = Image.new("RGB", (size, size), "#f5f1e8")
        pixels = image.load()
        center = size / 2
        outer = size * (0.34 if maskable else 0.42)
        inner = size * (0.24 if maskable else 0.31)
        for y in range(size):
            for x in range(size):
                distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
                if inner <= distance <= outer:
                    pixels[x, y] = (23, 32, 29)
                elif outer - size * 0.025 <= distance <= outer:
                    pixels[x, y] = (138, 49, 71)
        image.save(destination / filename, format="PNG", optimize=True)
        generated[filename] = f"icons/{filename}"
    return generated


def _workspace_media_path(workspace: Path, media: MediaView) -> Path | None:
    if media.relative_path is None:
        return None
    try:
        relative = PurePosixPath(media.relative_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            return None
        source = (workspace / Path(relative.as_posix())).resolve()
        if not source.is_relative_to(workspace) or not source.is_file():
            return None
        if media.content_hash and _file_hash(source) != media.content_hash:
            return None
    except OSError:
        return None
    return source


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
