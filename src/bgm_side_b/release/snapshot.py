"""Compact fact index and deterministic data-difference summaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path


def snapshot_index(
    models: Iterable[object], *, rules_hash: str, blacklist_hash: str
) -> dict[str, object]:
    """Create a compact index: ids/counts/hashes, never full facts or API payloads."""
    quarters: list[dict[str, object]] = []
    subjects: dict[str, dict[str, object]] = {}
    covers: dict[str, str] = {}
    for model in models:
        sections = []
        for section in model.sections:
            identifiers = [card.subject_id for card in section.subjects]
            sections.append({"kind": section.kind, "subject_ids": identifiers})
        quarters.append(
            {
                "key": f"{model.year:04d}-{model.month:02d}",
                "sections": sections,
            }
        )
        for detail in model.details:
            drawer = detail.drawer
            card = drawer.card
            roles = [
                (
                    character.character_id,
                    tuple(actor.person_id for actor in character.voice_actors),
                )
                for character in detail.characters
            ]
            subjects[str(card.subject_id)] = {
                "facts_hash": _hash(
                    (
                        card.subject_id,
                        card.media_format,
                        card.air_date,
                        card.declared_episode_count,
                        card.total_episode_count,
                        card.rating_score,
                        card.rating_count,
                        tuple(item.source for item in card.sources),
                        tuple(item.name for item in card.tags),
                    )
                ),
                "episode_hash": _hash(
                    tuple(
                        (episode.episode_id, episode.air_date, episode.duration_seconds)
                        for episode in detail.episodes
                    )
                ),
                "episode_count": len(detail.episodes),
                "roles_hash": _hash(roles),
                "role_count": len(detail.characters),
                "persons_hash": _hash(
                    tuple(person for _, people in roles for person in people)
                ),
                "person_count": sum(len(people) for _, people in roles),
            }
            if card.cover.content_hash:
                covers[str(card.subject_id)] = card.cover.content_hash
    return {
        "schema": 1,
        "quarters": sorted(quarters, key=lambda item: str(item["key"])),
        "subjects": dict(sorted(subjects.items(), key=lambda item: int(item[0]))),
        "covers": dict(sorted(covers.items(), key=lambda item: int(item[0]))),
        "rules_hash": rules_hash,
        "blacklist_hash": blacklist_hash,
    }


def diff_snapshots(
    previous: dict[str, object] | None, current: dict[str, object]
) -> dict[str, object]:
    """Summarise only intentional fact changes; first releases remain explicit."""
    current_subjects = _mapping(current, "subjects")
    if previous is None:
        return {
            "kind": "initial_snapshot",
            "quarters_added": len(_sequence(current, "quarters")),
            "quarters_removed": 0,
            "subjects_added": len(current_subjects),
            "subjects_removed": 0,
            "subjects_updated": 0,
            "episodes_added": sum(
                _int(item, "episode_count") for item in current_subjects.values()
            ),
            "episodes_removed": 0,
            "roles_added": sum(
                _int(item, "role_count") for item in current_subjects.values()
            ),
            "roles_removed": 0,
            "persons_added": sum(
                _int(item, "person_count") for item in current_subjects.values()
            ),
            "persons_removed": 0,
            "covers_changed": len(_mapping(current, "covers")),
            "rules_changed": bool(current.get("rules_hash")),
            "blacklist_changed": bool(current.get("blacklist_hash")),
            "orphan_cleanup": 0,
            "failure_summary": [],
        }
    previous_subjects = _mapping(previous, "subjects")
    shared = set(previous_subjects) & set(current_subjects)
    return {
        "kind": "data",
        "quarters_added": len(
            {str(item["key"]) for item in _sequence(current, "quarters")}
            - {str(item["key"]) for item in _sequence(previous, "quarters")}
        ),
        "quarters_removed": len(
            {str(item["key"]) for item in _sequence(previous, "quarters")}
            - {str(item["key"]) for item in _sequence(current, "quarters")}
        ),
        "subjects_added": len(set(current_subjects) - set(previous_subjects)),
        "subjects_removed": len(set(previous_subjects) - set(current_subjects)),
        "subjects_updated": sum(
            previous_subjects[key].get("facts_hash")
            != current_subjects[key].get("facts_hash")
            for key in shared
        ),
        "episodes_added": sum(
            max(
                0,
                _int(current_subjects[key], "episode_count")
                - _int(previous_subjects.get(key, {}), "episode_count"),
            )
            for key in current_subjects
        ),
        "episodes_removed": sum(
            max(
                0,
                _int(previous_subjects[key], "episode_count")
                - _int(current_subjects.get(key, {}), "episode_count"),
            )
            for key in previous_subjects
        ),
        "roles_added": sum(
            max(
                0,
                _int(current_subjects[key], "role_count")
                - _int(previous_subjects.get(key, {}), "role_count"),
            )
            for key in current_subjects
        ),
        "roles_removed": sum(
            max(
                0,
                _int(previous_subjects[key], "role_count")
                - _int(current_subjects.get(key, {}), "role_count"),
            )
            for key in previous_subjects
        ),
        "persons_added": sum(
            max(
                0,
                _int(current_subjects[key], "person_count")
                - _int(previous_subjects.get(key, {}), "person_count"),
            )
            for key in current_subjects
        ),
        "persons_removed": sum(
            max(
                0,
                _int(previous_subjects[key], "person_count")
                - _int(current_subjects.get(key, {}), "person_count"),
            )
            for key in previous_subjects
        ),
        "covers_changed": sum(
            _mapping(previous, "covers").get(key)
            != _mapping(current, "covers").get(key)
            for key in set(_mapping(previous, "covers"))
            | set(_mapping(current, "covers"))
        ),
        "rules_changed": previous.get("rules_hash") != current.get("rules_hash"),
        "blacklist_changed": previous.get("blacklist_hash")
        != current.get("blacklist_hash"),
        "orphan_cleanup": 0,
        "failure_summary": [],
    }


def read_snapshot(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("stored snapshot index is invalid") from error
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("stored snapshot index is invalid")
    return value


def write_snapshot(path: Path, index: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _hash(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _mapping(payload: dict[str, object], key: str) -> dict[str, dict[str, object]]:
    value = payload.get(key, {})
    return value if isinstance(value, dict) else {}


def _sequence(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload.get(key, [])
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key, 0)
    return value if isinstance(value, int) else 0
