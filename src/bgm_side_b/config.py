"""Typed loader for the formal site's exact display-tag whitelist."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TagRules:
    """Exact display-tag whitelist; aliases and synonyms are not supported."""

    allowed_tags: tuple[str, ...]


def load_tag_rules(allowed_path: Path) -> TagRules:
    """Load the exact display whitelist without alias normalization."""
    with allowed_path.open("rb") as file:
        data = tomllib.load(file)
    allowed = data.get("allowed_tags")
    if not isinstance(allowed, list) or not all(
        isinstance(item, str) and item for item in allowed
    ):
        raise ValueError("allowed_tags must be an array of non-empty strings")
    if len(set(allowed)) != len(allowed):
        raise ValueError("allowed_tags must not contain duplicates")
    return TagRules(tuple(allowed))
