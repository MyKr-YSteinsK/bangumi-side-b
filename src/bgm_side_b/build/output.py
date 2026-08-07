"""Staged output replacement with Windows-lock recovery safeguards."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bgm_side_b.build.profiles import BuildProfile

_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


class OutputError(RuntimeError):
    """Raised when a static output cannot be safely promoted."""


class PendingPromotionError(OutputError):
    """Raised after retaining a fully validated staging tree for recovery."""


class PromotionLockedError(OutputError):
    """Raised when bounded retries cannot replace an output directory."""

    def __init__(self, retries: int) -> None:
        super().__init__("generated output could not be promoted")
        self.retries = retries


@dataclass(frozen=True)
class OutputResult:
    """The final output tree after a successful staging promotion."""

    output_directory: Path
    replaced_previous_output: bool
    promotion_retries: int = 0
    pending_promotion: bool = False


class AtomicOutput:
    """Build in ``dist/.staging`` and retain verified output after a Windows lock."""

    def __init__(
        self, distribution_directory: Path, *, workspace_directory: Path | None = None
    ) -> None:
        self.distribution_directory = distribution_directory
        self.staging_directory = distribution_directory / ".staging"
        self.workspace_directory = workspace_directory

    def generate(
        self,
        profile: BuildProfile,
        writer: Callable[[Path], None],
        validator: Callable[[Path], None],
        *,
        before_promotion: Callable[[Path], None] | None = None,
        on_failure: Callable[[Path], None] | None = None,
        pending_metadata: dict[str, object] | None = None,
    ) -> OutputResult:
        """Generate, validate, and promote without replacing a complete old output."""
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        self.require_no_pending(profile)
        self.require_no_recovery(profile)
        self._probe(profile)
        stage = self.staging_directory / f"{profile.name}-{uuid.uuid4().hex}"
        stage.mkdir()
        try:
            writer(stage)
            validator(stage)
            if before_promotion is not None:
                before_promotion(stage)
            try:
                return self._promote(profile, stage)
            except PromotionLockedError as error:
                if self.workspace_directory is None or pending_metadata is None:
                    raise
                verified = self.staging_directory / (
                    f"{profile.name}-verified-{uuid.uuid4().hex}"
                )
                self._replace(stage, verified)
                self._write_pending(profile, verified, pending_metadata)
                raise PendingPromotionError(
                    "已保留已验证的构建；请关闭占用后运行："
                    f"bgmb promote {profile.name}"
                ) from error
        except BaseException:
            if on_failure is not None:
                on_failure(stage)
            _remove_tree(stage)
            raise

    def require_no_pending(self, profile: BuildProfile) -> None:
        """Refuse to overwrite a retained verified staging candidate silently."""
        if self._pending_path().is_file():
            pending = self._read_pending()
            if pending.get("profile") == profile.name:
                raise OutputError(
                    "检测到已验证但尚未替换的构建；请先运行："
                    f"bgmb promote {profile.name}"
                )

    def preflight(self, profile: BuildProfile) -> None:
        """Check that an existing output is replaceable before rendering a stage."""
        self.require_no_recovery(profile)
        self._probe(profile)

    def require_no_recovery(self, profile: BuildProfile) -> None:
        """Refuse to overwrite a complete old tree whose restore was not confirmed."""
        if self._recovery_trees(profile):
            raise OutputError(
                "检测到上一版输出尚未恢复；请先手动确认 dist/.staging 中保留的 "
                "recovery tree"
            )

    def discard_pending(self, profile: BuildProfile) -> bool:
        """Explicitly discard only the retained staging tree for one profile."""
        if not self._pending_path().is_file():
            return False
        pending = self._read_pending()
        if pending.get("profile") != profile.name:
            return False
        stage = self._pending_stage(pending)
        _remove_tree(stage)
        self._pending_path().unlink(missing_ok=True)
        return True

    def promote(
        self,
        profile: BuildProfile,
        validator: Callable[[Path], None],
        *,
        metadata: dict[str, object],
    ) -> OutputResult:
        """Promote a retained candidate after revalidating its identity and tree."""
        if not self._pending_path().is_file():
            raise OutputError("没有可恢复的 pending promotion")
        pending = self._read_pending()
        if pending.get("profile") != profile.name:
            raise OutputError("没有该输出类型的 pending promotion")
        for key in ("source_commit", "app_version", "data_generation"):
            if pending.get(key) != metadata.get(key):
                raise OutputError("pending promotion 已失效；请显式丢弃后重新构建")
        stage = self._pending_stage(pending)
        if not stage.is_dir() or pending.get("tree_hash") != _tree_hash(stage):
            raise OutputError("pending promotion 内容已变化；请显式丢弃后重新构建")
        validator(stage)
        self._probe(profile)
        try:
            result = self._promote(profile, stage)
        except PromotionLockedError as error:
            raise OutputError("输出目录仍被占用，请关闭占用后重试 promote") from error
        self._pending_path().unlink(missing_ok=True)
        return result

    def pending_profile(self) -> str | None:
        """Return the retained profile name without exposing a local filesystem path."""
        if not self._pending_path().is_file():
            return None
        value = self._read_pending().get("profile")
        return value if isinstance(value, str) else None

    def _probe(self, profile: BuildProfile) -> None:
        """Briefly rename and restore an existing target before expensive rendering."""
        target = self.distribution_directory / profile.output_directory
        if not target.exists():
            return
        self.staging_directory.mkdir(parents=True, exist_ok=True)
        probe = self.staging_directory / f"{profile.name}-probe-{uuid.uuid4().hex}"
        target_moved = False
        target_restored = False
        try:
            self._replace(target, probe)
            target_moved = True
            self._replace(probe, target)
            target_restored = True
        except OSError as error:
            if target_moved and not target_restored:
                try:
                    self._replace(probe, target)
                    target_restored = True
                    return
                except OSError as restore_error:
                    retained = self._retain_recovery(profile, probe)
                    if retained is None:
                        raise OutputError(
                            "上一版输出未确认恢复；请手动检查后再试"
                        ) from restore_error
                    raise OutputError(
                        "上一版输出未恢复到原位置，但完整副本已保留；请处理后再试"
                    ) from restore_error
            elif probe.exists() and not target.exists():
                retained = self._retain_recovery(profile, probe)
                if retained is None:
                    raise OutputError(
                        "上一版输出位置无法确认；请手动检查后再试"
                    ) from error
                raise OutputError(
                    "上一版输出位置无法确认，但完整副本已保留；请处理后再试"
                ) from error
            raise OutputError(
                f"dist/{profile.output_directory} 当前被占用，请关闭使用它的程序后重试"
            ) from error
        finally:
            if not target_moved or target_restored:
                _remove_tree(probe)

    def _retain_recovery(self, profile: BuildProfile, probe: Path) -> Path | None:
        """Keep an un-restored old output without assuming another rename is safe."""
        if not probe.exists():
            return None
        recovery = self.staging_directory / (
            f"{profile.name}-recovery-{uuid.uuid4().hex}"
        )
        try:
            self._replace(probe, recovery)
        except OSError:
            return probe if probe.exists() else None
        return recovery if recovery.exists() else None

    def _recovery_trees(self, profile: BuildProfile) -> tuple[Path, ...]:
        """Return only retained same-profile probe/recovery trees."""
        if not self.staging_directory.is_dir():
            return ()
        return tuple(
            path
            for pattern in (
                f"{profile.name}-recovery-*",
                f"{profile.name}-probe-*",
            )
            for path in self.staging_directory.glob(pattern)
            if path.is_dir()
        )

    def _promote(self, profile: BuildProfile, stage: Path) -> OutputResult:
        target = self.distribution_directory / profile.output_directory
        backup = self.staging_directory / f"{profile.name}-previous-{uuid.uuid4().hex}"
        had_previous = target.exists()
        retries = 0
        while True:
            try:
                if had_previous and target.exists():
                    self._replace(target, backup)
                self._replace(stage, target)
            except OSError as error:
                if backup.exists() and not target.exists():
                    try:
                        self._replace(backup, target)
                    except OSError as restore_error:
                        raise OutputError(
                            "previous output could not be restored"
                        ) from restore_error
                if _is_windows_lock(error) and retries < len(_RETRY_DELAYS):
                    time.sleep(_RETRY_DELAYS[retries])
                    retries += 1
                    continue
                if _is_windows_lock(error):
                    raise PromotionLockedError(retries) from error
                raise OutputError("generated output could not be promoted") from error
            _remove_tree(backup)
            return OutputResult(target, had_previous, promotion_retries=retries)

    def _replace(self, source: Path, destination: Path) -> None:
        source.replace(destination)

    def _pending_path(self) -> Path:
        if self.workspace_directory is None:
            return self.staging_directory / ".pending-unavailable"
        return self.workspace_directory / "state" / "pending-promotion.json"

    def _write_pending(
        self, profile: BuildProfile, stage: Path, metadata: dict[str, object]
    ) -> None:
        relative_stage_path = stage.relative_to(self.distribution_directory).as_posix()
        payload = {
            "schema": 1,
            "profile": profile.name,
            "source_commit": metadata["source_commit"],
            "app_version": metadata["app_version"],
            "data_generation": metadata["data_generation"],
            "tree_hash": _tree_hash(stage),
            "created_at": metadata["created_at"],
            "relative_stage_path": relative_stage_path,
        }
        destination = self._pending_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)

    def _read_pending(self) -> dict[str, object]:
        try:
            value = json.loads(self._pending_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OutputError("pending promotion 状态无效") from error
        required = {
            "schema",
            "profile",
            "source_commit",
            "app_version",
            "data_generation",
            "tree_hash",
            "created_at",
            "relative_stage_path",
        }
        if value.get("schema") != 1 or not required.issubset(value):
            raise OutputError("pending promotion 状态无效")
        return value

    def _pending_stage(self, pending: dict[str, object]) -> Path:
        relative = pending.get("relative_stage_path")
        if not isinstance(relative, str):
            raise OutputError("pending promotion 状态无效")
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.parts[:1] != (".staging",)
        ):
            raise OutputError("pending promotion 状态无效")
        return self.distribution_directory / Path(*path.parts)


def _is_windows_lock(error: OSError) -> bool:
    return isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
