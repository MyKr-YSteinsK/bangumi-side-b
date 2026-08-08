"""Pure rule projection from a read-only SQLite snapshot to build models."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from pathlib import Path, PurePosixPath
from unicodedata import normalize

from bgm_side_b.build.models import (
    BuildMetadata,
    BuildQuarter,
    EpisodeView,
    MediaView,
    QuarterNavigation,
    QuarterSection,
    SourceView,
    SubjectCard,
    SubjectDetailPage,
    SubjectDrawer,
    TagView,
)
from bgm_side_b.build.queries import QuarterFacts, SubjectFacts
from bgm_side_b.config import CountryFilter, SourceRules, TagRules
from bgm_side_b.legacy_rules import (
    InfoboxItem,
    Quarter,
    decide_country,
    normalise_aliases,
    preferred_title,
    quarter_for_date,
)

_SECTION_ORDER = ("new",)
_SECTION_LABELS = {
    "new": "本季度新番",
}


class BuildProjection:
    """Project stored facts without mutating SQLite or inventing display data."""

    def __init__(
        self,
        tag_rules: TagRules,
        source_rules: SourceRules,
        workspace_directory: Path,
        *,
        country_filter: CountryFilter,
        excluded_subject_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.tag_rules = tag_rules
        self.source_rules = source_rules
        self.workspace_directory = workspace_directory.resolve()
        self.country_filter = country_filter
        self.excluded_subject_ids = excluded_subject_ids

    def project_quarter(self, facts: QuarterFacts) -> BuildQuarter:
        """Return a complete template-safe quarter model and build warnings."""
        warnings: list[str] = []
        country_filtered_subjects = 0
        cards: list[tuple[SubjectFacts, SubjectCard]] = []
        for subject in facts.subjects:
            if subject.subject_id in self.excluded_subject_ids:
                continue
            country = decide_country(
                (InfoboxItem(key, value) for key, value in subject.country_infobox),
                self.country_filter,
                raw_tags=subject.raw_tags,
                subject_type=subject.subject_type,
                platform=subject.media_format,
                air_date=subject.air_date,
                target_quarter=Quarter(facts.year, facts.month),
            )
            if not country.included:
                country_filtered_subjects += 1
                warnings.append(
                    "subject "
                    f"{subject.subject_id} excluded by country: {country.reason}"
                )
                continue
            card = self._project_card(subject)
            if card is None:
                warnings.append(f"subject {subject.subject_id} has no usable title")
                continue
            cards.append((subject, card))

        ranked_cards = _rank_cards(cards)
        sections = tuple(
            QuarterSection(
                kind,
                _SECTION_LABELS[kind],
                tuple(card for _, card in ranked_cards if card.section == kind),
            )
            for kind in _SECTION_ORDER
            if any(card.section == kind for _, card in ranked_cards)
        )
        details = tuple(
            self._project_detail(subject, card, facts.year, facts.month)
            for subject, card in ranked_cards
        )
        navigation = tuple(
            QuarterNavigation(
                year,
                month,
                True,
                year == facts.year and month == facts.month,
            )
            for year, month in facts.navigation
        )
        return BuildQuarter(
            facts.year,
            facts.month,
            sections,
            navigation,
            details,
            BuildMetadata(
                facts.schema_version,
                len(ranked_cards),
                country_filtered_subjects,
                tuple(warnings),
            ),
        )

    def _project_card(self, subject: SubjectFacts) -> SubjectCard | None:
        titles = _project_titles(subject.titles)
        if titles is None:
            return None
        selected_tags = _project_tags(subject.raw_tags, self.tag_rules)
        sources = _project_sources(subject.sources, self.source_rules)
        return SubjectCard(
            subject_id=subject.subject_id,
            section=subject.section,
            media_format=subject.media_format,
            preferred_title=titles[0],
            original_title=titles[1],
            aliases=titles[2],
            air_date=subject.air_date,
            declared_episode_count=subject.episode_count,
            total_episode_count=subject.total_episode_count,
            stored_main_episode_count=len(subject.episodes),
            rating_score=subject.rating_score,
            rating_count=subject.rating_count,
            sources=sources,
            tags=selected_tags,
            cover=_media_view(subject.cover, "cover", self.workspace_directory),
            search_text=_search_text(titles),
        )

    def _project_detail(
        self,
        subject: SubjectFacts,
        card: SubjectCard,
        entered_year: int,
        entered_month: int,
    ) -> SubjectDetailPage:
        permanent = quarter_for_date(subject.air_date) if subject.air_date else None
        drawer = SubjectDrawer(
            card,
            subject.summary,
            subject.end_date,
            permanent.year if permanent else None,
            permanent.month if permanent else None,
            entered_year,
            entered_month,
        )
        episodes = tuple(
            EpisodeView(
                row["id"],
                row["episode_number"],
                row["sort_number"],
                _clean_text(row["name_cn"]),
                _clean_text(row["name"]),
                _row_date(row["air_date"]),
                row["duration_seconds"],
                row["position"],
            )
            for row in subject.episodes
        )
        return SubjectDetailPage(drawer, episodes)


def _project_titles(
    stored_titles: Iterable[tuple[str, str]],
) -> tuple[str, str | None, tuple[str, ...]] | None:
    preferred = _first_title(stored_titles, "preferred")
    original = _first_title(stored_titles, "original")
    display = preferred_title(preferred, original)
    if display is None:
        return None
    clean_original = _clean_text(original)
    if clean_original == display:
        clean_original = None
    aliases = normalise_aliases(
        (title for kind, title in stored_titles if kind == "alias"), display
    )
    aliases = tuple(alias for alias in aliases if alias != clean_original)
    return display, clean_original, aliases


def _first_title(stored_titles: Iterable[tuple[str, str]], kind: str) -> str | None:
    for title_kind, title in stored_titles:
        if title_kind == kind:
            return _clean_text(title)
    return None


def _project_tags(raw_tags: Iterable[str], rules: TagRules) -> tuple[TagView, ...]:
    selected: set[str] = set()
    allowed = set(rules.allowed_tags)
    for raw_tag in raw_tags:
        normalized = normalize("NFKC", raw_tag).strip()
        canonical = rules.aliases.get(normalized, normalized)
        if canonical in allowed:
            selected.add(canonical)
    return tuple(TagView(tag) for tag in rules.allowed_tags if tag in selected)


def _project_sources(
    stored_sources: Iterable[str], rules: SourceRules
) -> tuple[SourceView, ...]:
    values = set(stored_sources)
    return tuple(SourceView(source) for source in rules.order if source in values)


def _search_text(titles: tuple[str, str | None, tuple[str, ...]]) -> str:
    preferred, original, aliases = titles
    values = (preferred, original, *aliases)
    return " ".join(
        normalize("NFKC", value).strip().casefold() for value in values if value
    )


def _rank_cards(
    cards: list[tuple[SubjectFacts, SubjectCard]],
) -> list[tuple[SubjectFacts, SubjectCard]]:
    by_section: dict[str, list[tuple[SubjectFacts, SubjectCard]]] = {}
    for pair in cards:
        by_section.setdefault(pair[1].section, []).append(pair)
    ranked: list[tuple[SubjectFacts, SubjectCard]] = []
    for kind in _SECTION_ORDER:
        group = by_section.get(kind, [])
        score_desc = _rank(group, _score_desc_key)
        score_asc = _rank(group, _score_asc_key)
        votes_desc = _rank(group, _votes_desc_key)
        votes_asc = _rank(group, _votes_asc_key)
        default = sorted(group, key=_score_desc_key)
        ranked.extend(
            (
                facts,
                replace(
                    card,
                    sort_score_desc=score_desc[card.subject_id],
                    sort_score_asc=score_asc[card.subject_id],
                    sort_votes_desc=votes_desc[card.subject_id],
                    sort_votes_asc=votes_asc[card.subject_id],
                ),
            )
            for facts, card in default
        )
    return ranked


def _rank(
    cards: list[tuple[SubjectFacts, SubjectCard]], key: object
) -> dict[int, int]:
    return {
        card.subject_id: position
        for position, (_, card) in enumerate(sorted(cards, key=key))
    }


def _score_desc_key(pair: tuple[SubjectFacts, SubjectCard]) -> tuple[object, ...]:
    facts, card = pair
    return (
        card.rating_score is None,
        -(card.rating_score or 0),
        -(card.rating_count or 0),
        _date_key(facts.air_date),
        card.subject_id,
    )


def _score_asc_key(pair: tuple[SubjectFacts, SubjectCard]) -> tuple[object, ...]:
    facts, card = pair
    return (
        card.rating_score is None,
        card.rating_score or 0,
        -(card.rating_count or 0),
        _date_key(facts.air_date),
        card.subject_id,
    )


def _votes_desc_key(pair: tuple[SubjectFacts, SubjectCard]) -> tuple[object, ...]:
    facts, card = pair
    return (
        card.rating_score is None,
        -(card.rating_count or 0),
        -(card.rating_score or 0),
        _date_key(facts.air_date),
        card.subject_id,
    )


def _votes_asc_key(pair: tuple[SubjectFacts, SubjectCard]) -> tuple[object, ...]:
    facts, card = pair
    return (
        card.rating_score is None,
        card.rating_count or 0,
        -(card.rating_score or 0),
        _date_key(facts.air_date),
        card.subject_id,
    )


def _date_key(value: date | None) -> date:
    return value or date.max


def _media_view(row: object, kind: str, workspace: Path) -> MediaView:
    if row is None:
        return MediaView(kind, None, None, None, None, None)
    if row["status"] != "success":
        return MediaView(kind, None, None, None, None, None)
    local_path = row["local_path"]
    if not isinstance(local_path, str):
        return MediaView(kind, None, None, None, None, None)
    try:
        relative = PurePosixPath(local_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError
        source = (workspace / Path(relative.as_posix())).resolve()
        if not source.is_relative_to(workspace) or not source.is_file():
            raise ValueError
        expected_size = row["size_bytes"]
        if expected_size is not None and source.stat().st_size != expected_size:
            raise ValueError
        expected_hash = row["content_hash"]
        if expected_hash is not None and _content_hash(source) != expected_hash:
            raise ValueError
    except (OSError, ValueError):
        return MediaView(kind, None, None, None, None, None)
    return MediaView(
        kind,
        relative.as_posix(),
        row["content_hash"],
        row["mime_type"],
        row["width"],
        row["height"],
    )


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = normalize("NFKC", value).strip()
    return cleaned or None


def _row_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
