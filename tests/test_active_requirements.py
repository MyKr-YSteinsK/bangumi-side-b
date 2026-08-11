"""Small guards against reintroducing superseded active requirements."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_active_baseline_matches_current_archive_evidence_model() -> None:
    baseline = (ROOT / "docs" / "project-requirements-baseline.md").read_text(
        encoding="utf-8"
    )
    country_doc = (ROOT / "docs" / "country-filter.md").read_text(encoding="utf-8")

    for text in (baseline, country_doc):
        assert "allow_tv_default_without_country" not in text
        assert "一个 Subject 最多属于一个归档季度" not in text
        assert "sync 只联网同步，不 build" not in text

    for stale in (
        "PWA 不提供在线浏览模式",
        "首次完整初始化并校验后才可进入",
        "一个 Subject 最多一个归档季度",
        "手机为底部/单列高面板",
        "community alias mapping",
    ):
        assert stale not in baseline

    assert "进入持久化 REVIEW" in baseline
    assert "premiere quarter" in baseline
    assert "TV 最多一个" in baseline
    assert "exact membership" in baseline
    assert "事实成功提交后触发受影响范围的增量 build" in baseline
    assert "Bangumi Subject ID" in baseline
    assert "窄 context rail" in baseline
    assert "可联网直接浏览" in baseline
    assert "data/offline/YYYY-MM.json" in baseline
    assert "一个 normalized source label" in baseline


def test_clean_admission_has_no_tv_default_fallback() -> None:
    admission = (ROOT / "src" / "bgm_side_b" / "admission.py").read_text(
        encoding="utf-8"
    )
    assert "allow_tv_default_without_country" not in admission
    assert "_is_tv_default_candidate" not in admission


def test_clean_paths_do_not_read_legacy_release_configuration() -> None:
    clean_paths = (
        ROOT / "src" / "bgm_side_b" / "archive_config.py",
        ROOT / "src" / "bgm_side_b" / "admission.py",
        ROOT / "src" / "bgm_side_b" / "sync.py",
        ROOT / "src" / "bgm_side_b" / "build" / "site_builder.py",
        ROOT / "src" / "bgm_side_b" / "build" / "site_projection.py",
        ROOT / "src" / "bgm_side_b" / "build" / "frontend.py",
        ROOT / "static" / "js" / "app.js",
    )
    forbidden = (
        "allow_tv_default_without_country",
        "load_project_settings",
        "release_quarters",
        "main_character_relations",
    )
    for path in clean_paths:
        source = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in source, f"{path.name} reads legacy setting {value}"

    config = (ROOT / "config" / "bangumi.toml").read_text(encoding="utf-8")
    assert "LEGACY-ONLY" in config
