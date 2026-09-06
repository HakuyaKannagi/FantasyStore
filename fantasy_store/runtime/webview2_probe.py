from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import importlib
import os
from typing import Callable


class WebView2ProbeStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    # Kept only for Phase-1 compatibility. Phase 7 formal probe never returns it.
    DEFERRED = "DEFERRED"


@dataclass(frozen=True, slots=True)
class WebView2ProbeResult:
    status: WebView2ProbeStatus
    detail: str
    version: str | None = None


def probe_webview2_phase1() -> WebView2ProbeResult:
    """Historical Phase-1 probe boundary retained for regression compatibility."""
    if os.name != "nt":
        return WebView2ProbeResult(
            WebView2ProbeStatus.NOT_APPLICABLE,
            "WebView2 is a Windows release-target concern; current platform is not Windows",
        )
    return WebView2ProbeResult(
        WebView2ProbeStatus.DEFERRED,
        "Definitive WebView2 detection is performed by the Phase 7 formal probe",
    )


def _default_version_getter() -> str:
    """Return the installed WebView2 runtime version through pywebview's bundled interop.

    This is deliberately imported only on Windows/Phase 7 so source-side unit tests and
    earlier phases do not gain a hard pywebview import dependency.
    """
    webview_util = importlib.import_module("webview.util")
    clr = importlib.import_module("clr")
    interop_dll_path = getattr(webview_util, "interop_dll_path")
    clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
    core = importlib.import_module("Microsoft.Web.WebView2.Core")
    environment = getattr(core, "CoreWebView2Environment")
    try:
        version = environment.GetAvailableBrowserVersionString()
    except TypeError:
        # pythonnet versions differ in whether the optional browserExecutableFolder
        # parameter can be omitted; None means the installed Evergreen Runtime.
        version = environment.GetAvailableBrowserVersionString(None)
    return "" if version is None else str(version)


def probe_webview2(
    *,
    platform_name: str | None = None,
    version_getter: Callable[[], str] | None = None,
    logger=None,
) -> WebView2ProbeResult:
    """Formal Phase-7 WebView2 availability probe.

    No registry/COM/raw exception detail is placed in the user-facing result. Technical
    callers may log the exception separately if they own the logger; this function only
    classifies the state.
    """
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return WebView2ProbeResult(
            WebView2ProbeStatus.NOT_APPLICABLE,
            "WebView2 probe is not applicable on this development platform",
        )

    getter = version_getter or _default_version_getter
    try:
        version = getter().strip()
    except FileNotFoundError:
        if logger:
            logger.warning("WebView2 Runtime not found", extra={"event_code": "WEBVIEW2_MISSING"})
        return WebView2ProbeResult(
            WebView2ProbeStatus.UNAVAILABLE,
            "Microsoft Edge WebView2 Runtime was not found",
        )
    except Exception as exc:
        # pythonnet may surface the official WebView2RuntimeNotFoundException or a
        # wrapped file-not-found style exception rather than Python FileNotFoundError.
        # Classify only by exception type/name; never leak the raw COM/HRESULT text.
        type_name = type(exc).__name__.lower()
        if "webview2runtimenotfound" in type_name or "filenotfound" in type_name:
            if logger:
                logger.warning("WebView2 Runtime not found", extra={"event_code": "WEBVIEW2_MISSING"})
            return WebView2ProbeResult(
                WebView2ProbeStatus.UNAVAILABLE,
                "Microsoft Edge WebView2 Runtime was not found",
            )
        if logger:
            logger.exception("WebView2 formal probe failed", extra={"event_code": "WEBVIEW2_PROBE_EXCEPTION"})
        return WebView2ProbeResult(
            WebView2ProbeStatus.ERROR,
            "WebView2 Runtime availability could not be determined safely",
        )

    if not version:
        if logger:
            logger.warning("WebView2 Runtime version is empty", extra={"event_code": "WEBVIEW2_MISSING"})
        return WebView2ProbeResult(
            WebView2ProbeStatus.UNAVAILABLE,
            "Microsoft Edge WebView2 Runtime was not found",
        )
    return WebView2ProbeResult(
        WebView2ProbeStatus.AVAILABLE,
        "Microsoft Edge WebView2 Runtime is available",
        version=version,
    )
