"""Preview identity and bounded WebP generation."""

from __future__ import annotations

import hashlib
import re
import warnings
from io import BytesIO
from pathlib import Path

import PIL
from PIL import Image, ImageOps, features

from bgm_side_b.build.errors import BuildError
from bgm_side_b.build.fingerprint import fingerprint
from bgm_side_b.media import MAX_COVER_EDGE, MAX_IMAGE_BYTES

_COVER_URL = re.compile(r"covers/([1-9][0-9]*)\.webp\?v=([0-9a-f]{64})")
ORIGINAL_COVER_PATH = re.compile(r"covers/[1-9][0-9]*\.webp")
PREVIEW_PATH = re.compile(r"covers/previews/[0-9a-f]{64}\.webp")
COVER_PREVIEW_RULE = {
    "version": 1,
    "max_edge": 640,
    "quality": 78,
    "method": 6,
    "exact": True,
    "resampling": "LANCZOS",
    "orientation": "exif-transpose",
    "color": "RGB-or-RGBA-preserve-ICC",
    "fallback": "original-bytes-small-animated-no-byte-benefit",
    "pillow": PIL.__version__,
    "webp": features.version("webp"),
}


def cover_preview_rule_id() -> str:
    return fingerprint(COVER_PREVIEW_RULE)


def cover_preview_path(cover: object, *, rule: str | None = None) -> str | None:
    match = _COVER_URL.fullmatch(cover) if isinstance(cover, str) else None
    if match is None:
        return None
    identity_input = f"{rule or cover_preview_rule_id()}\n{match[2]}".encode("ascii")
    identity = hashlib.sha256(identity_input).hexdigest()
    return f"covers/previews/{identity}.webp"


def validate_preview_image(content: bytes) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if (
                    image.format != "WEBP"
                    or min(image.size) <= 0
                    or max(image.size) > MAX_COVER_EDGE
                ):
                    raise BuildError("preview source is not a bounded verified WebP")
                image.load()
    except (
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise BuildError("preview image cannot be safely decoded") from error


def cover_preview_bytes(source: Path, content_hash: str, size_bytes: int) -> bytes:
    if size_bytes <= 0 or size_bytes > MAX_IMAGE_BYTES:
        raise BuildError("preview source exceeds the cover byte limit")
    try:
        with source.open("rb") as stream:
            content = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError as error:
        raise BuildError("preview source is unavailable") from error
    if (
        len(content) != size_bytes
        or hashlib.sha256(content).hexdigest() != content_hash
    ):
        raise BuildError("preview source identity is invalid")
    validate_preview_image(content)
    try:
        with Image.open(BytesIO(content)) as source_image:
            edge = int(COVER_PREVIEW_RULE["max_edge"])
            if max(source_image.size) <= edge or source_image.is_animated:
                return content
            source_image.load()
            image = ImageOps.exif_transpose(source_image)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(
                output,
                format="WEBP",
                quality=int(COVER_PREVIEW_RULE["quality"]),
                method=int(COVER_PREVIEW_RULE["method"]),
                exact=bool(COVER_PREVIEW_RULE["exact"]),
                icc_profile=source_image.info.get("icc_profile", b""),
            )
            preview = output.getvalue()
    except (OSError, ValueError) as error:
        raise BuildError("preview encoding failed") from error
    validate_preview_image(preview)
    return preview if len(preview) < len(content) else content
