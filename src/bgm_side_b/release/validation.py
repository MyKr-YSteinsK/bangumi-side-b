"""Strict release-control JSON checks used before Git publication."""

from __future__ import annotations

from bgm_side_b.release.manifest import ManifestError, validate_manifest_payload


def validate_release_payload(payload: object) -> dict[str, object]:
    """Validate the intentionally small control file consumed by the PWA."""
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ManifestError("release metadata schema is invalid")
    required = {
        "release_version",
        "app_version",
        "generated_at",
        "published_at",
        "quarter_count",
        "subject_count",
        "total_bytes",
        "content_hash",
        "manifest_url",
        "manifest_sha256",
        "change_kind",
        "summary",
    }
    if not required.issubset(payload):
        raise ManifestError("release metadata fields are incomplete")
    if payload["manifest_url"] != "snapshot-manifest.json":
        raise ManifestError("release metadata manifest URL is invalid")
    if not isinstance(payload["summary"], dict):
        raise ManifestError("release metadata summary is invalid")
    if not isinstance(payload["quarter_count"], int) or payload["quarter_count"] < 0:
        raise ManifestError("release metadata quarter count is invalid")
    if not isinstance(payload["subject_count"], int) or payload["subject_count"] < 0:
        raise ManifestError("release metadata subject count is invalid")
    return payload


__all__ = ["ManifestError", "validate_manifest_payload", "validate_release_payload"]
