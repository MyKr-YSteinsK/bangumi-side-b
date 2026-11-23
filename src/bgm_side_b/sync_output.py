"""Terminal summaries for completed sync runs."""

from __future__ import annotations

from bgm_side_b.adjudication import render_review
from bgm_side_b.domain import Quarter
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import QuarterSyncResult

MAX_INLINE_SYNC_REVIEWS = 10


def sync_summary_lines(result: QuarterSyncResult) -> tuple[str, ...]:
    """Render the bounded evidence summary for a single-quarter sync."""
    lines = [
        f"status: facts={result.facts_status}, covers={result.covers_status}",
        (
            f"TV ACCEPTED {result.accepted_tv} | "
            f"CONTINUING TV {result.continuing_end_date + result.continuing_episode} | "
            f"MOVIE ACCEPTED {result.accepted_movie}"
        ),
        (
            "continuing evidence: "
            f"end_date={result.continuing_end_date}, "
            f"main_episode={result.continuing_episode}, "
            f"unresolved={result.continuing_unresolved}"
        ),
        (
            f"candidate observations={result.discovered}, "
            f"canonical detail requests={result.canonical_detail_requests}, "
            f"persisted REVIEW={result.persisted_review_count}, "
            "covers downloaded/reused/missing="
            f"{result.cover_downloaded}/{result.cover_reused}/"
            f"{result.cover_missing}"
        ),
    ]
    if result.auto_reconsidered or result.auto_restored or result.auto_reconciled:
        lines.append(
            "automatic exclusions: "
            f"re-evaluated={result.auto_reconsidered}, "
            f"restored={len(result.auto_restored)}, "
            f"reconciled={len(result.auto_reconciled)}"
        )
    lines.extend(
        (
            "AUTO PREMIERE "
            f"{item['subject_id']}: {item['air_date']} -> "
            f"{item['premiere_quarter']} ({item['evidence']})"
        )
        for item in result.early_premieres
    )
    lines.extend(_auto_blacklist_line(item) for item in result.auto_blacklisted)
    if result.auto_reconciled:
        lines.append(
            "AUTO RECONCILED "
            + ", ".join(str(subject_id) for subject_id in result.auto_reconciled)
        )
    if result.blacklisted:
        lines.append(
            "BLACKLIST "
            f"manual={result.manual_blacklisted}, "
            f"existing_auto={result.existing_auto_blacklisted}, "
            f"new_auto={len(result.auto_blacklisted)}"
        )
    if result.warnings or result.errors:
        lines.append(
            f"exceptions: warnings={len(result.warnings)}, errors={len(result.errors)}"
        )
    return tuple(lines)


