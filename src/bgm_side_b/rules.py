"""Deterministic normalization rules for the clean archive domain."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from bgm_side_b.domain import (
    JapaneseClassification,
    JapaneseDecision,
    SourceDecision,
    SourceEvidence,
    SourceType,
)

_COUNTRY_KEYS = frozenset(
    {
        "制片国家/地区",
        "国家/地区",
        "制片国家",
        "地区",
    }
)
_JAPANESE_TOKENS = frozenset({"日本", "Japan"})
_PUBLIC_REGION_TAGS = frozenset({"日本", "Japan", "中国", "美国", "韩国", "欧美"})
_BROAD_REGION_TOKENS = frozenset({"欧美"})
_COUNTRY_SEPARATOR = re.compile(r"[/／,，、;；|｜・]")
_SUMMARY_MARKER = re.compile(r"\[\s*简介原文\s*\]")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_SUMMARY_CHARACTERS = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]"
)
_KANA_MINIMUM = 20
_KANA_RATIO = 0.25
_SOURCE_PRECEDENCE = {
    frozenset({SourceType.VISUAL_NOVEL, SourceType.GAME}): SourceType.VISUAL_NOVEL,
    frozenset({SourceType.LIGHT_NOVEL, SourceType.NOVEL}): SourceType.LIGHT_NOVEL,
}


@dataclass(frozen=True)
class TagCandidate:
    """One API tag candidate before count information is discarded."""

    name: str
    count: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("tag candidate count must not be negative")


def normalize_text(value: str) -> str:
    """Apply only NFKC and surrounding-whitespace normalization."""
    return unicodedata.normalize("NFKC", value).strip()


def normalize_aliases(
    values: Iterable[str], *, excluded: Iterable[str] = ()
) -> tuple[str, ...]:
    """Keep stable, unique aliases without translation or fuzzy matching."""
    ignored = {normalized for item in excluded if (normalized := normalize_text(item))}
    seen = set(ignored)
    aliases: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            aliases.append(normalized)
    return tuple(aliases)


def order_tag_candidates(candidates: Iterable[TagCandidate]) -> tuple[str, ...]:
    """Order API candidates by count, then a deterministic normalized name."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        name = normalize_text(candidate.name)
        if name:
            counts[name] = max(counts.get(name, 0), candidate.count)
    return tuple(
        name
        for name, _ in sorted(
            counts.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
    )


def resolve_source(evidence: Iterable[SourceEvidence]) -> SourceDecision:
    """Resolve one source, returning unknown for missing or conflicting evidence."""
    observations = sorted(
        set(evidence),
        key=lambda item: (
            item.source_type.value,
            item.evidence_type,
            item.evidence_value,
        ),
    )
    if not observations:
        return SourceDecision(SourceType.UNKNOWN)
    source_types = {item.source_type for item in observations}
    resolved_type = (
        observations[0].source_type
        if len(source_types) == 1
        else _SOURCE_PRECEDENCE.get(frozenset(source_types))
    )
    if resolved_type is not None:
        first = next(item for item in observations if item.source_type is resolved_type)
        return SourceDecision(
            first.source_type, first.evidence_type, first.evidence_value
        )
    conflict = json.dumps(
        [
            [item.source_type.value, item.evidence_type, item.evidence_value]
            for item in observations
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return SourceDecision(SourceType.UNKNOWN, "conflict", conflict)


def classify_japanese(infobox: Iterable[tuple[str, str]]) -> JapaneseDecision:
    """Classify only exact structured country evidence; never inspect summary."""
    sources = tuple(
        source
        for item_key, value in infobox
        if (source := _infobox_country_source(item_key, value)) is not None
    )
    return _resolve_country_sources(
        sources,
        missing_evidence_type="unresolved_missing_infobox_country",
        conflict_evidence_type="unresolved_conflicting_infobox_country",
    )


def classify_japanese_with_public_regions(
    meta_tags: Iterable[str], infobox: Iterable[tuple[str, str]]
) -> JapaneseDecision:
    """Fuse exact public region and Infobox country evidence by source."""
    public_source = _public_region_source(meta_tags)
    sources = tuple(
        source
        for source in (
            public_source,
            *(
                _infobox_country_source(item_key, value)
                for item_key, value in infobox
            ),
        )
        if source is not None
    )
    return _resolve_country_sources(
        sources,
        missing_evidence_type="unresolved_missing_japanese_region",
        conflict_evidence_type="unresolved_japanese_evidence_conflict",
    )


def display_summary(summary_raw: str | None) -> str | None:
    """Return a conservative display summary while preserving raw SQLite facts."""
    if summary_raw is None:
        return None
    normalized = _normalize_summary(summary_raw)
    if not normalized:
        return None
    marker = _SUMMARY_MARKER.search(normalized)
    if marker is not None:
        return _normalize_summary(normalized[: marker.start()]) or None
    characters = _SUMMARY_CHARACTERS.findall(normalized)
    kana_count = len(_KANA.findall(normalized))
    kana_ratio = kana_count / len(characters) if characters else 0.0
    if kana_count >= _KANA_MINIMUM and kana_ratio >= _KANA_RATIO:
        return None
    return normalized


def _normalize_summary(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.rstrip() for line in normalized.split("\n")]
    normalized = "\n".join(lines)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", normalized).strip()


@dataclass(frozen=True)
class _CountrySource:
    """Exact country observations from one public structured source."""

    evidence_type: str
    regions: frozenset[str]


def _resolve_country_sources(
    sources: Iterable[_CountrySource],
    *,
    missing_evidence_type: str,
    conflict_evidence_type: str,
) -> JapaneseDecision:
    observed_sources = tuple(source for source in sources if source.regions)
    if not observed_sources:
        return JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            missing_evidence_type,
            "[]",
        )
    positive_sources = tuple(
        source for source in observed_sources if "日本" in source.regions
    )
    negative_only_sources = tuple(
        source for source in observed_sources if "日本" not in source.regions
    )
    evidence_value = _evidence_json(
        region for source in observed_sources for region in source.regions
    )
    if positive_sources and negative_only_sources:
        return JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            conflict_evidence_type,
            evidence_value,
        )
    if positive_sources:
        return JapaneseDecision(
            JapaneseClassification.ACCEPTED_JAPANESE,
            _combined_evidence_type(observed_sources),
            evidence_value,
        )
    return JapaneseDecision(
        JapaneseClassification.REJECTED_NON_JAPANESE,
        _combined_evidence_type(observed_sources),
        evidence_value,
    )


def _evidence_json(values: Iterable[str]) -> str:
    return json.dumps(sorted(set(values)), ensure_ascii=False, separators=(",", ":"))


def _combined_evidence_type(sources: Iterable[_CountrySource]) -> str:
    evidence_types = tuple(dict.fromkeys(source.evidence_type for source in sources))
    return "+".join(evidence_types)


def _public_region_source(meta_tags: Iterable[str]) -> _CountrySource | None:
    regions = {
        canonical
        for value in meta_tags
        if (normalized := normalize_text(value)) in _PUBLIC_REGION_TAGS
        if (canonical := _canonical_region(normalized)) is not None
    }
    if not regions:
        return None
    return _CountrySource("bangumi_public_region_tag", frozenset(regions))


def _infobox_country_source(
    item_key: str, value: str
) -> _CountrySource | None:
    if normalize_text(item_key) not in _COUNTRY_KEYS:
        return None
    regions = {
        canonical
        for token in _country_tokens(value)
        if (canonical := _canonical_region(token)) is not None
    }
    if not regions:
        return None
    return _CountrySource("infobox_country", frozenset(regions))


def _country_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for part in _COUNTRY_SEPARATOR.split(normalize_text(value))
        if (token := normalize_text(part))
    )


def _canonical_region(token: str) -> str | None:
    if token in _JAPANESE_TOKENS:
        return "日本"
    if token in _BROAD_REGION_TOKENS:
        return None
    return token


def _infobox_regions(infobox: Iterable[tuple[str, str]]) -> set[str]:
    return {
        region
        for item_key, value in infobox
        if (source := _infobox_country_source(item_key, value)) is not None
        for region in source.regions
    }
