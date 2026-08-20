"""Application version single-source invariants."""

from __future__ import annotations

from importlib.metadata import version as distribution_version
from pathlib import Path

from bgm_side_b import __version__
from tests.test_site_builder import _build_fixture


def test_source_metadata_and_settings_share_one_application_version(
    tmp_path: Path,
) -> None:
    builder, _ = _build_fixture(tmp_path)
    builder.build()

    settings = (tmp_path / "dist" / "site" / "settings" / "index.html").read_text(
        encoding="utf-8"
    )

    assert __version__ == "0.4.0"
    assert distribution_version("bgm-side-b") == __version__
    assert f"当前程序版本</dt><dd>{__version__}</dd>" in settings
