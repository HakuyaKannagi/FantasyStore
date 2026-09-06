from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from fantasy_store.domain.errors import PackBusyError, PackRecoveryRequiredError


@dataclass
class _PackLockState:
    condition: threading.Condition
    readers: int = 0
    writer: bool = False
    waiting_writers: int = 0


class PackAccessCoordinator:
    """Per-Pack writer-preference read/write locks for one application process."""

    def __init__(self) -> None:
        self._states_lock = threading.Lock()
        self._states: dict[str, _PackLockState] = {}
        self._blocked: set[str] = set()

    def _state(self, pack_id: str) -> _PackLockState:
        with self._states_lock:
            state = self._states.get(pack_id)
            if state is None:
                state = _PackLockState(threading.Condition(threading.Lock()))
                self._states[pack_id] = state
            return state

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def acquire_read(self, pack_id: str, timeout: float | None = None) -> None:
        with self._states_lock:
            if pack_id in self._blocked:
                raise PackRecoveryRequiredError(pack_id, "pack is blocked pending recovery")
        state = self._state(pack_id)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with state.condition:
            while state.writer or state.waiting_writers > 0:
                with self._states_lock:
                    if pack_id in self._blocked:
                        raise PackRecoveryRequiredError(pack_id, "pack is blocked pending recovery")
                remaining = self._remaining(deadline)
                if remaining is not None and remaining <= 0:
                    raise PackBusyError(pack_id)
                state.condition.wait(remaining)
            state.readers += 1

    def release_read(self, pack_id: str) -> None:
        state = self._state(pack_id)
        with state.condition:
            if state.readers <= 0:
                raise RuntimeError("read lock release without acquisition")
            state.readers -= 1
            if state.readers == 0:
                state.condition.notify_all()

    def acquire_write(self, pack_id: str, timeout: float | None = None) -> None:
        with self._states_lock:
            if pack_id in self._blocked:
                raise PackRecoveryRequiredError(pack_id, "pack is blocked pending recovery")
        state = self._state(pack_id)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with state.condition:
            state.waiting_writers += 1
            try:
                while state.writer or state.readers > 0:
                    with self._states_lock:
                        if pack_id in self._blocked:
                            raise PackRecoveryRequiredError(pack_id, "pack is blocked pending recovery")
                    remaining = self._remaining(deadline)
                    if remaining is not None and remaining <= 0:
                        raise PackBusyError(pack_id)
                    state.condition.wait(remaining)
                state.writer = True
            finally:
                state.waiting_writers -= 1

    def release_write(self, pack_id: str) -> None:
        state = self._state(pack_id)
        with state.condition:
            if not state.writer:
                raise RuntimeError("write lock release without acquisition")
            state.writer = False
            state.condition.notify_all()

    @contextmanager
    def read_lock(self, pack_id: str, timeout: float | None = None) -> Iterator[None]:
        self.acquire_read(pack_id, timeout)
        try:
            yield
        finally:
            self.release_read(pack_id)

    @contextmanager
    def write_lock(self, pack_id: str, timeout: float | None = None) -> Iterator[None]:
        self.acquire_write(pack_id, timeout)
        try:
            yield
        finally:
            self.release_write(pack_id)


    def mark_recovery_required(self, pack_id: str) -> None:
        state = self._state(pack_id)
        with self._states_lock:
            self._blocked.add(pack_id)
        with state.condition:
            state.condition.notify_all()

    def clear_recovery_required(self, pack_id: str) -> None:
        with self._states_lock:
            self._blocked.discard(pack_id)

    def is_recovery_required(self, pack_id: str) -> bool:
        with self._states_lock:
            return pack_id in self._blocked

    def is_write_locked(self, pack_id: str) -> bool:
        state = self._state(pack_id)
        with state.condition:
            return state.writer
