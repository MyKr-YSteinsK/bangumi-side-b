"""Official-origin policy tests for real release publication."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bgm_side_b.release.site_publish import (
    SitePublishError,
    _normalise_origin,
    validate_release_origin,
)
from bgm_side_b.release.workflow import WorkflowError, publish_prepared_release


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "value",
    (
        "https://github.com/MyKr-YSteinsK/bangumi-side-b.git",
        "https://github.com/MyKr-YSteinsK/bangumi-side-b",
        "git@github.com:MyKr-YSteinsK/bangumi-side-b.git",
        "ssh://git@github.com/MyKr-YSteinsK/bangumi-side-b.git",
    ),
)
def test_supported_official_origin_forms_normalize(value: str) -> None:
    assert _normalise_origin(value) == "github.com/mykr-ysteinsk/bangumi-side-b"


@pytest.mark.parametrize(
    "value",
    (
        "https://github.com/other/bangumi-side-b.git",
        "https://github.com/MyKr-YSteinsK/other.git",
        "file:///tmp/MyKr-YSteinsK/bangumi-side-b.git",
        "C:/work/bangumi-side-b",
        "https://gitlab.com/MyKr-YSteinsK/bangumi-side-b.git",
        "git@github.com:MyKr-YSteinsK/bangumi-side-b.git?token=1",
    ),
)
def test_unofficial_origin_forms_are_rejected(value: str) -> None:
    assert _normalise_origin(value) != "github.com/mykr-ysteinsk/bangumi-side-b"


def test_validate_release_origin_reads_configured_remote(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(
        root,
        "remote",
        "add",
        "origin",
        "git@github.com:MyKr-YSteinsK/bangumi-side-b.git",
    )

    assert validate_release_origin(root).startswith("git@github.com:")


@pytest.mark.parametrize(
    "value",
    (
        "https://github.com/other/bangumi-side-b.git",
        "https://github.com/MyKr-YSteinsK/other.git",
    ),
)
def test_validate_release_origin_rejects_other_github_repositories(
    tmp_path: Path, value: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "remote", "add", "origin", value)

    with pytest.raises(SitePublishError, match="official project origin"):
        validate_release_origin(root)


def test_real_workflow_rejects_a_local_bare_origin_before_prepared_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    remote = tmp_path / "remote.git"
    _git(root, "init", "-q")
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(root, "remote", "add", "origin", str(remote))

    with pytest.raises(WorkflowError, match="official project origin"):
        publish_prepared_release(root)

    with pytest.raises(SitePublishError):
        validate_release_origin(root)
