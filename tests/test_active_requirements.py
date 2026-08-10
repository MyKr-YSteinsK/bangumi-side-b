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

    assert "进入持久化 REVIEW" in baseline
    assert "premiere quarter" in baseline
    assert "TV 最多一个" in baseline
    assert "exact membership" in baseline
    assert "事实成功提交后触发受影响范围的增量 build" in baseline


def test_clean_admission_has_no_tv_default_fallback() -> None:
    admission = (ROOT / "src" / "bgm_side_b" / "admission.py").read_text(
        encoding="utf-8"
    )
    assert "allow_tv_default_without_country" not in admission
    assert "_is_tv_default_candidate" not in admission
