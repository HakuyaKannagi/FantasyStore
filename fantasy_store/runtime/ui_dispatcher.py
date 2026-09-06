from __future__ import annotations

import json
from threading import RLock
from typing import Any, Callable


class UiDispatcher:
    """Narrow worker -> UI notification adapter with close-aware delivery.

    Phase 7's ordinary Bridge calls resolve their own JS promises and do not need this
    dispatcher. It exists for runtime-originated notifications without allowing workers
    to concatenate untrusted strings into JavaScript source.
    """

    def __init__(self, window_provider: Callable[[], Any]) -> None:
        self._window_provider = window_provider
        self._lock = RLock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def notify(self, event_name: str, payload: Any) -> bool:
        if not isinstance(event_name, str) or not event_name or not event_name.replace("_", "").isalnum():
            raise ValueError("unsafe UI event name")
        with self._lock:
            if self._closed:
                return False
        window = self._window_provider()
        envelope = json.dumps({"event": event_name, "payload": payload}, ensure_ascii=True, separators=(",", ":"))
        # The payload is JSON data, not interpolated into a quoted JS literal.
        script = (
            "globalThis.dispatchEvent(new CustomEvent('fantasy-store-runtime',"
            f"{{detail:{envelope}}}));"
        )
        try:
            # run_js avoids evaluate_js' eval requirement and therefore does not require
            # CSP unsafe-eval. pywebview marshals the call to the GUI implementation.
            window.run_js(script)
            return True
        except Exception:
            with self._lock:
                if self._closed:
                    return False
            raise