def sync_scope_summary_lines(
    results: tuple[QuarterSyncResult, ...],
    quarter_timings: tuple[dict[str, object], ...] = (),
) -> tuple[str, ...]:
    """Render a bounded multi-quarter summary without claiming unique additions."""
    if quarter_timings:
        quarter_count = len(quarter_timings)
        attempted = sum(
            item.get("main_sync_status") not in {"skipped", "not_started"}
            for item in quarter_timings
        )
        skipped = sum(
            item.get("main_sync_status") == "skipped" for item in quarter_timings
        )
        facts_complete = sum(
            item.get("facts_status") == "complete" for item in quarter_timings
        )
        facts_incomplete = sum(
            item.get("facts_status") == "incomplete" for item in quarter_timings
        )
        facts_unknown = quarter_count - facts_complete - facts_incomplete
        covers_complete = sum(
            item.get("covers_status") == "complete" for item in quarter_timings
        )
        covers_incomplete = sum(
            item.get("covers_status") == "incomplete" for item in quarter_timings
        )
        covers_unknown = quarter_count - covers_complete - covers_incomplete
    else:
        quarter_count = len(results)
        attempted = sum(not item.skipped for item in results)
        skipped = quarter_count - attempted
        facts_complete = sum(item.facts_status == "complete" for item in results)
        facts_incomplete = sum(item.facts_status != "complete" for item in results)
        facts_unknown = 0
        covers_complete = sum(item.covers_status == "complete" for item in results)
        covers_incomplete = sum(item.covers_status != "complete" for item in results)
        covers_unknown = 0
    return (
        (
            f"scope quarters={quarter_count}, result rows={len(results)}, "
            f"attempted={attempted}, skipped={skipped}, "
            "facts complete/incomplete/unknown="
            f"{facts_complete}/{facts_incomplete}/{facts_unknown}, "
            "covers complete/incomplete/unknown="
            f"{covers_complete}/{covers_incomplete}/{covers_unknown}"
        ),
        (
            "per-quarter counts (candidates may repeat; accepted counts are "
            "decisions, not unique/new works): "
            f"candidate observations={sum(item.discovered for item in results)}, "
            "canonical detail requests="
            f"{sum(item.canonical_detail_requests for item in results)}, "
            f"TV accepted={sum(item.accepted_tv for item in results)}, "
            f"Movie accepted={sum(item.accepted_movie for item in results)}, "
            f"persisted REVIEW={sum(item.persisted_review_count for item in results)}, "
            "covers downloaded/reused/missing="
            f"{sum(item.cover_downloaded for item in results)}/"
            f"{sum(item.cover_reused for item in results)}/"
            f"{sum(item.cover_missing for item in results)}, "
            f"warnings/errors={sum(len(item.warnings) for item in results)}/"
            f"{sum(len(item.errors) for item in results)}"
        ),
        (
            "per-quarter decisions/events: "
            "non-Japanese rejected="
            f"{sum(item.rejected_non_japanese for item in results)}, "
            f"blacklisted={sum(item.blacklisted for item in results)} "
            "(manual/previous-auto/new-auto="
            f"{sum(item.manual_blacklisted for item in results)}/"
            f"{sum(item.existing_auto_blacklisted for item in results)}/"
            f"{sum(len(item.auto_blacklisted) for item in results)}), "
            "continuing confirmed by end-date/main-episode/unresolved="
            f"{sum(item.continuing_end_date for item in results)}/"
            f"{sum(item.continuing_episode for item in results)}/"
            f"{sum(item.continuing_unresolved for item in results)}, "
            "auto exclusions re-evaluated/restored/reconciled="
            f"{sum(item.auto_reconsidered for item in results)}/"
            f"{sum(len(item.auto_restored) for item in results)}/"
            f"{sum(len(item.auto_reconciled) for item in results)}"
        ),
    )


def _auto_blacklist_line(item: dict[str, object]) -> str:
    """Render a compact automatic exclusion event with its stable reason."""
    if item.get("reason") == "insufficient_airing_information":
        line = (
            "AUTO BLACKLISTED "
            f"{item['subject_id']}: {item['title']}"
        )
        line += (
            f" reason={item['reason']}"
            f" issue={item['issue_code']}"
        )
        return line
    line = (
        "AUTO BLACKLISTED "
        f"{item['subject_id']}: {item['title']} "
        f"({item.get('days_since_air_date')} days, "
        f"ratings={item['rating_count']})"
    )
    return line


def sync_review_lines(
    result: QuarterSyncResult,
    repository: SubjectRepository,
    quarter: Quarter,
) -> tuple[str, ...]:
    """Render only the persisted quarter queue plus explicitly external findings."""
    lines: list[str] = []
    if result.persisted_review_count:
        if result.persisted_review_count <= MAX_INLINE_SYNC_REVIEWS:
            lines.append(render_review(repository, quarter))
        else:
            lines.append(
                f"{result.persisted_review_count} persisted REVIEW items; "
                "run bgmb review for the complete local queue"
            )
    lines.extend(
        "REVIEW "
        f"{review['subject_id']} {review['issue_code']}｜"
        f"{review['command']}"
        for review in result.external_reviews[:MAX_INLINE_SYNC_REVIEWS]
    )
    remaining = len(result.external_reviews) - MAX_INLINE_SYNC_REVIEWS
    if remaining > 0:
        lines.append(
            f"{remaining} additional Search-only REVIEW items "
            "are in the sync report"
        )
    return tuple(lines)
