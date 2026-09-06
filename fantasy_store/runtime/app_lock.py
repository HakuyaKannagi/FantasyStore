from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from fantasy_store.domain.errors import AlreadyRunningError


class AppInstanceLock:
    """OS-backed non-blocking lock for the frozen single-process model."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None
        self._locked = False

    @property
    def is_locked(self) -> bool:
        return self._locked

    def acquire(self) -> "AppInstanceLock":
        if self._locked:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_obj = self.path.open("a+b")
        try:
            file_obj.seek(0, os.SEEK_END)
            if file_obj.tell() == 0:
                file_obj.write(b"\0")
                file_obj.flush()
            file_obj.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise AlreadyRunningError() from exc
            else:
                import fcntl
                try:
                    fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise AlreadyRunningError() from exc
        except Exception:
            file_obj.close()
            raise
        self._file = file_obj
        self._locked = True
        return self

    def release(self) -> None:
        if not self._locked or self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self._locked = False

    def __enter__(self) -> "AppInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
