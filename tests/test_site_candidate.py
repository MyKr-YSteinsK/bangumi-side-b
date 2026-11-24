"""Focused identity and safety checks for the formal dist/site candidate."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from bgm_side_b.release.site_candidate import (
    SiteCandidateError,
    validate_build_state,
    validate_site,
)
from tests.test_site_builder import _build_fixture


def _site(root: Path) -> Path:
    builder, _ = _build_fixture(root.parent / "fixture")
    builder.site_directory = root
    builder.build()
    return root


def test_identity_is_deterministic_and_quarter_scope_is_data_driven(
    tmp_path: Path,
) -> None:
    root = _site(tmp_path / "site")

    first = validate_site(root, source_commit="a" * 40)
    second = validate_site(root, source_commit="a" * 40)

    assert first.identity == second.identity
    assert first.public_quarters == ("2026-04", "2026-07")
    assert first.identity.artifact_count == sum(p.is_file() for p in root.rglob("*"))


def test_safety_scan_rejects_databases_and_absolute_paths_but_not_api_urls(
    tmp_path: Path,
) -> None:
    root = _site(tmp_path / "site")
    (root / "data" / "extra.db").write_bytes(b"no")
    with pytest.raises(SiteCandidateError, match="database"):
        validate_site(root)

    (root / "data" / "extra.db").unlink()
    quarter_bytes = (root / "data/quarters/2026-07.json").read_bytes()
    assert b"https://bgm.tv/subject/" in quarter_bytes
    assert validate_site(root).public_quarters == ("2026-04", "2026-07")
    (root / "index.html").write_text(r"C:\fixture", "utf-8")
    with pytest.raises(SiteCandidateError, match="absolute Windows"):
        validate_site(root)


def test_build_state_must_match_the_actual_tree(tmp_path: Path) -> None:
    root = _site(tmp_path / "site")
    workspace = tmp_path / "workspace"
    artifacts: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
            artifacts[relative] = sha256(content).hexdigest()
            sizes[relative] = len(content)
    workspace.mkdir()
    (workspace / "build-state.json").write_text(
        json.dumps({"schema": 1, "artifacts": artifacts, "artifact_sizes": sizes}),
        "utf-8",
    )

    validate_build_state(root, workspace)
    (root / "index.html").write_text("changed", "utf-8")
    with pytest.raises(SiteCandidateError, match="does not match"):
        validate_build_state(root, workspace)
