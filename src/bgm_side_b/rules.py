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

_COUNTRY_KEYS = frozenset({"制片国家/地区", "国家/地区"})
_JAPANESE_TOKENS = frozenset({"日本", "Japan"})
_PUBLIC_REGION_TAGS = frozenset({"日本", "中国", "美国", "韩国", "欧美"})
_COUNTRY_SEPARATOR = re.compile(r"[/／,，、;；|]")
_SUMMARY_MARKER = re.compile(r"\[\s*简介原文\s*\]")
_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_SUMMARY_CHARACTERS = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]"
)
_KANA_MINIMUM = 20
_KANA_RATIO = 0.25


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
    if len(source_types) == 1:
        first = observations[0]
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
    accepted = False
    rejected = False
    observed: set[str] = set()
    for item_key, value in infobox:
        if normalize_text(item_key) not in _COUNTRY_KEYS:
            continue
        normalized = normalize_text(value)
        tokens = tuple(
            token
            for part in _COUNTRY_SEPARATOR.split(normalized)
            if (token := normalize_text(part))
        )
        if not tokens:
            continue
        observed.add(normalized)
        if _JAPANESE_TOKENS.intersection(tokens):
            accepted = True
        else:
            rejected = True
    evidence_value = _evidence_json(observed)
    if accepted and not rejected:
        return JapaneseDecision(
            JapaneseClassification.ACCEPTED_JAPANESE,
            "infobox_country",
            evidence_value,
        )
    if rejected and not accepted:
        return JapaneseDecision(
            JapaneseClassification.REJECTED_NON_JAPANESE,
            "infobox_country",
            evidence_value,
        )
    if observed:
        return JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            "unresolved_conflicting_infobox_country",
            evidence_value,
        )
    return JapaneseDecision(
        JapaneseClassification.UNRESOLVED,
        "unresolved_missing_infobox_country",
        "[]",
    )


def classify_japanese_with_public_regions(
    meta_tags: Iterable[str], infobox: Iterable[tuple[str, str]]
) -> JapaneseDecision:
    """Use exact public region tags first, then strict structured country fallback."""
    public_regions = {
        normalized
        for value in meta_tags
        if (normalized := normalize_text(value)) in _PUBLIC_REGION_TAGS
    }
    infobox_regions = _infobox_regions(infobox)
    if public_regions and infobox_regions and public_regions != infobox_regions:
        return JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            "unresolved_japanese_evidence_conflict",
            _evidence_json(public_regions | infobox_regions),
        )
    regions = public_regions or infobox_regions
    evidence_type = (
        "bangumi_public_region_tag" if public_regions else "infobox_country"
    )
    if regions == {"日本"}:
        return JapaneseDecision(
            JapaneseClassification.ACCEPTED_JAPANESE,
            evidence_type,
            "日本",
        )
    if regions and "日本" not in regions:
        return JapaneseDecision(
            JapaneseClassification.REJECTED_NON_JAPANESE,
            evidence_type,
            _evidence_json(regions),
        )
    if regions:
        return JapaneseDecision(
            JapaneseClassification.UNRESOLVED,
            "unresolved_japanese_region_conflict",
            _evidence_json(regions),
        )
    return JapaneseDecision(
        JapaneseClassification.UNRESOLVED,
        "unresolved_missing_japanese_region",
        "[]",
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


def _evidence_json(values: set[str]) -> str:
    return json.dumps(sorted(values), ensure_ascii=False, separators=(",", ":"))


def _infobox_regions(infobox: Iterable[tuple[str, str]]) -> set[str]:
    regions: set[str] = set()
    for item_key, value in infobox:
        if normalize_text(item_key) not in _COUNTRY_KEYS:
            continue
        for part in _COUNTRY_SEPARATOR.split(normalize_text(value)):
            token = normalize_text(part)
            if token in _JAPANESE_TOKENS:
                regions.add("日本")
            elif token:
                regions.add(token)
    return regions
