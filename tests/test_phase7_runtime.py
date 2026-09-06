from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fantasy_store.runtime.managed_bridge import ManagedBridgeApi
from fantasy_store.runtime.ui_dispatcher import UiDispatcher
from fantasy_store.runtime.webview2_probe import WebView2ProbeStatus, probe_webview2
from fantasy_store.runtime.webview_file_picker import PyWebViewFilePicker, WindowHolder
from fantasy_store.runtime.webview_runtime import NavigationGuard, WebViewIntegration


class EventSlot:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self
    def fire(self, *args):
        for fn in list(self.handlers):
            fn(*args)


class FakeNativeWebView:
    def __init__(self):
        self.CoreWebView2 = None
        self.CoreWebView2InitializationCompleted = EventSlot()


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(before_show=EventSlot(), loaded=EventSlot(), closing=EventSlot())
        self.native = SimpleNamespace(webview=FakeNativeWebView())
        self.dialog_result = None
        self.scripts = []
        self.url = "http://127.0.0.1:32123/index.html"
    def create_file_dialog(self, *args, **kwargs):
        return self.dialog_result
    def get_current_url(self):
        return self.url
    def run_js(self, script):
        self.scripts.append(script)


class FakeWebview:
    class FileDialog:
        OPEN = "OPEN"
    def __init__(self):
        self.settings = {"ALLOW_FILE_URLS": True, "OPEN_EXTERNAL_LINKS_IN_BROWSER": True, "OPEN_DEVTOOLS_IN_DEBUG": True, "REMOTE_DEBUGGING_PORT": 1234}
        self.renderer = "edgechromium"
        self.window = FakeWindow()
        self.created = None
        self.started = None
    def create_window(self, *args, **kwargs):
        self.created = (args, kwargs)
        return self.window
    def start(self, **kwargs):
        self.started = kwargs
        self.window.events.before_show.fire(self.window)
        self.window.events.loaded.fire(self.window)
        self.window.events.closing.fire()


def test_formal_webview2_probe_non_windows():
    result = probe_webview2(platform_name="posix", version_getter=lambda: "should-not-run")
    assert result.status == WebView2ProbeStatus.NOT_APPLICABLE


def test_formal_webview2_probe_available_unavailable_error():
    available = probe_webview2(platform_name="nt", version_getter=lambda: "123.4.5")
    assert available.status == WebView2ProbeStatus.AVAILABLE
    assert available.version == "123.4.5"
    unavailable = probe_webview2(platform_name="nt", version_getter=lambda: "")
    assert unavailable.status == WebView2ProbeStatus.UNAVAILABLE
    missing = probe_webview2(platform_name="nt", version_getter=lambda: (_ for _ in ()).throw(FileNotFoundError()))
    assert missing.status == WebView2ProbeStatus.UNAVAILABLE
    WrappedMissing = type("WebView2RuntimeNotFoundException", (Exception,), {})
    wrapped_missing = probe_webview2(platform_name="nt", version_getter=lambda: (_ for _ in ()).throw(WrappedMissing("HRESULT secret")))
    assert wrapped_missing.status == WebView2ProbeStatus.UNAVAILABLE
    assert "HRESULT secret" not in wrapped_missing.detail
    error = probe_webview2(platform_name="nt", version_getter=lambda: (_ for _ in ()).throw(RuntimeError("registry secret")))
    assert error.status == WebView2ProbeStatus.ERROR
    assert "registry secret" not in error.detail


def test_native_file_picker_single_cancel_and_shutdown(tmp_path: Path):
    webview = FakeWebview()
    holder = WindowHolder()
    holder.bind(webview.window)
    picker = PyWebViewFilePicker(holder.get, webview_provider=lambda: webview)
    webview.window.dialog_result = None
    assert picker.pick_vpack() is None
    chosen = tmp_path / "x.vpack"
    chosen.write_bytes(b"x")
    webview.window.dialog_result = (str(chosen),)
    assert picker.pick_vpack() == chosen
    holder.stop_accepting_work()
    with pytest.raises(RuntimeError):
        picker.pick_vpack()


def test_ui_dispatcher_json_serializes_and_stops_after_close():
    window = FakeWindow()
    dispatcher = UiDispatcher(lambda: window)
    assert dispatcher.notify("pack_changed", {"name": "x');alert(1)//"}) is True
    script = window.scripts[-1]
    assert "CustomEvent" in script
    assert '"name":"x\');alert(1)//"' in script
    dispatcher.close()
    assert dispatcher.notify("pack_changed", {}) is False
    with pytest.raises(ValueError):
        UiDispatcher(lambda: window).notify("bad-event;alert(1)", {})


