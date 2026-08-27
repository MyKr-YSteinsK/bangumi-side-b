"""Deterministic, Git-trackable manual Japanese-scope decisions."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bgm_side_b.domain import JapaneseClassification, JapaneseDecision


@dataclass(frozen=True)
class JapaneseOverride:
    """One explicit human decision and the automatic state to restore on clear."""

    subject_id: int
    classification: JapaneseClassification
    automatic_classification: JapaneseClassification
    automatic_evidence_type: str
    automatic_evidence_value: str

    @property
    def decision(self) -> JapaneseDecision:
        """Return the decision used by admission while this override is active."""
        return JapaneseDecision(
            self.classification,
            "manual_japanese_override",
            self.classification.value,
        )

    @property
    def automatic_decision(self) -> JapaneseDecision:
        """Return the exact local automatic decision saved before the override."""
        return JapaneseDecision(
            self.automatic_classification,
            self.automatic_evidence_type,
            self.automatic_evidence_value,
        )

    @classmethod
    def from_decision(
        cls,
        subject_id: int,
        classification: JapaneseClassification,
        automatic: JapaneseDecision,
    ) -> JapaneseOverride:
        if classification not in {
            JapaneseClassification.ACCEPTED_JAPANESE,
            JapaneseClassification.REJECTED_NON_JAPANESE,
        }:
            raise ValueError(
                "manual Japanese classification must be accepted or rejected"
            )
        if automatic.evidence_type is None or automatic.evidence_value is None:
            raise ValueError("automatic Japanese evidence must be present")
        return cls(
            subject_id,
            classification,
            automatic.classification,
            automatic.evidence_type,
            automatic.evidence_value,
        )


def load_japanese_overrides(path: Path) -> dict[int, JapaneseOverride]:
    """Load one validated manual Japanese decision per subject."""
    if not path.exists():
        return {}
    with path.open("rb") as file:
        payload = tomllib.load(file)
    entries = payload.get("classifications", [])
    if not isinstance(entries, list):
        raise ValueError("Japanese overrides classifications must be an array")
    overrides: dict[int, JapaneseOverride] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Japanese override entry must be a table")
        subject_id = entry.get("subject_id")
        if not _positive_integer(subject_id):
            raise ValueError("Japanese override subject_id must be positive")
        if subject_id in overrides:
            raise ValueError(
                "Japanese overrides must not contain duplicate subject IDs"
            )
        classification = _manual_classification(entry.get("classification"))
        automatic_classification = _automatic_classification(
            entry.get(
                "automatic_classification", JapaneseClassification.UNRESOLVED.value
            )
        )
        evidence_type = entry.get(
            "automatic_evidence_type", "unresolved_missing_japanese_region"
        )
        evidence_value = entry.get("automatic_evidence_value", "[]")
        if not isinstance(evidence_type, str) or not evidence_type.strip():
            raise ValueError("automatic Japanese evidence type must be non-empty")
        if not isinstance(evidence_value, str) or not evidence_value.strip():
            raise ValueError("automatic Japanese evidence value must be non-empty")
        overrides[subject_id] = JapaneseOverride(
            subject_id,
            classification,
            automatic_classification,
            evidence_type,
            evidence_value,
        )
    return overrides


def save_japanese_overrides(
    path: Path, overrides: Mapping[int, JapaneseOverride]
) -> None:
    """Atomically write stable TOML for explicit human decisions."""
    lines = [
        "# Manual Japanese-scope decisions. One active entry per Bangumi Subject ID.",
        "# Clear the decision with: bgmb classify BGM_ID --clear",
        "",
    ]
    for subject_id, override in sorted(overrides.items()):
        if subject_id != override.subject_id or not _positive_integer(subject_id):
            raise ValueError("Japanese override subject_id must be positive")
        if override.classification not in {
            JapaneseClassification.ACCEPTED_JAPANESE,
            JapaneseClassification.REJECTED_NON_JAPANESE,
        }:
            raise ValueError(
                "manual Japanese classification must be accepted or rejected"
            )
        lines.extend(
            (
                "[[classifications]]",
                f"subject_id = {subject_id}",
                f"classification = {_manual_value(override.classification)}",
                "automatic_classification = "
                f"{json.dumps(override.automatic_classification.value)}",
                "automatic_evidence_type = "
                f"{json.dumps(override.automatic_evidence_type, ensure_ascii=False)}",
                "automatic_evidence_value = "
                f"{json.dumps(override.automatic_evidence_value, ensure_ascii=False)}",
                "",
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines))
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _manual_classification(value: object) -> JapaneseClassification:
    if value == "japanese":
        return JapaneseClassification.ACCEPTED_JAPANESE
    if value == "non_japanese":
        return JapaneseClassification.REJECTED_NON_JAPANESE
    raise ValueError(
        "Japanese override classification must be japanese or non_japanese"
    )


def _automatic_classification(value: object) -> JapaneseClassification:
    try:
        return JapaneseClassification(value)
    except ValueError as error:
        raise ValueError("automatic Japanese classification is invalid") from error


def _manual_value(classification: JapaneseClassification) -> str:
    if classification is JapaneseClassification.ACCEPTED_JAPANESE:
        return '"japanese"'
    if classification is JapaneseClassification.REJECTED_NON_JAPANESE:
        return '"non_japanese"'
    raise ValueError("manual Japanese classification must be accepted or rejected")


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
