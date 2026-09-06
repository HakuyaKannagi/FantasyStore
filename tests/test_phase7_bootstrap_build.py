from __future__ import annotations

import inspect
import sys
from pathlib import Path

from fantasy_store.bootstrap import bootstrap_phase7
from fantasy_store.bridge.api import BridgeApi
from fantasy_store.runtime.resource_locator import ResourceLocator
from fantasy_store.runtime.webview2_probe import WebView2ProbeStatus


PUBLIC_API = {
    "get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart",
    "update_cart_item", "remove_cart_item", "clear_cart", "checkout", "get_order_history",
    "get_order_detail", "get_statistics", "get_packs", "import_pack", "set_pack_enabled",
}


def test_bridge_surface_is_exactly_fixed_15_public_callables():
    public = {
        name for name, value in inspect.getmembers(BridgeApi, predicate=callable)
        if not name.startswith("_")
    }
    assert public == PUBLIC_API


def test_phase7_bootstrap_non_windows_does_not_require_importing_pywebview(tmp_path: Path):
    runtime = bootstrap_phase7(
        tmp_path / "data",
        resource_locator=ResourceLocator(Path(__file__).resolve().parents[1]),
        platform_name="posix",
        webview_provider=lambda: (_ for _ in ()).throw(AssertionError("must not load before run_window")),
    )
    try:
        assert runtime.webview2_probe.status == WebView2ProbeStatus.NOT_APPLICABLE
        assert runtime.phase6.ui_index.is_file()
    finally:
        runtime.close()


def test_resource_locator_frozen_meipass(monkeypatch, tmp_path: Path):
    resource = tmp_path / "ui" / "index.html"
    resource.parent.mkdir(parents=True)
    resource.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    locator = ResourceLocator()
    assert locator.resolve("ui/index.html") == resource.resolve()


def test_build_files_are_onedir_clean_and_bundle_only_required_resources():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "build" / "fantasy_store.spec").read_text(encoding="utf-8")
    ps1 = (root / "build" / "build_windows.ps1").read_text(encoding="utf-8")
    verify = (root / "build" / "verify_dist.ps1").read_text(encoding="utf-8")
    assert "COLLECT(" in spec
    assert "onefile" not in spec.casefold()
    assert "ui" in spec and "resources" in spec
    assert "console=False" in spec
    assert "Remove-Item" in ps1 and "dist" in ps1 and "build" in ps1
    assert "requirements.txt" in ps1 and "requirements-dev.txt" in ps1 and "requirements-build.txt" in ps1
    assert "Win32NT" in ps1 and "$IsWindows" not in ps1
    assert "sys.version_info >= (3, 11)" in ps1
    assert "Get-Command node" in ps1
    assert ps1.count("Assert-NativeSuccess") >= 5
    assert "FantasyStore.exe" in verify
    assert "tests" not in spec


def test_phase7_csp_is_strict_and_offline_only():
    root = Path(__file__).resolve().parents[1]
    html = (root / "ui" / "index.html").read_text(encoding="utf-8")
    assert "default-src 'self'" in html
    assert "script-src 'self'" in html
    assert "style-src 'self'" in html
    assert "img-src 'self' fantasy-image:" in html
    assert "object-src 'none'" in html
    assert "frame-src 'none'" in html
    assert "base-uri 'none'" in html
    assert "form-action 'none'" in html
    assert "connect-src 'none'" in html
    assert "unsafe-eval" not in html
    assert "unsafe-inline" not in html
    assert "http://" not in html and "https://" not in html


def test_spec_uses_repository_root_and_pyinstaller6_internal_layout():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "build" / "fantasy_store.spec").read_text(encoding="utf-8")
    verify = (root / "build" / "verify_dist.ps1").read_text(encoding="utf-8")
    assert "Path(SPECPATH).parent.resolve()" in spec
    assert "Path(SPECPATH).parent.parent.resolve()" not in spec
    assert 'contents_directory="_internal"' in spec
    assert "_internal/ui/index.html" in verify
    assert "_internal/ui/index-store-manager.html" in verify
    assert "_internal/ui/js/store-manager-bridge-client.js" in verify
    assert "_internal/resources/schema/pack-v1.json" in verify


def test_phase7_store_manager_selects_manager_ui_resource(tmp_path: Path):
    locator = ResourceLocator(Path(__file__).resolve().parents[1])
    normal = bootstrap_phase7(
        tmp_path / "normal", resource_locator=locator, platform_name="posix",
        webview_provider=lambda: (_ for _ in ()).throw(AssertionError("must not load before run_window")),
        store_manager=False,
    )
    admin = bootstrap_phase7(
        tmp_path / "admin", resource_locator=locator, platform_name="posix",
        webview_provider=lambda: (_ for _ in ()).throw(AssertionError("must not load before run_window")),
        store_manager=True,
    )
    try:
        assert normal.webview_integration.ui_index.name == "index.html"
        assert admin.webview_integration.ui_index.name == "index-store-manager.html"
    finally:
        normal.close(); admin.close()


def test_store_manager_cli_flag_is_python_startup_decision():
    from fantasy_store.main import _store_manager_requested
    assert _store_manager_requested([]) is False
    assert _store_manager_requested(["--store-manager"]) is True
    assert _store_manager_requested(["--other"]) is False
