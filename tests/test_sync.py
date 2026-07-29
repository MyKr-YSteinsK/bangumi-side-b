"""Tests for subject-only sync orchestration and safe reports."""

import json
from pathlib import Path

import pytest

from bgm_side_b.api import (
    ApiInfoboxItem,
    CandidateSubject,
    DiscoveryResult,
    DiscoveryStatistics,
    SubjectDetail,
)
from bgm_side_b.config import ProjectSettings, load_rules
from bgm_side_b.database import Database
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import (
    SubjectSynchronizer,
    SyncScope,
    _normalise_summary,
    _source_infobox,
    parse_sync_scope,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "subject_cases.json").read_text(
        encoding="utf-8"
    )
)


class FakeDiscovery:
    def __init__(
        self,
        candidates: tuple[CandidateSubject, ...],
        *,
        blacklisted: int = 0,
    ) -> None:
        self.candidates = candidates
        self.blacklisted = blacklisted
        self.calls: list[tuple[int, int]] = []

    def discover(self, year: int, month: int, _: frozenset[int]) -> DiscoveryResult:
        self.calls.append((year, month))
        return DiscoveryResult(
            self.candidates,
            DiscoveryStatistics(
                discovered=len(self.candidates) + self.blacklisted,
                blacklisted=self.blacklisted,
                needs_detail=len(self.candidates),
            ),
            (),
        )


class FakeApi:
    def __init__(
        self,
        details: dict[int, SubjectDetail],
        failures: set[int] | None = None,
    ) -> None:
        self.details = details
        self.failures = failures or set()
        self.calls: list[int] = []

    def get_subject(self, subject_id: int) -> SubjectDetail:
        self.calls.append(subject_id)
        if subject_id in self.failures:
            from bgm_side_b.api import BangumiApiError

            raise BangumiApiError("network", "network request failed")
        return self.details[subject_id]


@pytest.fixture
def rules() -> tuple[ProjectSettings, object, object]:
    settings, tags, sources = load_rules(Path(__file__).resolve().parents[1] / "config")
    return settings, tags, sources


def _candidate(subject_id: int, score: float = 7.5) -> CandidateSubject:
    return CandidateSubject(subject_id, "TV", "name", "中文", 1, score, 100)


def _synchronizer(
    tmp_path: Path,
    rules: tuple[ProjectSettings, object, object],
    api: FakeApi,
    discovery: FakeDiscovery,
) -> SubjectSynchronizer:
    settings, tag_rules, source_rules = rules
    repository = SubjectRepository(Database(tmp_path / "data" / "facts.sqlite3"))
    return SubjectSynchronizer(
        repository,
        api,
        settings,
        tag_rules,
        source_rules,
        discovery=discovery,
        reports_directory=tmp_path / "reports",
    )


def test_scope_parsing_is_ordered_and_rejects_invalid_quarters() -> None:
    assert parse_sync_scope(["2022", "1"]) == SyncScope((2022,), 1)
    assert parse_sync_scope(["2022-2023"]).quarters[0] == (2022, 1)
    with pytest.raises(ValueError):
        parse_sync_scope(["2022", "2"])


def test_summary_paragraphs_and_structured_infobox_values_are_preserved() -> None:
    assert _normalise_summary(" first\n line\n\n\n second ") == "first line\n\nsecond"
    values = _source_infobox(
        [ApiInfoboxItem("source", [{"v": "manga"}, {"v": "novel"}])]
    )
    assert [item.value for item in values] == ["manga", "novel"]


def test_sync_writes_tv_subject_and_safe_reports(
    tmp_path: Path,
    rules: tuple[ProjectSettings, object, object],
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    api = FakeApi({101: detail})
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101),), blacklisted=1),
    )

    run = sync.run(SyncScope((2022,), 1))

    stats = run.quarter_stats[0]
    assert stats.created == 1
    assert stats.blacklisted == 1
    assert run.exit_code == 0
    assert run.sync_report.exists()
    payload = run.sync_report.read_text(encoding="utf-8")
    assert str(tmp_path) not in payload
    assert "authorization" not in payload.lower()
    audit = json.loads(run.tag_audit_report.read_text(encoding="utf-8"))
    assert audit["tags"][0]["raw_tag"] == "喜剧"


def test_incremental_sync_refreshes_rating_without_detail_request(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    api = FakeApi({101: detail})
    discovery = FakeDiscovery((_candidate(101, 7.5),))
    sync = _synchronizer(tmp_path, rules, api, discovery)
    sync.run(SyncScope((2022,), 1))

    api.calls.clear()
    sync.discovery = FakeDiscovery((_candidate(101, 8.2),))
    run = sync.run(SyncScope((2022,), 1))

    assert api.calls == []
    assert run.quarter_stats[0].skipped == 1
    connection = sync.repository.database.connect()
    try:
        score = connection.execute(
            "SELECT rating_score FROM subjects WHERE id = 101"
        ).fetchone()[0]
        assert score == 8.2
    finally:
        connection.close()

    api.calls.clear()
    sync.run(SyncScope((2022,), 1), force=True)
    assert api.calls == [101]


def test_missing_date_and_local_failure_do_not_stop_other_subjects(
    tmp_path: Path, rules: tuple[ProjectSettings, object, object]
) -> None:
    detail = SubjectDetail.from_payload(FIXTURES["tv"])
    missing = SubjectDetail.from_payload(FIXTURES["missing_date"])
    api = FakeApi({101: detail, 105: missing}, failures={106})
    sync = _synchronizer(
        tmp_path,
        rules,
        api,
        FakeDiscovery((_candidate(101), _candidate(105), _candidate(106))),
    )

    run = sync.run(SyncScope((2022,), 1))

    assert run.quarter_stats[0].created == 1
    assert run.quarter_stats[0].missing_date == 1
    assert run.quarter_stats[0].failed == 1
    assert run.exit_code == 1
    assert sync.repository.subject_exists(101)
