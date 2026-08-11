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


def test_pwa_contract_replaces_the_legacy_snapshot_product() -> None:
    contract = (ROOT / "docs" / "pwa.md").read_text(encoding="utf-8")
    agent_contract = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    baseline = (ROOT / "docs" / "project-requirements-baseline.md").read_text(
        encoding="utf-8"
    )
    for stale in (
        "only the configured `2026-04`",
        "Subject detail pages",
        "active snapshot",
        "active pointer",
        "snapshot-manifest.json",
        "PWA 不提供在线浏览模式",
    ):
        assert stale not in contract
        assert stale not in agent_contract
    for required in (
        "data/offline/YYYY-MM.json",
        "quarter",
        "runtime cache",
        "Background Fetch",
        "covers/<ID>.webp?v=<content-hash>",
    ):
        assert required in contract
    assert "当前季度 scope 内定位该 appearance" in baseline
    assert "不自动跳转到 premiere quarter" in baseline
    assert "优先定位 premiere appearance" in baseline
    assert "same online `dist/site`" in agent_contract
    assert "explicit complete quarter downloads" in agent_contract


def test_readme_describes_current_admission_and_quarter_pwa() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for stale in (
        "初始化本地资料库",
        "下载并校验完整快照",
        "allow_tv_default_without_country",
        "季度 TV 默认",
    ):
        assert stale not in readme
    for required in (
        "public `meta_tags`",
        "严格回退",
        "进入 REVIEW",
        "直接在线浏览",
        "runtime cache",
        "主动下载单个",
        "未下载季度",
    ):
        assert required in readme


def test_formal_unified_runtime_does_not_reference_legacy_pwa() -> None:
    formal_sources = (
        ROOT / "src" / "bgm_side_b" / "build" / "site_builder.py",
        ROOT / "static" / "js" / "app.js",
        ROOT / "static" / "js" / "pwa.js",
        ROOT / "static" / "pwa" / "sw.js",
        ROOT / "static" / "css" / "site.css",
    )
    forbidden = (
        "pwa-controller.js",
        "pwa-ui.js",
        "static/sw.js",
        "snapshot-manifest.json",
        "active snapshot",
        "dist/pages",
        "LocalProfile",
        "PagesProfile",
    )
    for path in formal_sources:
        source = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in source, f"{path.name} references legacy PWA {value}"
