"""The two deliberately small static-output profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuildProfile:
    """The only allowed local-versus-Pages output differences."""

    name: str
    output_directory: str
    deployment_path: str
    include_character_images: bool
    derive_cover_webp: bool
    local_notice: bool

    def __post_init__(self) -> None:
        if self.name not in {"local", "pages"}:
            raise ValueError("build profile must be local or pages")
        if not self.output_directory or "/" in self.output_directory:
            raise ValueError("output directory must be one path component")
        normalized = _normalize_deployment_path(self.deployment_path)
        object.__setattr__(self, "deployment_path", normalized)


def local_profile() -> BuildProfile:
    """Return the full local profile used directly through ``file://``."""
    return BuildProfile("local", "local", "/", True, False, True)


def pages_profile(deployment_path: str = "/bangumi-side-b/") -> BuildProfile:
    """Return the lightweight Pages profile for one repository subpath."""
    return BuildProfile("pages", "pages", deployment_path, False, True, False)


def _normalize_deployment_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    if not candidate.startswith("/") or ".." in candidate.split("/"):
        raise ValueError("deployment path must be an absolute site path")
    return candidate.rstrip("/") + "/"