def test_navigation_guard_exact_origin_and_blocks_external():
    guard = NavigationGuard()
    guard.set_initial_url("http://127.0.0.1:4567/index.html")
    assert guard.allowed("http://127.0.0.1:4567/js/app.js")
    assert not guard.allowed("http://127.0.0.1:9999/")
    assert not guard.allowed("https://example.com/")
    assert not guard.allowed("file:///etc/passwd")
    assert not guard.allowed("javascript:alert(1)")


def test_webview_integration_uses_fixed_bridge_local_ui_and_release_settings(tmp_path: Path):
    ui = tmp_path / "ui" / "index.html"
    ui.parent.mkdir(parents=True)
    ui.write_text("<html></html>", encoding="utf-8")
    fake_webview = FakeWebview()
    holder = WindowHolder()
    phase6 = SimpleNamespace(
        bridge=object(),
        image_resolver=object(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, exception=lambda *a, **k: None),
        ui_index=ui,
        paths=SimpleNamespace(root=tmp_path / "data"),
    )
    # Do not exercise image resolver callback on this platform-neutral fake.
    integration = WebViewIntegration(phase6, holder, webview_provider=lambda: fake_webview)
    result = integration.run()
    args, kwargs = fake_webview.created
    assert args[0] == "架空ショッピング"
    assert Path(args[1]) == ui
    assert isinstance(kwargs["js_api"], ManagedBridgeApi)
    assert kwargs["js_api"]._bridge is phase6.bridge
    assert kwargs["min_size"] == (760, 560)
    assert fake_webview.started["debug"] is False
    assert fake_webview.settings["ALLOW_FILE_URLS"] is False
    assert fake_webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is False
    assert fake_webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] is False
    assert fake_webview.settings["REMOTE_DEBUGGING_PORT"] is None
    assert result.clean_shutdown is True


def test_managed_bridge_signatures_match_bridge_api_exactly():
    import inspect
    from fantasy_store.bridge.api import BridgeApi

    names = {
        "get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart",
        "update_cart_item", "remove_cart_item", "clear_cart", "checkout", "get_order_history",
        "get_order_detail", "get_statistics", "get_packs", "import_pack", "set_pack_enabled",
    }
    for name in names:
        bridge_params = [p.name for p in inspect.signature(getattr(BridgeApi, name)).parameters.values()]
        managed_params = [p.name for p in inspect.signature(getattr(ManagedBridgeApi, name)).parameters.values()]
        assert managed_params == bridge_params, name

    class ZeroArgBridge:
        def get_categories(self): return {"ok": True, "data": {"categories": []}, "error": None}
        def get_cart(self): return {"ok": True, "data": {}, "error": None}
        def clear_cart(self): return {"ok": True, "data": {}, "error": None}
        def get_statistics(self): return {"ok": True, "data": {}, "error": None}
        def get_packs(self): return {"ok": True, "data": {}, "error": None}
        def import_pack(self): return {"ok": True, "data": {}, "error": None}

    managed = ManagedBridgeApi(ZeroArgBridge())
    for name in ("get_categories", "get_cart", "clear_cart", "get_statistics", "get_packs", "import_pack"):
        assert getattr(managed, name)()["ok"] is True


def test_managed_bridge_surface_is_exact_15_and_waits_for_inflight_call():
    import inspect
    import threading
    import time

    public = {
        name for name, value in inspect.getmembers(ManagedBridgeApi, predicate=callable)
        if not name.startswith("_")
    }
    expected = {
        "get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart",
        "update_cart_item", "remove_cart_item", "clear_cart", "checkout", "get_order_history",
        "get_order_detail", "get_statistics", "get_packs", "import_pack", "set_pack_enabled",
    }
    assert public == expected

    entered = threading.Event()
    release = threading.Event()

    class SlowBridge:
        def get_categories(self):
            entered.set()
            assert release.wait(2)
            return {"ok": True, "data": {"categories": []}, "error": None}

    managed = ManagedBridgeApi(SlowBridge())
    result = []
    worker = threading.Thread(target=lambda: result.append(managed.get_categories()))
    worker.start()
    assert entered.wait(1)
    managed._begin_shutdown()
    rejected = managed.get_categories()
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INTERNAL_ERROR"

    waited = threading.Event()
    waiter = threading.Thread(target=lambda: (managed._wait_for_idle(), waited.set()))
    waiter.start()
    time.sleep(0.03)
    assert not waited.is_set()
    release.set()
    worker.join(2)
    waiter.join(2)
    assert waited.is_set()
    assert result[0]["ok"] is True
