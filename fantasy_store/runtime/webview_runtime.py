from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from urllib.parse import urlsplit

from fantasy_store.runtime.managed_bridge import ManagedBridgeApi, ManagedStoreManagerBridgeApi
from fantasy_store.runtime.ui_dispatcher import UiDispatcher
from fantasy_store.runtime.webview_file_picker import WindowHolder
from fantasy_store.runtime.webview_image_handler import ImageResourceLoader, WindowsWebView2ImageHandler

APP_TITLE = "架空ショッピング"
WINDOW_SIZE = (1180, 780)
WINDOW_MIN_SIZE = (760, 560)


@dataclass(slots=True)
class WebViewRuntimeResult:
    renderer: str | None
    clean_shutdown: bool


class NavigationGuard:
    """Restrict top-level navigation/new windows to the exact local UI origin."""

    def __init__(self, *, logger=None) -> None:
        self.logger = logger
        self.allowed_origin: tuple[str, str, int | None] | None = None
        self._core = None

    def set_initial_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("pywebview UI was not served from a trusted local origin")
        self.allowed_origin = (parts.scheme, parts.hostname, parts.port)

    def allowed(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            if self.allowed_origin is None:
                return False
            return (parts.scheme, parts.hostname, parts.port) == self.allowed_origin
        except Exception:
            return False

    def attach_core(self, core: Any) -> None:
        if self._core is core:
            return
        core.NavigationStarting += self._on_navigation
        core.NewWindowRequested += self._on_new_window
        self._core = core

    def _on_navigation(self, sender: Any, args: Any) -> None:
        uri = str(args.Uri)
        if self.allowed_origin is None:
            # First navigation is created by pywebview's local HTTP server. Establish
            # the exact loopback origin here; later navigations must match it.
            parts = urlsplit(uri)
            if parts.scheme in {"http", "https"} and parts.hostname in {"127.0.0.1", "localhost"}:
                self.allowed_origin = (parts.scheme, parts.hostname, parts.port)
                return
        if not self.allowed(uri):
            if hasattr(args, "set_Cancel"):
                args.set_Cancel(True)
            else:
                args.Cancel = True
            if self.logger:
                self.logger.warning("blocked external/unauthorized navigation", extra={"event_code": "WEBVIEW_NAV_BLOCKED"})

    def _on_new_window(self, sender: Any, args: Any) -> None:
        if hasattr(args, "set_Handled"):
            args.set_Handled(True)
        else:
            args.Handled = True
        if self.logger:
            self.logger.warning("blocked new-window navigation", extra={"event_code": "WEBVIEW_NEW_WINDOW_BLOCKED"})


class WebViewIntegration:
    def __init__(
        self,
        phase6_runtime: Any,
        window_holder: WindowHolder,
        *,
        webview_provider: Callable[[], Any] | None = None,
        ui_index: Path | None = None,
    ) -> None:
        self.phase6 = phase6_runtime
        self.window_holder = window_holder
        self._webview_provider = webview_provider or (lambda: importlib.import_module("webview"))
        self.logger = phase6_runtime.logger
        self._lock = RLock()
        self._closing = False
        self._window = None
        self.ui_index = Path(ui_index or phase6_runtime.ui_index).resolve()
        self.dispatcher = UiDispatcher(window_holder.get)
        self.managed_bridge = (
            ManagedStoreManagerBridgeApi(phase6_runtime.bridge)
            if hasattr(phase6_runtime.bridge, "uninstall_pack")
            else ManagedBridgeApi(phase6_runtime.bridge)
        )
        self.image_handler = WindowsWebView2ImageHandler(
            ImageResourceLoader(phase6_runtime.image_resolver),
            logger=self.logger,
        )
        self.navigation = NavigationGuard(logger=self.logger)

    @property
    def window(self) -> Any | None:
        return self._window

    def _before_show(self, window: Any) -> None:
        self.window_holder.bind(window)
        native = getattr(window, "native", None)
        native_webview = getattr(native, "webview", None)
        if native_webview is None:
            raise RuntimeError("native WebView2 control is unavailable")

        # Attach before CoreWebView2 initialization completes so image requests from the
        # first page load are intercepted without exposing file:// paths.
        self.image_handler.attach(native_webview)

        core = getattr(native_webview, "CoreWebView2", None)
        if core is not None:
            self.navigation.attach_core(core)
        else:
            def attach_after_init(sender, args):
                if not hasattr(args, "IsSuccess") or bool(args.IsSuccess):
                    core2 = getattr(sender, "CoreWebView2", None)
                    if core2 is not None:
                        self.navigation.attach_core(core2)
            native_webview.CoreWebView2InitializationCompleted += attach_after_init

    def _loaded(self, window: Any) -> None:
        current = window.get_current_url()
        if current:
            self.navigation.set_initial_url(str(current))
        self.logger.info("pywebview UI loaded", extra={"event_code": "WEBVIEW_UI_LOADED"})

    def _closing_event(self, *args) -> None:
        with self._lock:
            self._closing = True
        self.managed_bridge._begin_shutdown()
        self.window_holder.stop_accepting_work()
        self.dispatcher.close()
        self.logger.info("window closing; new UI work disabled", extra={"event_code": "WEBVIEW_CLOSING"})

    def run(self) -> WebViewRuntimeResult:
        webview = self._webview_provider()
        # Release configuration: no file URL access, external-browser escape, or devtools/debug endpoints.
        settings = getattr(webview, "settings", None)
        if settings is not None:
            try:
                settings["ALLOW_FILE_URLS"] = False
            except Exception:
                pass
            try:
                settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
            except Exception:
                pass
            try:
                settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
            except Exception:
                pass
            try:
                settings["REMOTE_DEBUGGING_PORT"] = None
            except Exception:
                pass

        ui_path = self.ui_index
        if not ui_path.is_file():
            raise RuntimeError("UI resource is missing")
        window = webview.create_window(
            APP_TITLE,
            str(ui_path),
            js_api=self.managed_bridge,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            resizable=True,
            text_select=True,
        )
        if window is None:
            raise RuntimeError("pywebview did not create a window")
        self._window = window
        window.events.before_show += self._before_show
        window.events.loaded += self._loaded
        window.events.closing += self._closing_event
        # Force the Windows release renderer. Local file paths are served by pywebview's
        # built-in localhost server; no external network is required.
        webview.start(
            gui="edgechromium" if os.name == "nt" else None,
            debug=False,
            private_mode=True,
            storage_path=str(self.phase6.paths.root / "webview"),
        )
        # Existing pywebview API workers may still be finishing after the Window closes.
        # Keep DB/runtime resources and AppInstanceLock alive until they are done.
        self.managed_bridge._begin_shutdown()
        self.managed_bridge._wait_for_idle()
        return WebViewRuntimeResult(renderer=getattr(webview, "renderer", None), clean_shutdown=True)
