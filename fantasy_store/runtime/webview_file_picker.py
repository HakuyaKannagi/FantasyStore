from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, Callable


class WindowHolder:
    """Thread-safe late binding of the pywebview Window used by native adapters."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._window: Any | None = None
        self._accepting_work = True

    def bind(self, window: Any) -> None:
        with self._lock:
            if not self._accepting_work:
                raise RuntimeError("application is shutting down")
            self._window = window

    def stop_accepting_work(self) -> None:
        with self._lock:
            self._accepting_work = False

    def get(self) -> Any:
        with self._lock:
            if not self._accepting_work:
                raise RuntimeError("application is shutting down")
            if self._window is None:
                raise RuntimeError("native window is not ready")
            return self._window


class PyWebViewFilePicker:
    """Phase-7 native `.vpack` picker. Absolute paths remain Python-internal."""

    def __init__(
        self,
        window_provider: Callable[[], Any],
        *,
        webview_provider: Callable[[], Any],
    ) -> None:
        self._window_provider = window_provider
        self._webview_provider = webview_provider

    def pick_vpack(self) -> Path | None:
        window = self._window_provider()
        webview = self._webview_provider()
        file_dialog = getattr(webview, "FileDialog")
        result = window.create_file_dialog(
            file_dialog.OPEN,
            allow_multiple=False,
            file_types=("FantasyStore Pack (*.vpack)",),
        )
        if not result:
            return None
        if isinstance(result, (str, Path)):
            selected = Path(result)
        else:
            if len(result) != 1:
                raise RuntimeError("native file picker returned an unexpected selection count")
            selected = Path(result[0])
        return selected
