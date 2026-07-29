"""One path layer for file-safe local and repository-subpath Pages links."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath

from bgm_side_b.build.profiles import BuildProfile


@dataclass(frozen=True)
class PathResolver:
    """Generate only validated relative links between generated site files."""

    profile: BuildProfile

    def href(self, document: str, target: str) -> str:
        """Return a portable relative href from one generated file to another."""
        current = _site_path(document)
        destination = _site_path(target)
        relative = posixpath.relpath(destination.as_posix(), current.parent.as_posix())
        return "./" if relative == "." else relative

    def asset(self, document: str, asset_path: str) -> str:
        """Return a relative link to a hashed static asset."""
        return self.href(document, asset_path)

    def quarter(self, year: int, month: int) -> str:
        """Return the stable generated path for a quarter archive page."""
        return f"quarters/{year:04d}-{month:02d}/index.html"

    def subject(self, subject_id: int) -> str:
        """Return the stable generated path for a subject detail page."""
        if subject_id <= 0:
            raise ValueError("subject id must be positive")
        return f"subjects/{subject_id}/index.html"

    def external_subject(self, subject_id: int) -> str:
        """Return the sole permitted absolute business link."""
        if subject_id <= 0:
            raise ValueError("subject id must be positive")
        return f"https://bgm.tv/subject/{subject_id}"


def _site_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or path.name in {"", "."}
    ):
        raise ValueError("site path must be relative and cannot escape output")
    return path
