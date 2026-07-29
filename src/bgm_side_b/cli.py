"""Command-line interface for Bangumi Side B."""

from __future__ import annotations

import argparse
from pathlib import Path

from bgm_side_b import __version__
from bgm_side_b.api import BangumiApiClient
from bgm_side_b.config import load_rules
from bgm_side_b.database import Database
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
            ).run(scope, force=args.force)
        except KeyboardInterrupt:
            return 130
        finally:
            client.close()
        print(f"sync report: {run.sync_report.as_posix()}")
        print(f"tag audit: {run.tag_audit_report.as_posix()}")
        return run.exit_code
    return 2
