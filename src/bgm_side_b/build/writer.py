"""Validated incremental patching for the single ``dist/site`` tree."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bgm_side_b.build.fingerprint import BuildState


class SiteWriteError(RuntimeError):
    """Raised when a site patch cannot be applied safely."""


class BuildBlockedError(SiteWriteError):
    """Raised for a locked or otherwise unavailable output file."""


@dataclass(frozen=True)
class PatchResult:
    """Safe, relative-path-only patch counters for build reports."""

    written: tuple[str, ...]
    deleted: tuple[str, ...]
    reused: tuple[str, ...]
    staged: tuple[str, ...]


class IncrementalSiteWriter:
    """Apply validated file patches without copying a second site snapshot."""

    def __init__(
        self,
        site_directory: Path,
        workspace_directory: Path,
        *,
        state_path: Path | None = None,
    ) -> None:
        self.site_directory = site_directory.resolve()
        self.workspace_directory = workspace_directory.resolve()
        self.state_path = (
            state_path or self.workspace_directory / "build-state.json"
        ).resolve()
        self.staging_directory = self.workspace_directory / "build-staging"

    def apply(
        self,
        desired: Mapping[str, bytes],
        state: BuildState,
        *,
        validate_staged: Callable[[Mapping[str, bytes]], None],
        validate_final: Callable[[Path], None] | None = None,
    ) -> PatchResult:
        """Stage, validate, patch, validate again, and commit state last."""
        normalized = {
            _safe_path(path): bytes(content) for path, content in desired.items()
        }
        self._cleanup_staging()
        self.site_directory.mkdir(parents=True, exist_ok=True)
        old_artifacts = dict(state.artifacts)
        existing = self._existing_paths(old_artifacts)
        changed = tuple(
            path
            for path, content in sorted(normalized.items())
            if not _same_file(self.site_directory / Path(path), content)
        )
        stale = tuple(
            path
            for path in sorted(existing - set(normalized))
            if (self.site_directory / Path(path)).exists()
        )
        try:
            validate_staged(normalized)
        except SiteWriteError:
            raise
        except BaseException as error:
            raise SiteWriteError("staged output validation failed") from error
        run_directory = Path(
            tempfile.mkdtemp(prefix="site-", dir=self.staging_directory)
        )
        backup_directory = Path(
            tempfile.mkdtemp(prefix="backup-", dir=self.staging_directory)
        )
        touched = tuple(sorted(set(changed) | set(stale)))
        backups: dict[str, bool] = {}
        try:
            self._stage_files(run_directory, normalized, changed)
            self._backup_files(backup_directory, touched, backups)
            self._apply_files(run_directory, normalized, changed, stale)
            if validate_final is not None:
                validate_final(self.site_directory)
            final_state = BuildState(
                state.schema,
                state.shared,
                state.quarters,
                state.years,
                state.archive,
                {
                    path: _sha256(content)
                    for path, content in sorted(normalized.items())
                },
            )
            self._commit_state(final_state)
            return PatchResult(
                changed,
                stale,
                tuple(sorted(set(normalized) - set(changed))),
                changed,
            )
        except PermissionError as error:
            self._restore(backup_directory, touched, backups)
            raise BuildBlockedError(
                "site patch blocked by a file in use: "
                f"{_relative_message(error, touched)}"
            ) from error
        except BaseException as error:
            self._restore(backup_directory, touched, backups)
            if isinstance(error, SiteWriteError):
                raise
            raise SiteWriteError(
                "site patch failed; previous output was restored"
            ) from error
        finally:
            shutil.rmtree(run_directory, ignore_errors=True)
            shutil.rmtree(backup_directory, ignore_errors=True)

    def _cleanup_staging(self) -> None:
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        for child in tuple(self.staging_directory.iterdir()):
            if child.is_dir() and child.name not in {".keep"}:
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                child.unlink(missing_ok=True)

    def _existing_paths(self, old_artifacts: Mapping[str, str]) -> set[str]:
        if old_artifacts:
            return {_safe_path(path) for path in old_artifacts}
        return {
            path.relative_to(self.site_directory).as_posix()
            for path in self.site_directory.rglob("*")
            if path.is_file()
        }

    def _stage_files(
        self,
        run_directory: Path,
        desired: Mapping[str, bytes],
        changed: tuple[str, ...],
    ) -> None:
        for relative in changed:
            staged = run_directory / Path(relative)
            staged.parent.mkdir(parents=True, exist_ok=True)
            with staged.open("wb") as stream:
                stream.write(desired[relative])
                stream.flush()
                os.fsync(stream.fileno())

    def _backup_files(
        self,
        backup_directory: Path,
        touched: tuple[str, ...],
        backups: dict[str, bool],
    ) -> None:
        for relative in touched:
            target = self.site_directory / Path(relative)
            if target.is_dir():
                raise SiteWriteError(f"generated path is a directory: {relative}")
            backups[relative] = target.is_file()
            if backups[relative]:
                backup = backup_directory / Path(relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup)

    def _apply_files(
        self,
        run_directory: Path,
        desired: Mapping[str, bytes],
        changed: tuple[str, ...],
        stale: tuple[str, ...],
    ) -> None:
        for relative in changed:
            target = self.site_directory / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(run_directory / Path(relative), target)
            except PermissionError:
                raise
            except OSError as error:
                raise SiteWriteError(
                    f"cannot replace generated file: {relative}"
                ) from error
        for relative in stale:
            target = self.site_directory / Path(relative)
            try:
                target.unlink()
            except FileNotFoundError:
                continue
            except PermissionError:
                raise
            except OSError as error:
                raise SiteWriteError(
                    f"cannot delete generated file: {relative}"
                ) from error

    def _restore(
        self,
        backup_directory: Path,
        touched: tuple[str, ...],
        backups: Mapping[str, bool],
    ) -> None:
        for relative in touched:
            target = self.site_directory / Path(relative)
            backup = backup_directory / Path(relative)
            try:
                if backups.get(relative, False) and backup.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    restore = backup.with_suffix(backup.suffix + ".restore")
                    shutil.copyfile(backup, restore)
                    os.replace(restore, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                # A subsequent build will safely mark the site dirty.  Never mask
                # the original failure with a best-effort rollback error.
                continue

    def _commit_state(self, state: BuildState) -> None:
        from bgm_side_b.build.site_projection import json_bytes

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="build-state-", suffix=".tmp", dir=self.state_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(json_bytes(state.to_dict()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except PermissionError:
            raise
        except OSError as error:
            raise SiteWriteError("cannot commit build state") from error
        finally:
            temporary.unlink(missing_ok=True)


def _safe_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or path.name in {"", "."}
    ):
        raise SiteWriteError("generated path must be relative")
    return path.as_posix()


def _same_file(path: Path, content: bytes) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size != len(content):
            return False
        return path.read_bytes() == content
    except OSError:
        return False


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _relative_message(error: PermissionError, touched: tuple[str, ...]) -> str:
    if touched:
        return touched[0]
    return "generated output"
