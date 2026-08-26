"""Small guards against reintroducing superseded active requirements."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
PROJECT = ROOT / "docs" / "project"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return " ".join(text.split())


def test_project_state_and_active_docs_match_current_archive_evidence_model() -> None:
    brief = _read(PROJECT / "PROJECT_BRIEF.md")
    decisions = _read(PROJECT / "DECISIONS.md")
    ownership = _read(PROJECT / "DOC_OWNERSHIP.md")
    guide = _read(ROOT / "docs" / "USER_GUIDE.md")
    visual = _read(ROOT / "docs" / "visual-system.md")
    pwa = _read(ROOT / "docs" / "pwa.md")
    readme = _read(ROOT / "README.md")
    active = "\n".join((brief, decisions, ownership, guide, visual, pwa, readme))
    compact_brief = _compact(brief)
    compact_guide = _compact(guide)
    compact_visual = _compact(visual)

    assert "full-screen/full-width single-column" in compact_brief
    assert "no narrow context rail" in compact_brief
    assert "draft/apply" in compact_brief
    assert "close/back cancels unapplied changes" in compact_brief
    assert "Quarter mobile browsing is continuous" in compact_brief
    assert "Archive browsing remains paginated" in compact_brief
    assert "The PWA short name is `Side B`" in compact_brief
    assert "View Transition is not the default motion foundation" in compact_brief
    assert "不保留 context rail" in compact_guide
    assert "只修改草稿" in compact_guide
    assert "一次性应用" in compact_guide
    assert "关闭或系统返回会取消草稿" in compact_guide
    assert "不保留 context rail" in compact_visual
    assert "draft" in compact_visual
    assert "apply" in compact_visual

    for stale in (
        "allow_tv_default_without_country",
        "一个 Subject 最多属于一个归档季度",
        "sync 只联网同步，不 build",
        "PWA 不提供在线浏览模式",
        "首次完整初始化并校验后才可进入",
        "一个 Subject 最多一个归档季度",
        "手机为底部/单列高面板",
        "community alias mapping",
        "窄 context rail",
        "实时筛选",
        "realtime mobile filtering",
    ):
        assert stale not in active

    # The old identity is mentioned only to record its supersession, never as
    # a current product identity or PWA manifest requirement.
    assert "BGM B" in decisions
    assert "superseded" in decisions
    current_identity = "\n".join((brief, guide, pwa, readme))
    assert "BGM B" not in current_identity

    for required_owner in (
        "docs/USER_GUIDE.md",
        "docs/development.md",
        "docs/country-filter.md",
        "docs/api-field-notes.md",
        "config/source-rules.toml",
        "config/allowed-tags.toml",
        "config/bangumi.toml",
        "config/quarter-overrides.toml",
        "docs/static-build.md",
        "docs/pwa.md",
        "docs/visual-system.md",
        "docs/publish.md",
        "docs/releases.md",
        "docs/repository-metadata.md",
        "CHANGELOG.md",
        "src/bgm_side_b/_version.py",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        ".github/workflows/deep-regression.yml",
        "LICENSE",
    ):
        assert required_owner in ownership

    for legacy_name in (
        "project-requirements-baseline.md",
        "subject-sync.md",
        "data-reset.md",
    ):
        assert not (ROOT / "docs" / legacy_name).exists()
        assert (ROOT / "docs" / "archive" / legacy_name).exists()
    archive_index = _read(ROOT / "docs" / "archive" / "README.md")
    assert "is an active product requirement" in archive_index
    assert "RED:REPEAT" in archive_index

    for current_doc in (
        ROOT / "README.md",
        ROOT / "docs" / "USER_GUIDE.md",
        ROOT / "docs" / "development.md",
        ROOT / "docs" / "visual-system.md",
        ROOT / "docs" / "pwa.md",
        ROOT / "docs" / "static-build.md",
        ROOT / "docs" / "publish.md",
        ROOT / "docs" / "releases.md",
    ):
        current_text = _read(current_doc)
        assert "docs/subject-sync.md" not in current_text
        assert "docs/data-reset.md" not in current_text
        assert "docs/project-requirements-baseline.md" not in current_text


def test_issue_taxonomy_distinguishes_information_gaps_from_conflicts() -> None:
    canonical_docs = (
        PROJECT / "PROJECT_BRIEF.md",
        PROJECT / "DECISIONS.md",
        ROOT / "docs" / "development.md",
    )
    for path in canonical_docs:
        text = _compact(_read(path))
        assert "information-insufficient" in text
        assert "automatic permanent exclusion" in text
        assert "factual conflict" in text
        assert "REVIEW" in text

    decisions = _compact(_read(PROJECT / "DECISIONS.md")).lower()
    assert "manual exclusion" in decisions
    assert "automatic permanent exclusion uses" in decisions
    assert "`auto_excluded_subject_ids`" in decisions
    assert "not one universal disposition" in decisions
    assert "Conflicts and insufficient evidence remain REVIEW" not in decisions
    assert "冲突或不足证据进入 REVIEW" not in _read(ROOT / "docs" / "development.md")


def test_current_state_records_checkpoint_baseline_and_completion() -> None:
    state = _compact(_read(PROJECT / "CURRENT_STATE.md"))
    adoption_sha = "4c458a7da6a23563f3a01306b604c52cb546981c"
    checkpoint_sha = "7e7c7671dd9620a38c61a5d1f1aed29fd94331dc"

    assert (
        f"Migration adoption audit start: `{adoption_sha}`" in state
    )
    assert (
        f"Migration Checkpoint adopted baseline: `{checkpoint_sha}`" in state
    )
    assert "Migration status: adoption is complete at the checkpoint baseline" in state
    assert "historical context, not the current adopted baseline" in state
    assert (
        "exact resulting HEAD of state-only documentation updates is "
        "authoritative in its TASK_RESULT" in state
    )
    assert "being retired" not in state
    assert "migration is being changed" not in state


def test_durable_decisions_preserve_migration_guardrails() -> None:
    decisions = _compact(_read(PROJECT / "DECISIONS.md")).lower()

    for required in (
        "independent repository",
        "mykr-ops",
        "generic plugin",
        "package/api boundary",
        "package update",
        "quarter-data update",
        "root or cross-document view transition",
        "full-screen black flash",
        "real-device evidence",
        "fast gate",
        "manual/deep layer",
    ):
        assert required in decisions


def test_agents_is_repo_specific_and_covers_live_access_boundary() -> None:
    agents = _read(ROOT / "AGENTS.md")

    for required in (
        "bgmb sync",
        "bgmb assign",
        "Live Bangumi sync: AUTHORIZED",
        "Unknown or newer",
        "release publish",
        "never calls `sync` or `build`",
        "A source push is not a Pages publication",
        "official project origin",
        "local `gh-pages` state",
    ):
        assert required in agents

    for generic_workflow in (
        "Each Phase must",
        "Version impact",
        "Validation cost policy",
        "CI-repair",
        "full pytest",
    ):
        assert generic_workflow not in agents


def test_agents_require_formal_plan_source_delivery_before_complete() -> None:
    agents = _compact(_read(ROOT / "AGENTS.md")).lower()

    for required in (
        "formal approved plan",
        "tracked source changes",
        "implementation, docs, and tests changes",
        "required validation has passed",
        "final source commit exists",
        "ordinary `git push`",
        "configured upstream",
        "upstream must accept that commit",
        "local branch head must equal the configured upstream head",
        "full `pass` / `complete`",
        "delivery failure",
        "`blocked`",
        "force push",
        "force-with-lease",
        "rebase",
        "amend",
        "squash",
        "history rewrite",
        "no source changes / no push required",
        "current source branch",
        "`release publish`",
        "pages publication",
        "`gh-pages` mutation",
        "live bangumi access",
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
        "data/offline/YYYY-MM.json",
        "quarter",
        "runtime cache",
        "Background Fetch",
        "covers/<ID>.webp?v=<content-hash>",
    ):
        assert required in contract
    assert "explicit complete-quarter downloads" in brief
    assert "extends the same online site" in brief
    assert "explicit complete quarter downloads" in agent_contract


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
