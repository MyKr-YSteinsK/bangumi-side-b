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


class SiteRecoveryError(SiteWriteError):
    """Raised when touched output cannot be proven restored."""


@dataclass(frozen=True)
class PatchResult:
    """Safe, relative-path-only patch counters for build reports."""

    written: tuple[str, ...]
    deleted: tuple[str, ...]
    reused: tuple[str, ...]
    staged: tuple[str, ...]
    generated_small_files: int = 0
    cover_files_read: int = 0
    cover_files_copied: int = 0


ArtifactSource = bytes | Path | Callable[[], bytes] | None


@dataclass(frozen=True)
class ArtifactSpec:
    """One planned output with lazy content and cheap reuse metadata."""

    relative_path: str
    content_hash: str | None
    size_bytes: int | None
    kind: str
    source: ArtifactSource = None


@dataclass(frozen=True)
class ArtifactPlan:
    """A bounded mapping of output paths to lazy artifact specifications."""

    specs: Mapping[str, ArtifactSpec]


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
        desired: Mapping[str, bytes] | Mapping[str, ArtifactSpec] | ArtifactPlan,
        state: BuildState,
        *,
        validate_staged: Callable[[Mapping[str, bytes]], None],
        validate_final: Callable[[Path], None] | None = None,
    ) -> PatchResult:
        """Stage, validate, patch, validate again, and commit state last."""
        plan = _coerce_plan(desired)
        self._cleanup_staging()
        self.site_directory.mkdir(parents=True, exist_ok=True)
        old_artifacts = dict(state.artifacts)
        existing = self._existing_paths(old_artifacts)
        changed = tuple(
            path
            for path, spec in sorted(plan.specs.items())
            if _needs_materialization(
                self.site_directory / Path(path),
                spec,
                old_artifacts.get(path),
                state,
            )
        )
        stale = tuple(
            path
            for path in sorted(existing - set(plan.specs))
            if (self.site_directory / Path(path)).exists()
        )
        materialized: dict[str, bytes] = {}
        try:
            materialized = {
                path: _materialize(plan.specs[path]) for path in changed
            }
            if materialized:
                validate_staged(materialized)
        except SiteWriteError:
            raise
        except BaseException as error:
            raise SiteWriteError("staged output validation failed") from error
        run_directory = Path(
            tempfile.mkdtemp(prefix="site-", dir=self.staging_directory)
        )
        backup_directory = Path(
            tempfile.mkdtemp(prefix="recovery-pending-", dir=self.staging_directory)
        )
        touched = tuple(sorted(set(changed) | set(stale)))
        backups: dict[str, bool] = {}
        backup_complete = False
        try:
            self._stage_files(run_directory, materialized, changed)
            self._backup_files(backup_directory, touched, backups)
            backup_complete = True
            self._apply_files(run_directory, materialized, changed, stale)
            if validate_final is not None and touched:
                validate_final(self.site_directory)
            final_artifacts = {
                path: (
                    _sha256(materialized[path])
                    if path in materialized
                    else plan.specs[path].content_hash
                    or old_artifacts.get(path, "")
                )
                for path in sorted(plan.specs)
            }
            final_sizes = {
                path: (
                    len(materialized[path])
                    if path in materialized
                    else plan.specs[path].size_bytes
                    if plan.specs[path].size_bytes is not None
                    else state.artifact_sizes.get(path)
                )
                for path in sorted(plan.specs)
            }
            final_state = BuildState(
                state.schema,
                state.shared,
                state.quarters,
                state.years,
                state.archive,
                final_artifacts,
                state.quarter_status,
                {
                    path: int(size)
                    for path, size in final_sizes.items()
                    if size is not None
                },
            )
            self._commit_state(final_state)
            self._cleanup_recovery()
            return PatchResult(
                changed,
                stale,
                tuple(sorted(set(plan.specs) - set(changed))),
                changed,
                sum(plan.specs[path].kind != "cover" for path in changed),
                sum(plan.specs[path].kind == "cover" for path in changed),
                sum(plan.specs[path].kind == "cover" for path in changed),
            )
        except PermissionError as error:
            if backup_complete:
                self._rollback(backup_directory, touched, backups)
            raise BuildBlockedError(
                "site patch blocked by a file in use: "
                f"{_relative_message(error, touched)}"
            ) from error
        except BaseException as error:
            if isinstance(error, SiteRecoveryError):
                raise
            if backup_complete:
                self._rollback(backup_directory, touched, backups)
            if isinstance(error, SiteWriteError):
                raise
            raise SiteWriteError(
                "site patch failed; previous output was restored"
            ) from error
        finally:
            shutil.rmtree(run_directory, ignore_errors=True)
            if not backup_complete:
                shutil.rmtree(backup_directory, ignore_errors=True)

    def _rollback(
        self,
        backup_directory: Path,
        touched: tuple[str, ...],
        backups: Mapping[str, bool],
    ) -> None:
        try:
            self._restore(backup_directory, touched, backups)
        except SiteRecoveryError as error:
            recovery = self._retain_recovery(backup_directory)
            self._invalidate_state(recovery)
            relative = recovery.relative_to(self.workspace_directory).as_posix()
            raise SiteRecoveryError(
                f"site recovery incomplete; next build required: {relative}"
            ) from error
        shutil.rmtree(backup_directory, ignore_errors=True)

    def _cleanup_staging(self) -> None:
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        for child in tuple(self.staging_directory.iterdir()):
            if (
                child.is_dir()
                and child.name not in {".keep"}
                and not child.name.startswith("recovery-")
            ):
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                child.unlink(missing_ok=True)

    def _cleanup_recovery(self) -> None:
        for child in tuple(self.staging_directory.glob("recovery-*")):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

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
        failures: list[str] = []
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
                failures.append(relative)
        if failures:
            raise SiteRecoveryError(
                "site recovery incomplete: " + ", ".join(failures)
            )

    def _retain_recovery(
        self,
        backup_directory: Path,
    ) -> Path:
        """Keep the original backup tree after an incomplete restore."""
        return backup_directory

    def _invalidate_state(self, recovery: Path) -> None:
        if not self.state_path.exists():
            return
        target = recovery / "build-state.invalid.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self.state_path, target)
        except OSError as error:
            try:
                self.state_path.unlink()
            except OSError as unlink_error:
                raise SiteRecoveryError(
                    "site recovery incomplete; build state could not be invalidated"
                ) from unlink_error
            raise SiteRecoveryError(
                "site recovery incomplete; build state move failed"
            ) from error

    def _commit_state(self, state: BuildState) -> None:
        from bgm_side_b.build.site_projection import json_bytes

        content = json_bytes(state.to_dict())
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="build-state-", suffix=".tmp", dir=self.state_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except OSError as error:
            try:
                committed = self.state_path.read_bytes() == content
            except OSError:
                committed = False
            if committed:
                return
            try:
                self.state_path.unlink(missing_ok=True)
            except OSError as invalidation_error:
                raise SiteRecoveryError(
                    "build state commit is ambiguous and could not be invalidated"
                ) from invalidation_error
            if isinstance(error, PermissionError):
                raise
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


