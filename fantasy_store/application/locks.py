from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterable, Iterator

from fantasy_store.pack.access_coordinator import PackAccessCoordinator

TraceHook = Callable[[str], None]


@contextmanager
def multi_pack_read_locks(
    coordinator: PackAccessCoordinator,
    pack_ids: Iterable[str],
    *,
    timeout: float | None,
    trace: TraceHook | None = None,
) -> Iterator[tuple[str, ...]]:
    ordered = tuple(sorted(set(pack_ids)))
    acquired: list[str] = []
    try:
        for pack_id in ordered:
            coordinator.acquire_read(pack_id, timeout)
            acquired.append(pack_id)
            if trace is not None:
                trace(f"pack_read_acquired:{pack_id}")
        yield ordered
    finally:
        for pack_id in reversed(acquired):
            coordinator.release_read(pack_id)
            if trace is not None:
                trace(f"pack_read_released:{pack_id}")
