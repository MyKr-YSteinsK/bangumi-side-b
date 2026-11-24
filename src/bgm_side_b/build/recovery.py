"""Durable undo information for one incremental patch to the public tree."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from pathlib import Path

from bgm_side_b.write_recovery import file_hash, replace_bytes


class SiteWriteError(RuntimeError):
    """A site patch could not be applied safely."""


class SiteRecoveryError(SiteWriteError):
    """The old or new tree could not be proven complete."""


_MANIFEST = ".bgmb-patch.json"


def pending_patches(workspace: Path) -> tuple[Path, ...]:
    # Older, unmarked backups cannot be classified safely. A v1 directory
    # becomes active only when its complete manifest is atomically installed.
    staging = workspace / "build-staging"
    active = []
    for path in staging.glob("recovery-*"):
        if path.name.startswith("recovery-v1-"):
            try:
                (path / _MANIFEST).stat()
            except FileNotFoundError:
                continue
            except OSError:
                pass  # An inaccessible manifest still blocks the candidate.
        active.append(path)
    return tuple(active)


def begin_patch(
    directory: Path,
    state: Path,
    after: bytes,
    touched: tuple[str, ...],
    materialized: dict[str, bytes],
) -> None:
    previous = state.read_bytes() if state.is_file() else None
    files = {
        path: [
            file_hash(directory / "files" / path),
            hashlib.sha256(materialized[path]).hexdigest()
            if path in materialized
            else None,
        ]
        for path in touched
    }
    manifest = {
        "files": files,
        "previous": None if previous is None else base64.b64encode(previous).decode(),
        "state": hashlib.sha256(after).hexdigest(),
    }
    replace_bytes(directory / _MANIFEST, json.dumps(manifest).encode())


def discard_patch(directory: Path) -> None:
    # Retire the manifest only after all writes are reconciled. A terminated
    # cleanup then leaves unreferenced backups, never a partially active patch.
    (directory / _MANIFEST).unlink()
    shutil.rmtree(directory, ignore_errors=True)


def recover_patch(
    site: Path, workspace: Path, state: Path, *, restore_all: bool = False
) -> None:
    pending = pending_patches(workspace)
    if not pending:
        return
    if len(pending) != 1:
        raise SiteRecoveryError(
            "multiple pending site patches; inspect retained backups"
        )
    directory = pending[0]
    try:
        manifest = json.loads((directory / _MANIFEST).read_bytes())
        previous = manifest["previous"]
        if previous is not None:
            previous = base64.b64decode(previous, validate=True)
        old_state = None if previous is None else hashlib.sha256(previous).hexdigest()
        current = file_hash(state)
        committed = current == manifest["state"] and current != old_state
        if not committed and current != old_state:
            invalidated = directory / "build-state.invalid.json"
            if current is not None or file_hash(invalidated) != old_state:
                raise SiteRecoveryError("site recovery conflict: build-state.json")
        repairs = []
        all_after = True
        for relative, (before, after) in manifest["files"].items():
            target = (site / relative).resolve()
            backup = (directory / "files" / relative).resolve()
            if not target.is_relative_to(site) or not backup.is_relative_to(directory):
                raise SiteRecoveryError("invalid site recovery path")
            actual = file_hash(target)
            if actual != after:
                all_after = False
            if committed and actual == after:
                continue
            if actual not in (before, after) or committed:
                raise SiteRecoveryError(f"site recovery conflict: {relative}")
            if actual == before and not restore_all:
                continue
            if file_hash(backup) != before:
                raise SiteRecoveryError(f"site recovery backup mismatch: {relative}")
            repairs.append((target, backup, before))
        # A repair of a missing artifact can have identical old and new states.
        if current == manifest["state"] and all_after:
            repairs.clear()
        for target, backup, before in repairs:
            if before is None:
                target.unlink(missing_ok=True)
            else:
                temporary = directory / ".bgmb.restore"
                shutil.copyfile(backup, temporary)
                os.replace(temporary, target)
        if not committed and current != old_state and previous is not None:
            replace_bytes(state, previous)
        discard_patch(directory)
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise SiteRecoveryError(
            "site recovery incomplete; retained patch evidence"
        ) from error
