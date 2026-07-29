"""Command-line interface for Bangumi Side B."""

from __future__ import annotations

import argparse
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.api import BangumiApiClient
from bgm_side_b.build.builder import ArchiveBuilder, BuildError
from bgm_side_b.build.queries import BuildDataError
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
from bgm_side_b.release.publish import Publisher, PublishError
from bgm_side_b.repository import SubjectRepository
from bgm_side_b.sync import SubjectSynchronizer, parse_sync_scope


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
    sync_parser = subparsers.add_parser("sync", help="Synchronise subject facts only.")
    sync_parser.add_argument("scope", nargs="+", metavar="YEAR_OR_RANGE")
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh stable subject details as well as ratings.",
    )
    sync_parser.add_argument(
        "--force-images",
        action="store_true",
        help="Revalidate and redownload cached cover and character images.",
    )
    build_command = subparsers.add_parser(
        "build", help="Build offline static archive pages from local SQLite facts."
    )
    build_command.add_argument("scope", nargs="*", metavar="YEAR_OR_RANGE")
    build_command.add_argument(
        "--all", action="store_true", help="Build every currently stored quarter."
    )
    publish_command = subparsers.add_parser(
        "publish", help="Validate and manually publish an existing Pages candidate."
    )
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 0
    if args.command == "sync":
        try:
            scope = parse_sync_scope(args.scope)
        except ValueError as error:
            build_parser().error(str(error))
        root = find_project_root()
        if root is None:
            build_parser().error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings, tag_rules, source_rules = load_rules(root / "config")
        database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
        repository = SubjectRepository(database)
        client = BangumiApiClient(
            timeout_seconds=settings.sync.request_timeout_seconds,
            max_retries=settings.sync.max_retries,
            concurrency=settings.sync.api_concurrency,
        )
        try:
            run = SubjectSynchronizer(
                repository,
                client,
                settings,
                tag_rules,
                source_rules,
                reports_directory=root / "workspace" / "reports",
            ).run(scope, force=args.force, force_images=args.force_images)
        except KeyboardInterrupt:
            return 130
        finally:
            client.close()
        print(f"sync report: {run.sync_report.as_posix()}")
        print(f"tag audit: {run.tag_audit_report.as_posix()}")
        return run.exit_code
    if args.command == "build":
        if args.all == bool(args.scope):
            build_parser().error("build accepts one scope or --all")
        try:
            scope = None if args.all else parse_sync_scope(args.scope)
        except ValueError as error:
            build_parser().error(str(error))
        root = find_project_root()
        if root is None:
            build_parser().error(
                "could not find a project root containing pyproject.toml and config"
            )
        settings, tag_rules, source_rules = load_rules(root / "config")
        database = Database(root / "workspace" / "data" / "bangumi-side-b.sqlite3")
        try:
            run = ArchiveBuilder(
                root, database, settings, tag_rules, source_rules
            ).build(scope, target=args.target)
        except (BuildDataError, BuildError) as error:
            build_parser().error(str(error))
        print(f"build report: {run.report_path.as_posix()}")
        return 0
    if args.command == "publish":
        root = find_project_root()
        if root is None:
            build_parser().error(
                "could not find a project root containing pyproject.toml and config"
            )
        try:
            run = Publisher(root).publish(
                dry_run=args.dry_run, remote=args.remote, branch=args.branch
            )
        except PublishError as error:
            build_parser().error(str(error))
        print(f"publish report: {run.report_path.as_posix()}")
        if run.dry_run:
            print(f"dry run only: {run.release_version} was not published")
        else:
            print(f"published release: {run.release_version}")
        return 0
    return 2
