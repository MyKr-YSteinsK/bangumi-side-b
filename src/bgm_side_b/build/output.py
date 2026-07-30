"""Staged output replacement that preserves a previous complete build on failure."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bgm_side_b.build.profiles import BuildProfile


class OutputError(RuntimeError):
    """Raised when a generated output cannot be safely promoted."""


@dataclass(frozen=True)
class OutputResult:
    """The final output tree after a successful staging promotion."""

    output_directory: Path
    replaced_previous_output: bool


class AtomicOutput:
    """Build inside ``dist/.staging`` then replace one profile directory safely."""

    def __init__(self, distribution_directory: Path) -> None:
        self.distribution_directory = distribution_directory
        self.staging_directory = distribution_directory / ".staging"

    def generate(
        self,
        profile: BuildProfile,
        writer: Callable[[Path], None],
        validator: Callable[[Path], None],
        *,
        before_promotion: Callable[[Path], None] | None = None,
        on_failure: Callable[[Path], None] | None = None,
    ) -> OutputResult:
        """Generate, validate, and promote a profile without half-replacing output."""
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        stage = self.staging_directory / f"{profile.name}-{uuid.uuid4().hex}"
        stage.mkdir()
        try:
            writer(stage)
            validator(stage)
            if before_promotion is not None:
                before_promotion(stage)
            return self._promote(profile, stage)
        except BaseException:
            if on_failure is not None:
                on_failure(stage)
            _remove_tree(stage)
            raise

    def _promote(self, profile: BuildProfile, stage: Path) -> OutputResult:
        target = self.distribution_directory / profile.output_directory
        backup = self.staging_directory / f"{profile.name}-previous-{uuid.uuid4().hex}"
        had_previous = target.exists()
        try:
            if had_previous:
                target.replace(backup)
            try:
                stage.replace(target)
            except OSError as error:
                if had_previous:
                    try:
                        backup.replace(target)
                    except OSError as restore_error:
                        raise OutputError(
                            "previous output could not be restored"
                        ) from restore_error
                raise OutputError("generated output could not be promoted") from error
            _remove_tree(backup)
            return OutputResult(target, had_previous)
        except OSError as error:
            raise OutputError("generated output could not be promoted") from error


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
