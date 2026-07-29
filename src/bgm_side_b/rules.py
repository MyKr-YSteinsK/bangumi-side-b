"""Pure, deterministic domain rules for Bangumi Side B."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from unicodedata import normalize

from bgm_side_b.config import SourceRules, TagRules

QUARTER_MONTHS = (1, 4, 7, 10)
SUPPORTED_FORMATS = frozenset({"tv", "movie"})

_FORMAT_VALUES = {
    "tv": "tv",
    "剧场版": "movie",
    "movie": "movie",
    "web": "web",
    "ova": "ova",
    "oad": "oad",
}
_ADAPTATION_SOURCES = frozenset(
    {"manga", "light_novel", "novel", "game", "visual_novel"}
)


@dataclass(frozen=True)
class Quarter:
    """A validated calendar quarter represented by its first month."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if self.year < 1 or self.year > 9999:
            raise ValueError("year must be between 1 and 9999")
        if not is_quarter_month(self.month):
            raise ValueError("quarter month must be one of 1, 4, 7, or 10")

    @property
    def start_date(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        next_month = self.month + 3
        if next_month == 13:
            return date(self.year, 12, 31)
        return date(self.year, next_month, 1) - timedelta(days=1)


@dataclass(frozen=True)
class InfoboxItem:
    """One structured Infobox key/value pair eligible for exact matching."""

    key: str
    value: str


@dataclass(frozen=True)
class SourceEvidence:
    """An exact Infobox or community-tag value supporting one source."""

    evidence_type: str
    value: str


@dataclass(frozen=True)
class SourceResult:
    """Resolved canonical sources, their evidence, and deterministic warnings."""

    sources: tuple[str, ...]
    evidence: tuple[SourceEvidence, ...]
    warnings: tuple[str, ...]


def normalize_text(value: str) -> str:
    """Apply NFKC and trim outer whitespace without semantic inference."""
    return normalize("NFKC", value).strip()


def is_quarter_month(month: int) -> bool:
    """Return whether ``month`` is a valid quarter-start month."""
    return (
        isinstance(month, int)
        and not isinstance(month, bool)
        and month in QUARTER_MONTHS
    )


def quarter_for_date(value: date) -> Quarter:
    """Return the permanent calendar quarter containing a complete date."""
    return Quarter(value.year, ((value.month - 1) // 3) * 3 + 1)


def expand_years(start_year: int, end_year: int | None = None) -> tuple[int, ...]:
    """Expand one year or an inclusive ascending year range."""
    final_year = start_year if end_year is None else end_year
    if not all(
        isinstance(year, int) and not isinstance(year, bool)
        for year in (start_year, final_year)
    ):
        raise ValueError("years must be integers")
    if start_year < 1 or final_year > 9999 or start_year > final_year:
        raise ValueError("year range must be ascending and within 1 to 9999")
    return tuple(range(start_year, final_year + 1))


def normalize_format(value: str | None) -> str | None:
    """Map only documented exact format values to canonical lower-case values."""
    if value is None:
        return None
    return _FORMAT_VALUES.get(normalize_text(value).casefold())


def is_supported_format(value: str | None) -> bool:
    """Return whether a raw format is enabled in the first version."""
    return normalize_format(value) in SUPPORTED_FORMATS


def preferred_title(
    chinese_title: str | None, original_title: str | None
) -> str | None:
    """Choose a non-empty Chinese title, otherwise the non-empty original title."""
    chinese = normalize_text(chinese_title) if chinese_title else ""
    original = normalize_text(original_title) if original_title else ""
    return chinese or original or None


def normalise_aliases(
    values: Iterable[str], primary_title: str | None
) -> tuple[str, ...]:
    """Trim, NFKC-normalise, deduplicate, and exclude the primary title."""
    primary = normalize_text(primary_title) if primary_title else ""
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = normalize_text(value)
        if not candidate or candidate == primary or candidate in seen:
            continue
        seen.add(candidate)
        aliases.append(candidate)
    return tuple(aliases)


def display_tags(raw_tags: Iterable[str], rules: TagRules) -> tuple[str, ...]:
    """Map exact aliases, filter to the whitelist, and apply configured order."""
    allowed = set(rules.allowed_tags)
    selected: set[str] = set()
    for raw_tag in raw_tags:
        candidate = normalize_text(raw_tag)
        canonical = rules.aliases.get(candidate, candidate)
        if canonical in allowed:
            selected.add(canonical)
    return tuple(tag for tag in rules.allowed_tags if tag in selected)


def derive_sources(
    infobox_items: Iterable[InfoboxItem],
    raw_tags: Iterable[str],
    rules: SourceRules,
) -> SourceResult:
    """Resolve sources from exact Infobox evidence, falling back to exact tags."""
    infobox_evidence = _infobox_evidence(infobox_items, rules)
    evidence = infobox_evidence or _tag_evidence(raw_tags, rules)
    return _resolve_evidence(evidence, rules)


def format_utc(value: datetime) -> str:
    """Render an aware timestamp in stable UTC ``Z`` notation."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _infobox_evidence(
    items: Iterable[InfoboxItem], rules: SourceRules
) -> tuple[SourceEvidence, ...]:
    evidence: list[SourceEvidence] = []
    for item in items:
        key = normalize_text(item.key)
        value = normalize_text(item.value)
        if key in rules.infobox_keys and value in rules.infobox_values:
            evidence.append(SourceEvidence("infobox", value))
    return _unique_evidence(evidence)


def _tag_evidence(
    raw_tags: Iterable[str], rules: SourceRules
) -> tuple[SourceEvidence, ...]:
    evidence = [
        SourceEvidence("tag", normalized)
        for raw_tag in raw_tags
        if (normalized := normalize_text(raw_tag)) in rules.tag_values
    ]
    return _unique_evidence(evidence)


def _unique_evidence(evidence: Iterable[SourceEvidence]) -> tuple[SourceEvidence, ...]:
    return tuple(dict.fromkeys(evidence))


def _resolve_evidence(
    evidence: tuple[SourceEvidence, ...], rules: SourceRules
) -> SourceResult:
    if not evidence:
        return SourceResult(("unknown",), (), ())

    source_for_evidence = {
        "infobox": rules.infobox_values,
        "tag": rules.tag_values,
    }
    resolved = {
        source_for_evidence[item.evidence_type][item.value] for item in evidence
    }
    resolved.discard("unknown")
    if "visual_novel" in resolved:
        resolved.discard("game")
    if "light_novel" in resolved:
        resolved.discard("novel")

    if "original" in resolved and resolved & _ADAPTATION_SOURCES:
        return SourceResult(
            ("unknown",),
            evidence,
            ("original_adaptation_conflict",),
        )

    ordered = tuple(source for source in rules.order if source in resolved)
    return SourceResult(ordered or ("unknown",), evidence, ())
