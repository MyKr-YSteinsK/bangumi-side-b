"""Strict release-control JSON checks used before Git publication."""

from __future__ import annotations

from bgm_side_b.release.manifest import ManifestError, validate_manifest_payload

_CHANGE_KINDS = frozenset({"initial", "system", "data", "system_and_data", "none"})
_NO_DATA_CHANGE = "资料无结构化变化"


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
    if payload["change_kind"] not in _CHANGE_KINDS:
        raise ManifestError("release metadata change kind is invalid")
    system = payload["summary"].get("system")
    data = payload["summary"].get("data")
    if _summary_lines(system) is None or _summary_lines(data) is None:
        raise ManifestError("release metadata summary is invalid")
    has_system = bool(system)
    has_data = any(line != _NO_DATA_CHANGE for line in data)
    kind = payload["change_kind"]
    if kind == "initial" or kind == "system_and_data":
        if not has_system or not has_data:
            raise ManifestError("release metadata change summary conflicts with kind")
    elif kind == "system":
        if not has_system or has_data:
            raise ManifestError("release metadata change summary conflicts with kind")
    elif kind == "data":
        if has_system or not has_data:
            raise ManifestError("release metadata change summary conflicts with kind")
    elif kind == "none" and (has_system or has_data):
        raise ManifestError("release metadata change summary conflicts with kind")
    if not isinstance(payload["quarter_count"], int) or payload["quarter_count"] < 0:
        raise ManifestError("release metadata quarter count is invalid")
    if not isinstance(payload["subject_count"], int) or payload["subject_count"] < 0:
        raise ManifestError("release metadata subject count is invalid")
    return payload


def _summary_lines(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(
        isinstance(line, str) and line.strip() for line in value
    ):
        return None
    return value


__all__ = ["ManifestError", "validate_manifest_payload", "validate_release_payload"]
