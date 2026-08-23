"""Small, strict parser for the repository's build-time ``CHANGELOG.md``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class ChangelogError(ValueError):
    """Raised when the project changelog is not in the supported shape."""


@dataclass(frozen=True)
class ChangelogItem:
    """One ordinary paragraph or Markdown-style bullet."""

    kind: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class ChangelogSection:
    """One ``###`` group and its ordered content."""

    title: str
    items: tuple[ChangelogItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
        }


ChangelogBlock = ChangelogItem | ChangelogSection


@dataclass(frozen=True)
class ChangelogRelease:
    """One concrete versioned release section."""

    heading: str
    version: str
    date: str | None
    blocks: tuple[ChangelogBlock, ...]

    def to_dict(self) -> dict[str, object]:
        blocks: list[dict[str, object]] = []
        for block in self.blocks:
            blocks.append(
                block.to_dict()
                | ({"kind": "section"} if isinstance(block, ChangelogSection) else {})
            )
        return {
            "heading": self.heading,
            "version": self.version,
            "date": self.date,
            "blocks": blocks,
        }


@dataclass(frozen=True)
class ChangelogDocument:
    """The deterministic model consumed by the static site builder."""

    title: str
    preamble: tuple[str, ...]
    releases: tuple[ChangelogRelease, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "preamble": list(self.preamble),
            "releases": [release.to_dict() for release in self.releases],
        }

    def release_for_version(self, version: str) -> ChangelogRelease | None:
        return next(
            (release for release in self.releases if release.version == version),
            None,
        )

_RELEASE_RE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+)(?:\s+-\s+(?P<date>\d{4}-\d{2}-\d{2}))?$"
)
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class ChangelogMilestone:
    """One ``major.minor`` window and its concrete releases."""

    label: str
    releases: tuple[ChangelogRelease, ...]

    @property
    def children(self) -> tuple[ChangelogRelease, ...]:
        """Alias that describes the releases rendered inside the group."""
        return self.releases


@dataclass(frozen=True)
class ChangelogSettingsGroups:
    """Build-time Settings presentation groups, kept separate from Markdown."""

    standalone: tuple[ChangelogRelease, ...]
    milestones: tuple[ChangelogMilestone, ...]


def release_version_tuple(version: str) -> tuple[int, int, int]:
    """Parse the deliberately small concrete release version format."""
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise ChangelogError(f"malformed release version: {version}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def group_releases_for_settings(
    document: ChangelogDocument,
) -> ChangelogSettingsGroups:
    """Group concrete releases into standalone entries and milestone windows."""
    ordered = tuple(
        sorted(
            document.releases,
            key=lambda release: release_version_tuple(release.version),
            reverse=True,
        )
    )
    milestone_releases = tuple(
        release
        for release in ordered
        if release_version_tuple(release.version)[2] == 0
    )
    if not milestone_releases:
        return ChangelogSettingsGroups(ordered, ())

    milestone_versions = tuple(
        release_version_tuple(release.version) for release in milestone_releases
    )
    latest_milestone = milestone_versions[0]
    standalone = tuple(
        release
        for release in ordered
        if release_version_tuple(release.version) > latest_milestone
    )
    groups: list[ChangelogMilestone] = []
    for index, milestone in enumerate(milestone_releases):
        current = milestone_versions[index]
        previous = (
            milestone_versions[index + 1]
            if index + 1 < len(milestone_versions)
            else None
        )
        releases = tuple(
            release
            for release in ordered
            if release_version_tuple(release.version) <= current
            and (previous is None or release_version_tuple(release.version) > previous)
        )
        groups.append(
            ChangelogMilestone(
                f"{current[0]}.{current[1]}",
                releases,
            )
        )
    return ChangelogSettingsGroups(standalone, tuple(groups))


def load_changelog(path: Path) -> ChangelogDocument:
    """Read and parse a UTF-8 changelog, normalizing only line endings."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ChangelogError(f"CHANGELOG.md is unavailable: {path.name}") from error
    return parse_changelog(text)


def parse_changelog(text: str) -> ChangelogDocument:
    """Parse the deliberately small Markdown subset used by this repository."""
    if not isinstance(text, str):
        raise ChangelogError("CHANGELOG.md must be UTF-8 text")
    lines = (
        text.removeprefix("\ufeff")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None or not lines[first].startswith("# "):
        raise ChangelogError("CHANGELOG.md must start with a level-one title")
    title = lines[first][2:].strip()
    if not title:
        raise ChangelogError("CHANGELOG.md title is empty")

    preamble: list[str] = []
    releases: list[ChangelogRelease] = []
    current_heading: str | None = None
    current_version: str | None = None
    current_date: str | None = None
    current_blocks: list[ChangelogBlock] = []
    section_title: str | None = None
    section_items: list[ChangelogItem] = []

    def flush_section() -> None:
        nonlocal section_title, section_items
        if section_title is not None:
            current_blocks.append(
                ChangelogSection(section_title, tuple(section_items))
            )
        section_title = None
        section_items = []

    def flush_release() -> None:
        nonlocal current_heading, current_version, current_date, current_blocks
        flush_section()
        if current_heading is not None:
            releases.append(
                ChangelogRelease(
                    current_heading,
                    current_version,
                    current_date,
                    tuple(current_blocks),
                )
            )
        current_heading = None
        current_version = None
        current_date = None
        current_blocks = []

    for line in lines[first + 1 :]:
        stripped = line.strip()
        if line.startswith("## "):
            flush_release()
            heading = line[3:].strip()
            if heading == "尚未发布":
                raise ChangelogError("unreleased sections are not supported")
            match = _RELEASE_RE.fullmatch(heading)
            if match is None:
                raise ChangelogError(f"malformed release heading: {heading}")
            version = match.group("version")
            if any(release.version == version for release in releases):
                raise ChangelogError(f"duplicate release heading: {heading}")
            current_heading = heading
            current_version = version
            current_date = match.group("date")
            continue
        if current_heading is None:
            if stripped:
                if line.startswith("#"):
                    raise ChangelogError(f"unsupported heading: {stripped}")
                preamble.append(stripped)
            continue
        if line.startswith("### "):
            flush_section()
            section_title = line[4:].strip()
            if not section_title:
                raise ChangelogError("changelog group heading is empty")
            continue
        if line.startswith("#"):
            raise ChangelogError(f"unsupported heading: {stripped}")
        if not stripped:
            continue
        kind = "bullet" if line.lstrip().startswith("- ") else "paragraph"
        content = line.lstrip()[2:].strip() if kind == "bullet" else stripped
        if not content:
            raise ChangelogError("empty changelog item")
        target = section_items if section_title is not None else current_blocks
        if line[:1].isspace() and target:
            previous = target[-1]
            if isinstance(previous, ChangelogItem):
                target[-1] = ChangelogItem(
                    previous.kind, f"{previous.text} {content}"
                )
                continue
        target.append(ChangelogItem(kind, content))

    flush_release()
    if not releases:
        raise ChangelogError("CHANGELOG.md has no release sections")
    return ChangelogDocument(title, tuple(preamble), tuple(releases))


__all__ = [
    "ChangelogDocument",
    "ChangelogError",
    "ChangelogItem",
    "ChangelogMilestone",
    "ChangelogRelease",
    "ChangelogSettingsGroups",
    "ChangelogSection",
    "group_releases_for_settings",
    "load_changelog",
    "parse_changelog",
    "release_version_tuple",
]
