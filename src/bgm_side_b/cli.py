"""Command-line interface for Bangumi Side B."""

from __future__ import annotations

import argparse
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.api import BangumiApiClient
from bgm_side_b.audit import ReleaseDataAuditor
from bgm_side_b.build.builder import ArchiveBuilder, BuildError
from bgm_side_b.build.queries import BuildDataError
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.progress import create_progress_reporter
from bgm_side_b.release.publish import Publisher, PublishError
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import (
    SubjectSynchronizer,
    parse_sync_scope,
    validate_release_scope,
)


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
    subparsers.add_parser(
        "audit", help="Read-only audit of first-release workspace data."
    )
    sync_parser = subparsers.add_parser("sync", help="Synchronise subject facts only.")
    _add_progress_arguments(sync_parser)
    sync_parser.add_argument("scope", nargs=2, metavar=("YEAR", "QUARTER_MONTH"))
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh stable subject details as well as ratings.",
    )
    sync_parser.add_argument(
        "--force-images",
        action="store_true",
        help="Revalidate and redownload cached cover images.",
    )
    build_command = subparsers.add_parser(
        "build", help="Build offline static archive pages from local SQLite facts."
    )
    _add_progress_arguments(build_command)
    build_command.add_argument("scope", nargs="*", metavar="YEAR_OR_QUARTER")
    build_command.add_argument(
        "--all", action="store_true", help="Build every configured release quarter."
    )
    build_command.add_argument(
        "--discard-pending",
        action="store_true",
        help="Explicitly discard a retained verified staging output before rebuilding.",
    )
    publish_command = subparsers.add_parser(
        "publish", help="Validate and manually publish an existing Pages candidate."
    )
    _add_progress_arguments(publish_command)
    publish_command.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and validate without Git publication.",
    )
    publish_command.add_argument("--remote", default="origin")
    publish_command.add_argument("--branch", default="gh-pages")
    build_command.add_argument(
        "--target",
        choices=("all", "local", "pages"),
        default="all",
        help="Select local, Pages, or both static output profiles.",
    )
    promote_command = subparsers.add_parser(
        "promote", help="Promote a retained verified static output without rebuilding."
    )
    _add_progress_arguments(promote_command)
    promote_command.add_argument("profile", choices=("local", "pages"))
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
        settings, _, _ = load_rules(root / "config")
        result = ReleaseDataAuditor(root, settings).audit()
        print(result.render())
        return 0 if result.passed else 1
    if args.verbose and args.progress == "off":
        parser.error("--progress off cannot be combined with --verbose")
    if args.command == "sync":
        try:
            scope = parse_sync_scope(args.scope)
        except ValueError as error:
            parser.error(str(error))
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings, tag_rules, source_rules = load_rules(root / "config")
        try:
            validate_release_scope(scope, settings)
        except ValueError as error:
            parser.error(str(error))
        database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
        repository = SubjectRepository(database)
        with create_progress_reporter(args, "sync") as reporter:
            client = BangumiApiClient(
                timeout_seconds=settings.sync.request_timeout_seconds,
                max_retries=settings.sync.max_retries,
                concurrency=settings.sync.api_concurrency,
                reporter=reporter,
            )
            try:
                run = SubjectSynchronizer(
                    repository,
                    client,
                    settings,
                    tag_rules,
                    source_rules,
                    reports_directory=root / "workspace" / "reports",
                    reporter=reporter,
                ).run(scope, force=args.force, force_images=args.force_images)
            except KeyboardInterrupt:
                reporter.warning(
                    stage="interrupted",
                    message="已收到 Ctrl+C，停止安排新任务。",
                )
                return 130
            finally:
                client.close()
        print(f"sync report: {_relative_output_path(root, run.sync_report)}")
        print(f"tag audit: {_relative_output_path(root, run.tag_audit_report)}")
        print(f"country audit: {_relative_output_path(root, run.country_audit_report)}")
        return run.exit_code
    if args.command == "build":
        if args.all == bool(args.scope):
            parser.error("build accepts one scope or --all")
        try:
            scope = None if args.all else parse_sync_scope(args.scope)
        except ValueError as error:
            parser.error(str(error))
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings, tag_rules, source_rules = load_rules(root / "config")
        database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
        with create_progress_reporter(args, "build") as reporter:
            try:
                run = ArchiveBuilder(
                    root, database, settings, tag_rules, source_rules, reporter=reporter
                ).build(
                    scope, target=args.target, discard_pending=args.discard_pending
                )
            except KeyboardInterrupt:
                reporter.warning(
                    stage="interrupted",
                    message="已中断｜上一版 dist 输出保持不变",
                )
                return 130
            except (BuildDataError, BuildError, ValueError) as error:
                parser.error(str(error))
        print(f"build report: {_relative_output_path(root, run.report_path)}")
        return 0
    if args.command == "promote":
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings, tag_rules, source_rules = load_rules(root / "config")
        database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
        with create_progress_reporter(args, "promote") as reporter:
            reporter.start(stage="pending", message="正在检查已验证的 pending 构建")
            try:
                result = ArchiveBuilder(
                    root, database, settings, tag_rules, source_rules, reporter=reporter
                ).promote(args.profile)
            except (BuildDataError, BuildError, ValueError) as error:
                parser.error(str(error))
            reporter.complete(
                stage="summary",
                message="已完成已验证构建的原子替换",
                counters={"重试": result.promotion_retries},
            )
        print(f"已完成 dist/{args.profile} 的恢复替换")
        return 0
    if args.command == "publish":
        root = find_project_root()
        if root is None:
            parser.error(
                "could not find a project root containing pyproject.toml and config"
            )
        with create_progress_reporter(args, "publish") as reporter:
            try:
                run = Publisher(root, reporter).publish(
                    dry_run=args.dry_run, remote=args.remote, branch=args.branch
                )
            except KeyboardInterrupt:
                reporter.warning(
                    stage="interrupted",
                    message="已中断｜未创建远端提交",
                )
                return 130
            except PublishError as error:
                parser.error(str(error))
        print(f"publish report: {_relative_output_path(root, run.report_path)}")
        if run.dry_run:
            print(f"仅 dry-run：资料版本 {run.release_version} 未发布")
        else:
            print(f"已发布资料版本：{run.release_version}")
        return 0
    return 2


def _relative_output_path(root: Path, path: Path) -> str:
    """Return a safe project-relative path for user-facing command summaries."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "report unavailable"
