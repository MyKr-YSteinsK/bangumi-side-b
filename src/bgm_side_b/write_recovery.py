"""Process ownership and restart recovery for CLI writes to a local archive."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

from bgm_side_b.database import Database


class RecoveryError(RuntimeError):
    """The operation must stop with its recovery evidence intact."""


@contextmanager
def writer_lock(workspace: Path) -> Iterator[None]:
    workspace.mkdir(parents=True, exist_ok=True)
    # Keep the inode stable across owners. A dead process releases its kernel lock.
    with (workspace / "writer.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RecoveryError("workspace writer busy or lock unavailable") from error
        yield


def replace_bytes(path: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".bgmb-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except FileNotFoundError:
        return None


def _base(database: Database) -> Path:
    directory = database.path.resolve().parent
    if directory.name == "data":
        directory = directory.parent
        if directory.name == "workspace":
            directory = directory.parent
    return directory


def pending_facts(database: Database) -> tuple[Path, ...]:
    return tuple(database.path.parent.glob(database.path.name + ".undo-*"))


class FactFiles:
    """Record the exact original bytes before each file edit paired with SQL."""

    def __init__(self, database: Database, connection: sqlite3.Connection) -> None:
        self.database = database
        self.connection = connection
        self.token = uuid.uuid4().hex
        self.directory = database.path.with_name(
            database.path.name + ".undo-" + self.token
        )
        self.started = False

    def remember(self, target: Path, after: bytes | None) -> tuple[Path, str | None]:
        relative = target.resolve().relative_to(_base(self.database)).as_posix()
        if not self.started:
            self.connection.execute(
                "INSERT OR REPLACE INTO database_metadata VALUES ('file_commit', ?)",
                (self.token,),
            )
            self.directory.mkdir()
            self.started = True
        key = hashlib.sha256(relative.encode()).hexdigest()
        record = self.directory / (key + ".json")
        if record.exists():
            raise RecoveryError(f"duplicate transactional file edit: {relative}")
        before = target.read_bytes() if target.exists() else None
        payload = {
            "path": relative,
            "before": None if before is None else base64.b64encode(before).decode(),
            "after": None if after is None else hashlib.sha256(after).hexdigest(),
        }
        replace_bytes(record, json.dumps(payload).encode())
        return record, None if before is None else hashlib.sha256(before).hexdigest()


_active: ContextVar[FactFiles | None] = ContextVar("fact_files", default=None)


def remember_file(target: Path, after: bytes | None) -> None:
    files = _active.get()
    if files is not None:
        files.remember(target, after)


def install_file(temporary: Path, target: Path) -> None:
    """Pair an atomic replacement with SQL when inside a fact transaction."""
    files = _active.get()
    if files is None:
        temporary.replace(target)
        return
    content = temporary.read_bytes()
    record, previous = files.remember(target, content)
    try:
        temporary.replace(target)
    except OSError:
        # A sharing violation can be a soft cover failure. Only release its
        # undo entry once we know the existing file survived the failed call.
        actual = file_hash(target)
        if actual == hashlib.sha256(content).hexdigest():
            return
        if actual != previous:
            raise RecoveryError(f"fact recovery conflict: {target.name}") from None
        record.unlink()
        raise


@contextmanager
def fact_transaction(database: Database) -> Iterator[sqlite3.Connection]:
    if pending_facts(database):
        raise RecoveryError("fact recovery pending; rerun a local writer")
    with closing(database.connect()) as connection:
        files = FactFiles(database, connection)
        context = _active.set(files)
        try:
            with connection:
                connection.execute("BEGIN")
                yield connection
        finally:
            _active.reset(context)
            if files.started:
                recover_facts(database)


def recover_facts(database: Database) -> None:
    directories = pending_facts(database)
    if not directories:
        return
    if len(directories) != 1 or not database.path.is_file():
        raise RecoveryError("cannot determine fact transaction identity")
    directory = directories[0]
    with closing(database.connect()) as connection:
        row = connection.execute(
            "SELECT value FROM database_metadata WHERE key = 'file_commit'"
        ).fetchone()
    committed = row is not None and directory.name.endswith(".undo-" + row[0])
    base = _base(database)
    try:
        repairs = []
        for record in sorted(directory.glob("*.json")):
            payload = json.loads(record.read_bytes())
            target = (base / payload["path"]).resolve()
            if not target.is_relative_to(base):
                raise RecoveryError("invalid fact recovery target")
            encoded = payload["before"]
            before = (
                base64.b64decode(encoded, validate=True)
                if encoded is not None
                else None
            )
            old = hashlib.sha256(before).hexdigest() if before is not None else None
            actual = file_hash(target)
            expected = payload["after"] if committed else old
            if actual == expected:
                continue
            if committed or actual != payload["after"]:
                raise RecoveryError(f"fact recovery conflict: {payload['path']}")
            repairs.append((target, before))
        for target, before in repairs:
            if before is None:
                target.unlink()
            else:
                replace_bytes(target, before)
        shutil.rmtree(directory)
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise RecoveryError(
            "fact recovery incomplete; retained local evidence"
        ) from error
