"""Tests for profile-safe assets, paths, reports, and staged output."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from bgm_side_b.build import output as output_module
from bgm_side_b.build.assets import (
    AssetError,
    MediaPublisher,
    assert_pages_media_policy,
    generate_pwa_icons,
    publish_static_assets,
)
from bgm_side_b.build.models import MediaView
from bgm_side_b.build.output import AtomicOutput, OutputError, PendingPromotionError
from bgm_side_b.build.paths import PathResolver
from bgm_side_b.build.profiles import local_profile, pages_profile
from bgm_side_b.build.report import ProfileBuildReport, write_build_report


def test_profiles_and_relative_paths_work_for_file_and_pages_subpaths() -> None:
    local = local_profile()
    pages = pages_profile("/bangumi-side-b/")
    assert not local.derive_cover_webp
    assert pages.derive_cover_webp
    assert pages.deployment_path == "/bangumi-side-b/"

    for profile in (local, pages):
        paths = PathResolver(profile)
        assert (
            paths.asset("quarters/2022-01/index.html", "assets/site.123.css")
            == "../../assets/site.123.css"
        )
        assert (
            paths.href("subjects/101/index.html", paths.quarter(2022, 1))
            == "../../quarters/2022-01/index.html"
        )
        assert paths.external_subject(101) == "https://bgm.tv/subject/101"


def test_hashed_assets_and_profile_media_keep_pages_cover_output_bounded(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    (static / "css").mkdir(parents=True)
    (static / "js").mkdir()
    (static / ".gitkeep").write_text("", encoding="utf-8")
    (static / "js" / ".DS_Store").write_text("metadata", encoding="utf-8")
    (static / "css" / "site.css").write_text("body { color: #111; }", encoding="utf-8")
    (static / "js" / "site.js").write_text("window.ready = true;", encoding="utf-8")
    output = tmp_path / "output"
    published = publish_static_assets(static, output)
    assert set(published) == {"css/site.css", "js/site.js"}
    assert all("/" not in Path(path).name for path in published.values())
    assert all((output / path).is_file() for path in published.values())
    assert not any(
        path.name.startswith(".gitkeep") for path in (output / "assets").iterdir()
    )

    workspace = tmp_path / "workspace"
    cover = _media(workspace, "media/covers/1.png", (400, 600))
    pages = MediaPublisher(workspace, output)
    derived = pages.publish_cover(1, cover, pages_profile())
    assert derived is not None
    assert derived.relative_path.endswith(".webp")
    with Image.open(output / derived.relative_path) as image:
        assert image.format == "WEBP"
        assert image.size == (400, 600)
    assert pages.publish_cover(2, cover, pages_profile()) == derived
    assert pages.derived_covers_generated == 1
    assert pages.derived_covers_reused == 0
    cached = workspace / "cache" / "derived-covers"
    assert len(tuple(cached.glob("*.webp"))) == 1
    second = MediaPublisher(workspace, tmp_path / "second-output")
    assert second.publish_cover(1, cover, pages_profile()) is not None
    assert second.derived_covers_generated == 0
    assert second.derived_covers_reused == 1
    next(cached.glob("*.webp")).write_bytes(b"corrupt")
    recovered = MediaPublisher(workspace, tmp_path / "recovered-output")
    assert recovered.publish_cover(1, cover, pages_profile()) is not None
    assert recovered.derived_covers_generated == 1
    assert_pages_media_policy(output)

    local_output = tmp_path / "local"
    local = MediaPublisher(workspace, local_output)
    copied = local.publish_cover(1, cover, local_profile())
    assert copied is not None
    assert (local_output / copied.relative_path).read_bytes() == (
        workspace / "media/covers/1.png"
    ).read_bytes()


def test_media_rejects_unsafe_paths_and_pages_policy_fails_closed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    publisher = MediaPublisher(workspace, output)
    unsafe = MediaView("cover", "../outside.png", None, "image/png", 1, 1)
    assert publisher.publish_cover(1, unsafe, local_profile()) is None

    characters = output / "media" / "characters"
    characters.mkdir(parents=True)
    (characters / "unexpected.png").write_bytes(b"x")
    with pytest.raises(AssetError, match="character media"):
        assert_pages_media_policy(output)


def test_pwa_icon_generator_is_deterministic_and_keeps_maskable_safe_margin(
    tmp_path: Path,
) -> None:
    icons = generate_pwa_icons(tmp_path)
    assert set(icons) == {"icon-192.png", "icon-512.png", "icon-512-maskable.png"}
    with Image.open(tmp_path / icons["icon-512-maskable.png"]) as image:
        assert image.size == (512, 512)
        assert image.getpixel((0, 0)) == (245, 241, 232)


def test_atomic_output_replaces_complete_tree_and_preserves_previous_on_failure(
    tmp_path: Path,
) -> None:
    distribution = tmp_path / "dist"
    target = distribution / "local"
    target.mkdir(parents=True)
    (target / "version.txt").write_text("old", encoding="utf-8")
    output = AtomicOutput(distribution)

    result = output.generate(
        local_profile(),
        lambda stage: (stage / "version.txt").write_text("new", encoding="utf-8"),
        lambda stage: None,
    )
    assert result.replaced_previous_output
    assert (target / "version.txt").read_text(encoding="utf-8") == "new"

    def fail_validation(_: Path) -> None:
        raise RuntimeError("validation failed")

    failures: list[Path] = []
    with pytest.raises(RuntimeError, match="validation failed"):
        output.generate(
            local_profile(),
            lambda stage: (stage / "version.txt").write_text("bad", encoding="utf-8"),
            fail_validation,
            on_failure=failures.append,
        )
    assert (target / "version.txt").read_text(encoding="utf-8") == "new"
    assert not list((distribution / ".staging").glob("local-*"))
    assert len(failures) == 1


def test_atomic_output_retries_windows_locks_and_retains_pending_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    distribution = tmp_path / "dist"
    workspace = tmp_path / "workspace"
    target = distribution / "local"
    target.mkdir(parents=True)
    (target / "version.txt").write_text("old", encoding="utf-8")
    output = AtomicOutput(distribution, workspace_directory=workspace)
    original_replace = output._replace
    attempts = 0

    def transient(source: Path, destination: Path) -> None:
        nonlocal attempts
        if destination.name.startswith("local-previous-") and attempts < 2:
            attempts += 1
            raise PermissionError(5, "locked")
        original_replace(source, destination)

    monkeypatch.setattr(output, "_replace", transient)
    monkeypatch.setattr(output_module.time, "sleep", lambda _: None)
    result = output.generate(
        local_profile(),
        lambda stage: (stage / "version.txt").write_text("new", encoding="utf-8"),
        lambda stage: None,
        pending_metadata=_pending_metadata(),
    )
    assert result.promotion_retries == 2
    assert (target / "version.txt").read_text(encoding="utf-8") == "new"

    locked_output = AtomicOutput(distribution, workspace_directory=workspace)
    original_locked_replace = locked_output._replace

    def persistent(source: Path, destination: Path) -> None:
        if destination.name.startswith("local-previous-"):
            raise PermissionError(5, "locked")
        original_locked_replace(source, destination)

    monkeypatch.setattr(locked_output, "_replace", persistent)
    with pytest.raises(PendingPromotionError, match="bgmb promote local"):
        locked_output.generate(
            local_profile(),
            lambda stage: (stage / "version.txt").write_text(
                "pending", encoding="utf-8"
            ),
            lambda stage: None,
            pending_metadata=_pending_metadata(),
        )
    assert (target / "version.txt").read_text(encoding="utf-8") == "new"
    pending = workspace / "state" / "pending-promotion.json"
    assert pending.is_file()
    with pytest.raises(OutputError, match="bgmb promote local"):
        AtomicOutput(distribution, workspace_directory=workspace).require_no_pending(
            local_profile()
        )
    recovery = AtomicOutput(distribution, workspace_directory=workspace)
    stale = {**_pending_metadata(), "app_version": "0.1.4"}
    with pytest.raises(OutputError, match="已失效"):
        recovery.promote(local_profile(), lambda stage: None, metadata=stale)
    promoted = recovery.promote(
        local_profile(), lambda stage: None, metadata=_pending_metadata()
    )
    assert promoted.replaced_previous_output
    assert (target / "version.txt").read_text(encoding="utf-8") == "pending"
    assert not pending.exists()


def test_atomic_output_detects_a_locked_target_before_writer_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    distribution = tmp_path / "dist"
    target = distribution / "pages"
    target.mkdir(parents=True)
    output = AtomicOutput(distribution)

    def locked(_: Path, __: Path) -> None:
        raise PermissionError(5, "locked")

    monkeypatch.setattr(output, "_replace", locked)
    wrote = False

    def writer(_: Path) -> None:
        nonlocal wrote
        wrote = True

    with pytest.raises(OutputError, match="dist/pages 当前被占用"):
        output.generate(pages_profile(), writer, lambda stage: None)
    assert not wrote
    assert not list((distribution / ".staging").glob("pages-*"))


def test_build_report_is_atomic_and_rejects_local_paths(tmp_path: Path) -> None:
    report = ProfileBuildReport(
        "local", 1, 2, 2, 2, 0, ("missing cover",), 123, 4, True, ()
    )
    started = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)
    destination = write_build_report(
        tmp_path / "reports", "2022-01", started, started, (report,)
    )
    payload = destination.read_text(encoding="utf-8")
    assert str(tmp_path) not in payload
    assert '"profile": "local"' in payload
    unsafe = ProfileBuildReport(
        "local", 0, 0, 0, 0, 0, (str(tmp_path),), 0, 0, False, ()
    )
    with pytest.raises(ValueError, match="local path"):
        write_build_report(tmp_path / "reports", "scope", started, started, (unsafe,))


def _media(workspace: Path, relative_path: str, size: tuple[int, int]) -> MediaView:
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "#8A3147").save(path, format="PNG")
    content = path.read_bytes()
    return MediaView(
        "cover" if "covers" in relative_path else "character_image",
        relative_path,
        hashlib.sha256(content).hexdigest(),
        "image/png",
        size[0],
        size[1],
    )


def _pending_metadata() -> dict[str, object]:
    return {
        "source_commit": "a" * 40,
        "app_version": "0.1.3",
        "data_generation": 1,
        "created_at": "2026-08-07T00:00:00Z",
    }
