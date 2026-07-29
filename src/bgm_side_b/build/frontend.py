"""Safe, deterministic data embedded in quarter pages for native JavaScript."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict

from bgm_side_b.build.models import BuildQuarter, SubjectDrawer


def drawer_json(
    quarter: BuildQuarter,
    cover_hrefs: Mapping[int, str] | None = None,
) -> str:
    """Serialize quick-drawer data without allowing a script-tag escape."""
    hrefs = cover_hrefs or {}
    payload = {
        str(detail.drawer.card.subject_id): _drawer_payload(detail.drawer, hrefs)
        for detail in quarter.details
    }
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _drawer_payload(
    drawer: SubjectDrawer, cover_hrefs: Mapping[int, str]
) -> dict[str, object]:
    card = drawer.card
    return {
        "id": card.subject_id,
        "preferred_title": card.preferred_title,
        "original_title": card.original_title,
        "aliases": card.aliases,
        "summary": drawer.summary,
        "media_format": card.media_format,
        "declared_episode_count": card.declared_episode_count,
        "total_episode_count": card.total_episode_count,
        "air_date": card.air_date.isoformat() if card.air_date else None,
        "end_date": drawer.end_date.isoformat() if drawer.end_date else None,
        "rating_score": card.rating_score,
        "rating_count": card.rating_count,
        "sources": [asdict(source) for source in card.sources],
        "tags": [asdict(tag) for tag in card.tags],
        "cover_href": cover_hrefs.get(card.subject_id),
        "bangumi_href": f"https://bgm.tv/subject/{card.subject_id}",
    }
