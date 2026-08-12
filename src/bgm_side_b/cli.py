"""Command-line interface for Bangumi Side B."""

from __future__ import annotations

import argparse
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.adjudication import ArchiveAdjudicator, AssignmentError, render_review
from bgm_side_b.admission import QuarterOverride
from bgm_side_b.api import BangumiApiClient
from bgm_side_b.archive_config import (
    load_archive_source_rules,
    load_archive_sync_settings,
)
from bgm_side_b.build.serve import ServeError, serve_site
from bgm_side_b.build.site_builder import (
    BuildError as SiteBuildError,
)
from bgm_side_b.build.site_builder import (
    UnifiedSiteBuilder,
)
from bgm_side_b.config import load_tag_rules
from bgm_side_b.database import Database as ArchiveDatabase
from bgm_side_b.database import DatabaseError
from bgm_side_b.domain import Quarter
from bgm_side_b.overrides import load_quarter_overrides, save_quarter_overrides
from bgm_side_b.progress import create_progress_reporter
from bgm_side_b.release.unified_audit import UnifiedReleaseAuditor
from bgm_side_b.release.workflow import (
    WorkflowError,
    doctor,
    local_status,
    prepare_release,
    publish_prepared_release,
)
from bgm_side_b.repository import SubjectRepository as ArchiveSubjectRepository
from bgm_side_b.sync import (
    ArchiveSynchronizer,
    QuarterSyncResult,
    SyncError,
    parse_sync_scope,
)

MAX_INLINE_SYNC_REVIEWS = 10


def find_project_root(start: Path | None = None) -> Path | None:
    """Find the nearest project root without exposing local paths on failure."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "config").is_dir():
            return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the currently available commands."""
    parser = argparse.ArgumentParser(
        prog="bgmb",
        description="Local-first Bangumi archive tooling.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("audit", help="Read-only audit of current archive facts.")
    doctor_command = subparsers.add_parser("doctor", help="检查本地与远端发布环境。")
    doctor_command.add_argument("--local", action="store_true", help="只检查本地状态。")
    subparsers.add_parser("status", help="快速查看本地发布状态。")
    sync_parser = subparsers.add_parser(
        "sync",
        help="Synchronise facts/covers and build the affected static site scope.",
    )
    _add_progress_arguments(sync_parser)
    sync_parser.add_argument("scope", nargs="*", metavar="YEAR_OR_QUARTER")
    sync_parser.add_argument(
        "--from",
        dest="range_start",
        nargs=2,
        metavar=("YEAR", "QUARTER_MONTH"),
    )
    sync_parser.add_argument(
        "--to",
        dest="range_end",
        nargs=2,
        metavar=("YEAR", "QUARTER_MONTH"),
    )
    sync_parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Refresh complete quarters inside an explicit range.",
    )
    review_command = subparsers.add_parser(
        "review", help="List unresolved archive review items."
    )
    review_command.add_argument("scope", nargs="*", metavar="YEAR_OR_QUARTER")
    assign_command = subparsers.add_parser(
        "assign", help="Set one manual archive quarter."
    )
    assign_command.add_argument("subject_id", type=int, metavar="BGM_ID")
    assign_command.add_argument("assignment", nargs="*", metavar="YEAR_OR_QUARTER")
    assign_group = assign_command.add_mutually_exclusive_group()
    assign_group.add_argument("--unassigned", action="store_true")
    assign_group.add_argument("--clear", action="store_true")
    build_command = subparsers.add_parser(
        "build", help="Build offline static archive pages from local SQLite facts."
    )
    _add_progress_arguments(build_command)
    build_command.add_argument("scope", nargs="*", metavar="YEAR_OR_QUARTER")
    build_command.add_argument(
        "--all", action="store_true", help="Build every configured release quarter."
    )
    serve_command = subparsers.add_parser(
        "serve", help="Serve the existing dist/site tree on localhost."
    )
    serve_command.add_argument("--port", type=int, default=8000)
    release_command = subparsers.add_parser(
        "release", help="执行明确的发布准备或真实发布编排。"
    )
    release_subparsers = release_command.add_subparsers(dest="release_command")
    prepare_command = release_subparsers.add_parser(
        "prepare", help="审计、构建统一站点并执行发布 dry-run。"
    )
    _add_progress_arguments(prepare_command)
    release_publish_command = release_subparsers.add_parser(
        "publish", help="发布仍然有效的 prepared release。"
    )
    _add_progress_arguments(release_publish_command)
    return parser


