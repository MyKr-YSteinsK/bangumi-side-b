"""Command-line interface for Bangumi Side B."""

from __future__ import annotations

import argparse

from bgm_side_b import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the currently available commands."""
    parser = argparse.ArgumentParser(
        prog="bgmb",
        description="Local-first Bangumi archive tooling.",
        epilog="Synchronisation commands are planned and are not available yet.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface and return a process exit code."""
    build_parser().parse_args(argv)
    return 0
