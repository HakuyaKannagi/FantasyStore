from __future__ import annotations

import ctypes
import os
import sys

from fantasy_store.bootstrap import bootstrap_phase7
from fantasy_store.bridge.response import safe_message_for
from fantasy_store.domain.errors import AlreadyRunningError, DomainError
from fantasy_store.runtime.webview2_probe import WebView2ProbeStatus

_APP_TITLE = "架空ショッピング"
_DOUBLE_START_MESSAGE = "二重起動だよ★終了するね！"
_WEBVIEW2_MISSING_MESSAGE = (
    "Microsoft Edge WebView2 Runtimeが必要です。\n"
    "WebView2 Runtimeを導入してから、もう一度起動してください。"
)
_WEBVIEW2_ERROR_MESSAGE = (
    "WebView2 Runtimeの状態を確認できませんでした。\n"
    "安全のためアプリを起動しません。\nログ: %LOCALAPPDATA%\\FantasyStore\\logs"
)
_UNEXPECTED_MESSAGE = (
    "起動中に予期しない問題が発生しました。安全のためアプリを終了します。\n"
    "ログ: %LOCALAPPDATA%\\FantasyStore\\logs"
)


def _native_message(message: str, title: str = _APP_TITLE) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # type: ignore[attr-defined]
    else:
        print(message, file=sys.stderr)


def _domain_fatal_message(exc: DomainError) -> str:
    safe = safe_message_for(exc.code)
    if exc.code == "RECOVERY_REQUIRED":
        return (
            f"{safe}\n\n安全のため通常店舗は起動しません。"
            "\n状態確認には店長モードを使用してください。"
            "\nFantasyStore.exe --store-manager"
            "\nログ: %LOCALAPPDATA%\\FantasyStore\\logs"
        )
    return f"{safe}\n\n安全のため通常画面は起動しません。\nログ: %LOCALAPPDATA%\\FantasyStore\\logs"


def _store_manager_requested(argv: list[str] | None = None) -> bool:
    args = sys.argv[1:] if argv is None else argv
    return "--store-manager" in args


def main() -> int:
    runtime = None
    try:
        runtime = bootstrap_phase7(store_manager=_store_manager_requested())
        probe = runtime.webview2_probe
        if os.name == "nt":
            if probe.status == WebView2ProbeStatus.UNAVAILABLE:
                runtime.logger.error("WebView2 Runtime missing", extra={"event_code": "WEBVIEW2_MISSING"})
                _native_message(_WEBVIEW2_MISSING_MESSAGE)
                return 3
            if probe.status != WebView2ProbeStatus.AVAILABLE:
                runtime.logger.error("WebView2 Runtime probe failed", extra={"event_code": "WEBVIEW2_PROBE_FAILED"})
                _native_message(_WEBVIEW2_ERROR_MESSAGE)
                return 4
        elif probe.status != WebView2ProbeStatus.NOT_APPLICABLE:
            runtime.logger.error("unexpected non-Windows WebView2 probe state", extra={"event_code": "WEBVIEW2_PROBE_FAILED"})
            return 4

        runtime.logger.info("starting pywebview runtime", extra={"event_code": "WEBVIEW_START"})
        runtime.run_window()
        return 0
    except AlreadyRunningError:
        _native_message(_DOUBLE_START_MESSAGE)
        return 2
    except DomainError as exc:
        if runtime is not None:
            runtime.logger.error("safe-stop domain failure: %s", exc.code, extra={"event_code": "RUNTIME_SAFE_STOP"})
        _native_message(_domain_fatal_message(exc))
        return 1
    except Exception:
        if runtime is not None:
            runtime.logger.exception("unrecoverable startup/runtime error", extra={"event_code": "RUNTIME_FATAL"})
        _native_message(_UNEXPECTED_MESSAGE)
        return 1
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
