"""Stable archive-domain contracts shared by storage and later workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MediaFormat(StrEnum):
    """The only media formats admitted to the archive."""

    TV = "TV"
    MOVIE = "MOVIE"


@dataclass(frozen=True, order=True)
class Quarter:
    """One calendar archive quarter."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if self.year <= 0:
            raise ValueError("quarter year must be positive")
        if self.month not in {1, 4, 7, 10}:
            raise ValueError("quarter month must be one of 1, 4, 7, or 10")


class SourceType(StrEnum):
    """Deterministic normalized adaptation sources."""

    MANGA = "漫画改"
    LIGHT_NOVEL = "轻小说改"
    NOVEL = "小说改"
    GAME = "游戏改"
    VISUAL_NOVEL = "视觉小说改"
    ORIGINAL_ANIME = "原创动画"
    OTHER_ADAPTATION = "其他改编"
    UNKNOWN = "来源未知"


class QuarterAssignmentSource(StrEnum):
    """Whether archive ownership was inferred or explicitly assigned."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class JapaneseClassification(StrEnum):
    """A deterministic three-state Japanese-only classification."""

    ACCEPTED_JAPANESE = "ACCEPTED_JAPANESE"
    REJECTED_NON_JAPANESE = "REJECTED_NON_JAPANESE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class JapaneseDecision:
    """Classification plus the smallest structured evidence used to reach it."""

    classification: JapaneseClassification
    evidence_type: str | None = None
    evidence_value: str | None = None

    def __post_init__(self) -> None:
        if (self.evidence_type is None) != (self.evidence_value is None):
            raise ValueError("Japanese evidence type and value must be paired")
        if (
            self.classification is JapaneseClassification.ACCEPTED_JAPANESE
            and self.evidence_type is None
        ):
            raise ValueError("accepted Japanese subjects require structured evidence")