def _coerce_plan(
    desired: Mapping[str, bytes] | Mapping[str, ArtifactSpec] | ArtifactPlan,
) -> ArtifactPlan:
    if isinstance(desired, ArtifactPlan):
        specs = desired.specs
    else:
        specs = desired
    normalized: dict[str, ArtifactSpec] = {}
    for path, value in specs.items():
        safe = _safe_path(path)
        if isinstance(value, ArtifactSpec):
            normalized[safe] = ArtifactSpec(
                safe,
                value.content_hash,
                value.size_bytes,
                value.kind,
                value.source,
            )
            continue
        content = bytes(value)
        normalized[safe] = ArtifactSpec(
            safe,
            _sha256(content),
            len(content),
            "generated",
            content,
        )
    return ArtifactPlan(normalized)


def _needs_materialization(
    path: Path,
    spec: ArtifactSpec,
    previous_hash: str | None,
    state: BuildState,
) -> bool:
    if not path.is_file():
        return True
    if spec.content_hash is None and spec.source is not None:
        return True
    expected_hash = spec.content_hash or previous_hash
    if expected_hash is None or previous_hash != expected_hash:
        return True
    expected_size = spec.size_bytes
    if expected_size is None:
        expected_size = state.artifact_sizes.get(spec.relative_path)
    if expected_size is not None:
        try:
            if path.stat().st_size != expected_size:
                return True
        except OSError:
            return True
    return False


def _materialize(spec: ArtifactSpec) -> bytes:
    source = spec.source
    if isinstance(source, bytes):
        content = source
    elif isinstance(source, Path):
        try:
            with source.open("rb") as stream:
                content = b"".join(
                    chunk for chunk in iter(lambda: stream.read(64 * 1024), b"")
                )
        except OSError as error:
            raise SiteWriteError(
                f"cannot read generated source: {spec.relative_path}"
            ) from error
    elif callable(source):
        try:
            content = source()
        except BaseException as error:
            raise SiteWriteError(
                f"cannot generate artifact: {spec.relative_path}"
            ) from error
        if not isinstance(content, bytes):
            raise SiteWriteError("artifact generator must return bytes")
    else:
        raise SiteWriteError(f"artifact source is unavailable: {spec.relative_path}")
    if spec.size_bytes is not None and len(content) != spec.size_bytes:
        raise SiteWriteError(f"artifact size is invalid: {spec.relative_path}")
    if spec.content_hash is not None and _sha256(content) != spec.content_hash:
        raise SiteWriteError(f"artifact hash is invalid: {spec.relative_path}")
    return content


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _relative_message(error: PermissionError, touched: tuple[str, ...]) -> str:
    if touched:
        return touched[0]
    return "generated output"
