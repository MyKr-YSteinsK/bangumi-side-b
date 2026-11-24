"""Guard active documentation and source against retired archive behavior."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
PROJECT = ROOT / "docs" / "项目源文件"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_recovery_and_project_upload_sources_are_reachable() -> None:
    agents = _read(ROOT / "AGENTS.md")
    start = _read(PROJECT / "00-START_HERE.md")
    instructions = _read(PROJECT / "PROJECT_INSTRUCTIONS.md")
    readme = _read(ROOT / "README.md")

    for name in ("PROJECT_BRIEF.md", "CURRENT_STATE.md", "DECISIONS.md", "RUNBOOK.md"):
        assert (PROJECT / name).is_file()
        assert f"(docs/项目源文件/{name})" in agents
        assert f"({name})" in start
        assert name in instructions

    assert "(PROJECT_INSTRUCTIONS.md)" in start
    assert len(instructions.encode("utf-16-le")) // 2 <= 8000
    assert "(docs/项目源文件/00-START_HERE.md)" in readme
    assert "/docs/delete/" in _read(ROOT / ".gitignore")


def test_current_docs_preserve_mobile_and_offline_browsing() -> None:
    brief = _read(PROJECT / "PROJECT_BRIEF.md")
    guide = _read(ROOT / "docs" / "USER_GUIDE.md")
    visual = _read(ROOT / "docs" / "visual-system.md")

    for required in (
        "全宽单栏",
        "关闭放弃未应用项",
        "连续浏览",
        "Archive 提供分页",
        "Side B",
    ):
        assert required in brief
    for required in (
        "只修改草稿",
        "一次性应用",
        "关闭或系统返回会取消草稿",
        "不保留 context rail",
    ):
        assert required in guide
    for required in ("不保留 context rail", "draft", "apply"):
        assert required in visual

    active = "\n".join(
        (
            brief,
            guide,
            visual,
            _read(PROJECT / "DECISIONS.md"),
            _read(PROJECT / "RUNBOOK.md"),
            _read(ROOT / "README.md"),
            _read(ROOT / "docs" / "pwa.md"),
        )
    )
    for stale in (
        "BGM B",
        "allow_tv_default_without_country",
        "一个 Subject 最多属于一个归档季度",
        "一个 Subject 最多一个归档季度",
        "sync 只联网同步，不 build",
        "PWA 不提供在线浏览模式",
        "首次完整初始化并校验后才可进入",
        "手机为底部/单列高面板",
        "community alias mapping",
        "窄 context rail",
        "实时筛选",
        "realtime mobile filtering",
    ):
        assert stale not in active


def test_historical_docs_have_a_recovery_pointer() -> None:
    archives = ROOT / "docs" / "archive"
    archive = _read(archives / "README.md")
    for retired in (
        "project-requirements-baseline.md",
        "subject-sync.md",
        "data-reset.md",
    ):
        assert not (archives / retired).exists()
        assert retired in archive
    assert "git show b6c21de:docs/project/CURRENT_STATE.md" in archive


def test_decisions_distinguish_evidence_gaps_from_human_review() -> None:
    decisions = _read(PROJECT / "DECISIONS.md")
    for required in (
        "information-insufficient",
        "自动排除缓存",
        "事实冲突进入 REVIEW",
        "人工裁决",
        "excluded_subject_ids",
        "auto_excluded_subject_ids",
        "重新发现",
    ):
        assert required in decisions
    for number in range(1, 24):
        assert f"**D-{number:03d}**" in decisions


def test_agents_keep_data_and_delivery_boundaries() -> None:
    agents = _read(ROOT / "AGENTS.md")
    for required in (
        "bgmb sync",
        "bgmb assign",
        "Live Bangumi sync: AUTHORIZED",
        "未知或更高版本",
        "本机 `gh-pages`",
        "普通推送一次到配置的上游",
        "核对实际远端 HEAD",
        "推送失败时保留本地提交",
        "无 tracked 改动无需提交或推送",
        "禁止强推、改写历史",
        "Pages 独立授权",
        "release publish",
        "官方 origin",
        "禁止 sync 或 build",
    ):
        assert required in agents


def test_clean_admission_has_no_tv_default_fallback() -> None:
    admission = _read(ROOT / "src" / "bgm_side_b" / "admission.py")
    assert "allow_tv_default_without_country" not in admission
    assert "_is_tv_default_candidate" not in admission


def test_clean_paths_do_not_read_legacy_release_configuration() -> None:
    clean_paths = (
        ROOT / "src" / "bgm_side_b" / "archive_config.py",
        ROOT / "src" / "bgm_side_b" / "admission.py",
        ROOT / "src" / "bgm_side_b" / "sync.py",
        ROOT / "src" / "bgm_side_b" / "build" / "site_builder.py",
        ROOT / "src" / "bgm_side_b" / "build" / "site_projection.py",
        ROOT / "src" / "bgm_side_b" / "release" / "workflow.py",
        ROOT / "static" / "js" / "app.js",
    )
    forbidden = (
        "allow_tv_default_without_country",
        "load_project_settings",
        "release_quarters",
        "main_character_relations",
    )
    for path in clean_paths:
        source = _read(path)
        for value in forbidden:
            assert value not in source, f"{path.name} reads legacy setting {value}"

    config = _read(ROOT / "config" / "bangumi.toml")
    for legacy_table in ("[scope]", "[country_filter]", "[roles]", "[infobox]"):
        assert legacy_table not in config


def test_formal_tag_display_has_no_alias_configuration_path() -> None:
    config_source = _read(ROOT / "src" / "bgm_side_b" / "config.py")
    projection_source = _read(
        ROOT / "src" / "bgm_side_b" / "build" / "site_projection.py"
    )

    assert not (ROOT / "config" / "tag-aliases.toml").exists()
    assert "aliases_path" not in config_source
    assert "tag-aliases.toml" not in config_source
    assert "tag-aliases.toml" not in projection_source


def test_pwa_contract_replaces_the_legacy_snapshot_product() -> None:
    contract = _read(ROOT / "docs" / "pwa.md")
    agent_contract = _read(ROOT / "AGENTS.md")
    brief = _read(PROJECT / "PROJECT_BRIEF.md")
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
        "data/offline-v2/YYYY-MM.json",
        "quarter",
        "runtime cache",
        "Background Fetch",
        "covers/<ID>.webp?v=<content-hash>",
    ):
        assert required in contract
    assert "PWA 下载完整季度" in brief
    assert "在线与离线共用" in brief


def test_readme_describes_current_admission_and_quarter_pwa() -> None:
    readme = _read(ROOT / "README.md")
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
        source = _read(path)
        for value in forbidden:
            assert value not in source, f"{path.name} references legacy PWA {value}"


def test_retired_template_and_frontend_artifacts_are_absent() -> None:
    retired = (
        ROOT / "static" / "js" / "site.js",
        ROOT / "static" / "js" / "pwa-controller.js",
        ROOT / "static" / "js" / "pwa-ui.js",
        ROOT / "static" / "sw.js",
    )
    assert all(not path.exists() for path in retired)
    assert not any(path.is_file() for path in (ROOT / "templates").rglob("*"))
    assert "Jinja2" not in _read(ROOT / "pyproject.toml")