def _add_progress_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared terminal-output controls to a long-running command."""
    parser.add_argument(
        "--progress",
        choices=("auto", "plain", "off"),
        default="auto",
        help="Select automatic, plain, or disabled progress output.",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--verbose", action="store_true", help="Record every progress event."
    )
    output_group.add_argument(
        "--quiet", action="store_true", help="Disable progress output."
    )


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return 0
    if args.command == "audit":
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
        result = UnifiedReleaseAuditor(root, settings).audit()
        print(result.render())
        return 0 if result.passed else 1
    if args.command in {"review", "assign"}:
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        try:
            settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
            database = ArchiveDatabase(
                root / "workspace" / "data" / "bangumi-side-b.sqlite3"
            )
            repository = ArchiveSubjectRepository(database)
            if args.command == "review":
                if len(args.scope) not in {0, 2}:
                    parser.error("review accepts no scope or YEAR QUARTER_MONTH")
                quarter = (
                    None
                    if not args.scope
                    else Quarter(int(args.scope[0]), int(args.scope[1]))
                )
                if not database.path.exists():
                    print("Bangumi Side B — REVIEW\n\n0 unresolved subjects")
                    return 0
                print(render_review(repository, quarter))
                return 0
            database.initialize()
            override = _assignment_override(args)
            adjudicator = ArchiveAdjudicator(
                repository,
                root / "config" / "quarter-overrides.toml",
                settings.excluded_subject_ids,
            )
            existing = repository.get_subject_facts(args.subject_id)
            report_warning = None
            if existing is not None:
                snapshot = (
                    adjudicator.clear(args.subject_id)
                    if args.clear
                    else adjudicator.assign(args.subject_id, override)
                )
                report_path = None
            else:
                if args.clear:
                    raise AssignmentError(
                        "cannot clear an assignment for an unknown subject"
                    )
                assignments = load_quarter_overrides(
                    root / "config" / "quarter-overrides.toml"
                )
                previous = dict(assignments)
                assignments[args.subject_id] = override
                save_quarter_overrides(
                    root / "config" / "quarter-overrides.toml", assignments
                )
                client = BangumiApiClient(
                    timeout_seconds=settings.request_timeout_seconds,
                    max_retries=settings.max_retries,
                    concurrency=settings.api_concurrency,
                )
                try:
                    imported = ArchiveSynchronizer(
                        repository,
                        client,
                        settings,
                        load_archive_source_rules(
                            root / "config" / "source-rules.toml"
                        ),
                        overrides_path=root / "config" / "quarter-overrides.toml",
                        workspace_directory=root / "workspace",
                        reports_directory=root / "workspace" / "reports",
                    ).import_single_subject(args.subject_id, override)
                except BaseException:
                    save_quarter_overrides(
                        root / "config" / "quarter-overrides.toml", previous
                    )
                    raise
                finally:
                    client.close()
                snapshot = imported.snapshot
                report_path = imported.report_path
                report_warning = imported.report_warning
        except (AssignmentError, SyncError, ValueError) as error:
            parser.error(str(error))
        quarter = snapshot.premiere.quarter if snapshot.premiere else None
        if quarter is None:
            print(f"assignment saved: {snapshot.subject.subject_id} is unassigned")
        else:
            print(
                "assignment saved: "
                f"{snapshot.subject.subject_id} -> "
                f"{quarter.year:04d}-{quarter.month:02d}"
            )
        if report_path is not None:
            print(f"manual import report: {_relative_output_path(root, report_path)}")
        if report_warning is not None:
            print(f"warning: {report_warning}")
        return 0
    if getattr(args, "verbose", False) and getattr(args, "progress", "auto") == "off":
        parser.error("--progress off cannot be combined with --verbose")
    if args.command in {"doctor", "status", "release"}:
        root = find_project_root()
        if root is None:
            parser.error("找不到包含 pyproject.toml 和 config 的项目根目录")
        if args.command == "status":
            try:
                print(local_status(root).render_status())
            except (ValueError, WorkflowError) as error:
                parser.error(f"状态检查失败：{error}")
            return 0
        if args.command == "doctor":
            try:
                print(doctor(root, local_only=args.local).render())
            except (ValueError, WorkflowError) as error:
                parser.error(f"环境检查失败：{error}")
            return 0
        if args.release_command is None:
            parser.error("release 需要 prepare 或 publish 子命令")
        with create_progress_reporter(args, "release") as reporter:
            try:
                if args.release_command == "prepare":
                    run = prepare_release(root, reporter)
                else:
                    run = publish_prepared_release(root, reporter)
            except KeyboardInterrupt:
                reporter.warning(stage="interrupted", message="已中断；未创建新的发布")
                return 130
            except (
                WorkflowError,
                ValueError,
            ) as error:
                parser.error(str(error))
        if args.release_command == "prepare":
            print(f"prepared release: {_relative_output_path(root, run.state_path)}")
            print(f"dry-run report: {_relative_output_path(root, run.report_path)}")
        else:
            report = (
                _relative_output_path(root, run.report_path)
                if run.report_path.is_file()
                else "unavailable"
            )
            print(f"publish report: {report}")
            for warning in run.warnings:
                print(f"warning: {warning}")
        return 0
    if args.command == "sync":
        try:
            scope = parse_sync_scope(
                args.scope,
                range_start=args.range_start,
                range_end=args.range_end,
                refresh_existing=args.refresh_existing,
            )
        except ValueError as error:
            parser.error(str(error))
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
        source_rules = load_archive_source_rules(root / "config" / "source-rules.toml")
        database = ArchiveDatabase(
            root / "workspace" / "data" / "bangumi-side-b.sqlite3"
        )
        repository = ArchiveSubjectRepository(database)
        with create_progress_reporter(args, "sync") as reporter:
            client = BangumiApiClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
                concurrency=settings.api_concurrency,
                reporter=reporter,
            )
            try:
                run = ArchiveSynchronizer(
                    repository,
                    client,
                    settings,
                    source_rules,
                    overrides_path=root / "config" / "quarter-overrides.toml",
                    workspace_directory=root / "workspace",
                    reports_directory=root / "workspace" / "reports",
                    reporter=reporter,
                ).run(scope)
            except (DatabaseError, SyncError, ValueError) as error:
                parser.error(str(error))
            except KeyboardInterrupt:
                reporter.warning(
                    stage="interrupted",
                    message="已收到 Ctrl+C，停止安排新任务。",
                )
                return 130
            finally:
                client.close()
        print(f"sync report: {_relative_output_path(root, run.report_path)}")
        if scope.is_single_quarter and run.quarters:
            result = run.quarters[0]
            for line in _sync_summary_lines(result):
                print(line)
            if result.reviews:
                if len(result.reviews) <= MAX_INLINE_SYNC_REVIEWS:
                    print(render_review(repository, scope.start))
                else:
                    print(
                        f"{len(result.reviews)} persisted REVIEW items; "
                        "run bgmb review for the complete local queue"
                    )
            for review in result.external_reviews[:MAX_INLINE_SYNC_REVIEWS]:
                print(
                    "REVIEW "
                    f"{review['subject_id']} {review['issue_code']}｜"
                    f"{review['command']}"
                )
            remaining = len(result.external_reviews) - MAX_INLINE_SYNC_REVIEWS
            if remaining > 0:
                print(
                    f"{remaining} additional Search-only REVIEW items "
                    "are in the sync report"
                )
        if run.exit_code == 0:
            try:
                tags = load_tag_rules(
                    root / "config" / "allowed-tags.toml",
                )
                with create_progress_reporter(args, "build") as build_reporter:
                    build_run = UnifiedSiteBuilder(
                        root,
                        database,
                        tags,
                        workspace_directory=root / "workspace",
                        reporter=build_reporter,
                        excluded_subject_ids=settings.excluded_subject_ids,
                    ).build()
            except (SiteBuildError, ServeError, ValueError) as error:
                print(f"sync facts committed but automatic build failed: {error}")
                return 1
            print(
                "facts synchronized; incremental site build: "
                f"{_relative_output_path(root, build_run.report_path)}"
            )
        return run.exit_code
    if args.command == "serve":
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        try:
            serve_site(root / "dist" / "site", port=args.port)
        except ServeError as error:
            parser.error(str(error))
        return 0
    if args.command == "build":
        if args.all == bool(args.scope):
            parser.error("build accepts one scope or --all")
        try:
            scope = None if args.all else _parse_build_quarter(args.scope)
        except ValueError as error:
            parser.error(str(error))
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings = load_archive_sync_settings(root / "config" / "bangumi.toml")
        tag_rules = load_tag_rules(
            root / "config" / "allowed-tags.toml",
        )
        database = ArchiveDatabase(
            root / "workspace" / "data" / "bangumi-side-b.sqlite3"
        )
        with create_progress_reporter(args, "build") as reporter:
            try:
                run = UnifiedSiteBuilder(
                    root,
                    database,
                    tag_rules,
                    reporter=reporter,
                    excluded_subject_ids=settings.excluded_subject_ids,
                ).build(scope)
            except KeyboardInterrupt:
                reporter.warning(
                    stage="interrupted",
                    message="已中断｜上一版 dist 输出保持不变",
                )
                return 130
            except (DatabaseError, SiteBuildError, ValueError) as error:
                parser.error(str(error))
        print(f"build report: {_relative_output_path(root, run.report_path)}")
        return 0
    return 2


def _sync_summary_lines(result: QuarterSyncResult) -> tuple[str, ...]:
    """Render the bounded evidence summary for a single-quarter sync."""
    lines = [
        (
            f"NEW TV {result.accepted_tv} | "
            f"CONTINUING TV {result.continuing_end_date + result.continuing_episode} | "
            f"MOVIE {result.accepted_movie}"
        ),
        (
            "continuing evidence: "
            f"end_date={result.continuing_end_date}, "
            f"main_episode={result.continuing_episode}, "
            f"unresolved={result.continuing_unresolved}"
        ),
    ]
    lines.extend(
        (
            "AUTO PREMIERE "
            f"{item['subject_id']}: {item['air_date']} -> "
            f"{item['premiere_quarter']} ({item['evidence']})"
        )
        for item in result.early_premieres
    )
    if result.warnings or result.errors:
        lines.append(
            f"exceptions: warnings={len(result.warnings)}, errors={len(result.errors)}"
        )
    return tuple(lines)


def _relative_output_path(root: Path, path: Path) -> str:
    """Return a safe project-relative path for user-facing command summaries."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "report unavailable"


def _parse_build_quarter(values: list[str]) -> Quarter:
    """Parse the narrow offline build target without consulting network state."""
    if len(values) != 2:
        raise ValueError("build requires YEAR QUARTER_MONTH or --all")
    try:
        return Quarter(int(values[0]), int(values[1]))
    except (TypeError, ValueError) as error:
        raise ValueError("build requires a valid YEAR and QUARTER_MONTH") from error


def _assignment_override(args: argparse.Namespace) -> QuarterOverride:
    if args.clear:
        if args.assignment or args.unassigned:
            raise ValueError(
                "--clear cannot be combined with a quarter or --unassigned"
            )
        return QuarterOverride(None)
    if args.unassigned:
        if args.assignment:
            raise ValueError("--unassigned cannot be combined with a quarter")
        return QuarterOverride(None)
    if len(args.assignment) != 2:
        raise ValueError("assign requires YEAR QUARTER_MONTH, --unassigned, or --clear")
    return QuarterOverride(Quarter(int(args.assignment[0]), int(args.assignment[1])))
