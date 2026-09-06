from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from fantasy_store.domain.errors import DomainError


TraceHook = Callable[[str], None]


class CartCheckoutCoordinator:
    """Single exclusive application lock for cart writes and checkout."""

    def __init__(self, *, timeout: float | None = None, trace: TraceHook | None = None) -> None:
        self._lock = threading.Lock()
        self.timeout = timeout
        self.trace = trace

    def _emit(self, event: str) -> None:
        if self.trace is not None:
            self.trace(event)

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        if self.timeout is None:
            acquired = self._lock.acquire()
        else:
            acquired = self._lock.acquire(timeout=max(0.0, self.timeout))
        if not acquired:
            # PACK_BUSY is already a frozen public error code; do not invent a
            # cart-specific code only for an internal lock timeout.
            raise DomainError("PACK_BUSY", "cart/checkout operation is busy")
        self._emit("cart_acquired")
        try:
            yield
        finally:
            self._emit("cart_released")
            self._lock.release()
