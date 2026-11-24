"""Deterministic offline manifests over planned artifacts and their identities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from bgm_side_b.build.cover_preview import cover_preview_path
from bgm_side_b.build.fingerprint import fingerprint
from bgm_side_b.build.site_paths import APP_ASSETS, SHELL_RESOURCE_PATHS, quarter_paths
from bgm_side_b.build.site_projection import (
    ProjectionError,
    QuarterProjection,
    json_bytes,
)
from bgm_side_b.build.writer import ArtifactSpec


def _resource_signatures(
    resources: Iterable[Mapping[str, object]],
) -> list[tuple[object, object, object]]:
    return [
        (entry.get("url"), entry.get("content_hash"), entry.get("size_bytes"))
        for entry in resources
    ]


def offline_revision(
    quarter: str, data_revision: str, resources: Iterable[Mapping[str, object]]
) -> str:
    return fingerprint(
        {
            "quarter": quarter,
            "data_revision": data_revision,
            "resources": _resource_signatures(resources),
        }
    )


def shell_revision(resources: Iterable[Mapping[str, object]]) -> str:
    return fingerprint(_resource_signatures(resources))


def _manifest_resources(
    paths: Iterable[str], specs: Mapping[str, ArtifactSpec], *, name: str
) -> list[dict[str, object]]:
    resources: list[dict[str, object]] = []
    for relative in paths:
        spec = specs.get(relative)
        if spec is None or spec.content_hash is None or spec.size_bytes is None:
            raise ProjectionError(f"{name} resource metadata is missing: {relative}")
        resources.append(
            {
                "url": relative,
                "content_hash": spec.content_hash,
                "size_bytes": spec.size_bytes,
            }
        )
    return resources


def offline_manifest_bytes(
    quarter: QuarterProjection, specs: Mapping[str, ArtifactSpec]
) -> bytes:
    label = quarter.quarter
    page_path, data_path, _ = quarter_paths(label)
    covers = tuple(
        item.cover_url
        for item in (
            *quarter.tv_premiere,
            *quarter.tv_continuing,
            *quarter.movie_premiere,
        )
        if item.cover_url
    )
    originals = sorted({cover.split("?", 1)[0] for cover in covers})
    previews = sorted(
        {path for cover in covers if (path := cover_preview_path(cover)) is not None}
    )
    resources = _manifest_resources(
        (page_path, data_path, *APP_ASSETS, *originals, *previews),
        specs,
        name="offline",
    )
    data_revision = specs[data_path].content_hash
    assert data_revision is not None
    return json_bytes(
        {
            "quarter": label,
            "revision": offline_revision(label, data_revision, resources),
            "data_revision": data_revision,
            "resources": resources,
        }
    )


def pwa_shell_manifest(specs: Mapping[str, ArtifactSpec]) -> dict[str, object]:
    resources = _manifest_resources(SHELL_RESOURCE_PATHS, specs, name="PWA shell")
    return {"schema": 1, "revision": shell_revision(resources), "resources": resources}


def service_worker_bytes(source: bytes, revision: str) -> bytes:
    token = b"__BSB_SHELL_REVISION__"
    if source.count(token) != 1:
        raise ProjectionError("service worker shell revision token is invalid")
    return source.replace(token, revision.encode("ascii"))
