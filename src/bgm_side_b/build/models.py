"""Template-independent immutable models for the static archive."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MediaView:
    """One verified workspace-relative image available to a build profile."""

    media_kind: str
    relative_path: str | None
    content_hash: str | None
    mime_type: str | None
    width: int | None
    height: int | None

    @property
    def is_available(self) -> bool:
        """Return whether a verified file can be safely copied into output."""
        return self.relative_path is not None


@dataclass(frozen=True)
class TagView:
    """One configured community tag selected from raw stored facts."""

    name: str


@dataclass(frozen=True)
class SourceView:
    """One canonical source classification with duplicate evidence removed."""

    source: str


@dataclass(frozen=True)
class SubjectCard:
    """The complete fact projection needed by a quarter-card template."""

    subject_id: int
    section: str
    media_format: str
    preferred_title: str
    original_title: str | None
    aliases: tuple[str, ...]
    air_date: date | None
    declared_episode_count: int | None
    total_episode_count: int | None
    stored_main_episode_count: int
    rating_score: float | None
    rating_count: int | None
    sources: tuple[SourceView, ...]
    tags: tuple[TagView, ...]
    cover: MediaView
    search_text: str
    sort_score_desc: int = 0
    sort_score_asc: int = 0
    sort_votes_desc: int = 0
    sort_votes_asc: int = 0

    @property
    def card_sources(self) -> tuple[SourceView, ...]:
        """Return the source subset shown on dense card layouts."""
        return self.sources[:2]

    @property
    def source_overflow_count(self) -> int:
        """Return the number of source labels omitted from the compact card."""
        return max(0, len(self.sources) - len(self.card_sources))

    @property
    def card_tags(self) -> tuple[TagView, ...]:
        """Return the tag subset shown on dense card layouts."""
        return self.tags[:2]


@dataclass(frozen=True)
class SubjectDrawer:
    """The full, non-episode facts embedded for a quarter-page quick drawer."""

    card: SubjectCard
    summary: str | None
    end_date: date | None
    permanent_year: int | None
    permanent_month: int | None
    entered_year: int
    entered_month: int


@dataclass(frozen=True)
class EpisodeView:
    """One stored main-story episode in its deterministic display order."""

    episode_id: int
    episode_number: float | None
    sort_number: float | None
    chinese_title: str | None
    original_title: str | None
    air_date: date | None
    duration_seconds: int | None
    position: int


@dataclass(frozen=True)
class VoiceActorView:
    """One subject-scoped voice actor relation without image data."""

    person_id: int
    preferred_name: str
    original_name: str | None
    language: str | None
    position: int


@dataclass(frozen=True)
class CharacterView:
    """One main character and only its voice relations in this subject."""

    character_id: int
    preferred_name: str
    original_name: str | None
    summary: str | None
    image: MediaView
    voice_actors: tuple[VoiceActorView, ...]
    position: int


@dataclass(frozen=True)
class SubjectDetailPage:
    """The independent full-detail projection for a single subject page."""

    drawer: SubjectDrawer
    episodes: tuple[EpisodeView, ...]
    characters: tuple[CharacterView, ...]


@dataclass(frozen=True)
class QuarterSection:
    """One non-empty quarter grouping in its fixed editorial order."""

    kind: str
    label: str
    subjects: tuple[SubjectCard, ...]


@dataclass(frozen=True)
class QuarterNavigation:
    """One available database quarter for future static navigation rendering."""

    year: int
    month: int
    has_subjects: bool
    is_current: bool


@dataclass(frozen=True)
class BuildMetadata:
    """Non-sensitive facts about one build projection."""

    schema_version: int
    subject_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class BuildQuarter:
    """A complete template-safe quarter projection and its detail pages."""

    year: int
    month: int
    sections: tuple[QuarterSection, ...]
    navigation: tuple[QuarterNavigation, ...]
    details: tuple[SubjectDetailPage, ...]
    metadata: BuildMetadata
