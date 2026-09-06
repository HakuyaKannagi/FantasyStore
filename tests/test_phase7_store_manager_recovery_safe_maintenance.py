from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fantasy_store.bootstrap import StoreManagerRecoveryRuntime, bootstrap_phase7
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackJournalStore
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.persistence.pack_repository import PackRepository
from fantasy_store.runtime.resource_locator import ResourceLocator
from tests.phase4_helpers import make_vpack, setup_phase4
from tests.phase5_helpers import item, setup_phase5


def _locator() -> ResourceLocator:
    return ResourceLocator(Path(__file__).resolve().parents[1])


def _no_window_provider():
    raise AssertionError("pywebview must not be imported until run_window")


def _make_legacy_pack(tmp_path: Path, *, enabled: bool = True, pack_id: str = "rc.demo.pack"):
    paths, repo = setup_phase4(tmp_path, with_user_db=True)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, pack_id=pack_id, version="2.0", item_id="legacy-item"))
    if not enabled:
        manager.set_pack_enabled(pack_id, False)

    installed = paths.installed_packs / pack_id
    pack_json = installed / "pack.json"
    payload = json.loads(pack_json.read_text(encoding="utf-8"))
    payload["version"] = "2"
    pack_json.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    legacy_digest = compute_content_digest(installed)
    with repo.transaction() as conn:
        conn.execute(
            "UPDATE installed_packs SET pack_version=?, content_digest=? WHERE pack_id=?",
            ("2", legacy_digest, pack_id),
        )
    store = PackJournalStore(paths)
    journal = next(j for j in (store.load(p) for p in store.list_final_journals()) if j.pack_id == pack_id)
    store.update(journal, new_version="2", new_digest=legacy_digest)
    return paths, repo


def test_recovery_html_has_fixed_readable_colors_hashed_csp_and_wrapping(tmp_path):
    paths, _ = _make_legacy_pack(tmp_path)
    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        html = runtime.recovery_integration.html
        assert "background:#f4f6f8" in html
        assert "color:#20242a" in html
        assert "table{width:100%" in html
        assert "overflow-wrap:anywhere" in html
        assert "word-break:break-word" in html
        assert "style-src 'sha256-" in html
        assert "script-src 'sha256-" in html
        assert "unsafe-inline" not in html
        assert "unsafe-eval" not in html
        assert "http://" not in html and "https://" not in html
        assert "script-src 'none'" not in html
    finally:
        runtime.close()


def test_enabled_legacy_pack_allows_disable_but_not_uninstall_until_disabled(tmp_path):
    paths, repo = _make_legacy_pack(tmp_path, enabled=True)
    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        assert isinstance(runtime, StoreManagerRecoveryRuntime)
        diagnostic = runtime.safety_failures[0]
        assert diagnostic.safe_maintenance_allowed is True
        status = runtime.maintenance.assess("rc.demo.pack")
        assert status.safety_level == "SAFE_MAINTENANCE"
        assert status.can_disable is True
        assert status.can_uninstall is False
        assert "Packを無効化" in runtime.recovery_integration.html
        assert 'data-action="uninstall"' in runtime.recovery_integration.html

        rejected = runtime.maintenance_api.uninstall_pack("rc.demo.pack")
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "RECOVERY_REQUIRED"

        result = runtime.maintenance_api.disable_pack("rc.demo.pack")
        assert result["ok"] is True
        assert result["data"]["status"] == "DISABLED"
        row = repo.get_installed_pack("rc.demo.pack")
        assert row is not None and row.is_enabled is False
        after = runtime.maintenance.assess("rc.demo.pack")
        assert after.can_disable is False
        assert after.can_uninstall is True
    finally:
        runtime.close()


def test_disabled_legacy_pack_can_formally_uninstall_and_history_is_retained(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1", name="Purchased")])])
    env.cart.add_to_cart("pack.one", "i1", 1)
    order = env.checkout.checkout("33333333-3333-4333-8333-333333333333").order
    env.pack_service.set_pack_enabled("pack.one", False)

    installed = env.paths.installed_packs / "pack.one"
    pack_json = installed / "pack.json"
    payload = json.loads(pack_json.read_text(encoding="utf-8"))
    payload["version"] = "1"
    pack_json.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    digest = compute_content_digest(installed)
    with env.packs.transaction() as conn:
        conn.execute("UPDATE installed_packs SET pack_version='1', content_digest=? WHERE pack_id='pack.one'", (digest,))
    store = PackJournalStore(env.paths)
    journal = next(j for j in (store.load(p) for p in store.list_final_journals()) if j.pack_id == "pack.one")
    store.update(journal, new_version="1", new_digest=digest)

    runtime = bootstrap_phase7(
        env.paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        status = runtime.maintenance.assess("pack.one")
        assert status.can_uninstall is True
        result = runtime.maintenance_api.uninstall_pack("pack.one")
        assert result["ok"] is True
        assert result["data"]["status"] == "UNINSTALLED"
        assert result["data"]["maintenance"]["restart_required"] is True
        assert env.packs.get_installed_pack("pack.one") is None
        assert not installed.exists()
        # user_data/order snapshot authority is outside the Pack uninstall transaction.
        assert env.history.get_order_detail(order.order_id).lines[0].name == "Purchased"
        assert env.stats.get_statistics().order_count == 1
    finally:
        runtime.close()


def test_db_filesystem_mismatch_remains_diagnostic_only_and_write_is_rejected(tmp_path):
    paths, _, = _make_legacy_pack(tmp_path, enabled=False)
    conn = sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET install_dir=? WHERE pack_id='rc.demo.pack'", ("packs/installed/not-rc-demo",))
    conn.commit(); conn.close()

    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        assert runtime.safety_failures[0].category == "PACK_INSTALLED_DB_MISMATCH"
        status = runtime.maintenance.assess("rc.demo.pack")
        assert status.safety_level == "DIAGNOSTIC_ONLY"
        assert status.can_disable is False and status.can_uninstall is False
        assert runtime.maintenance_api.disable_pack("rc.demo.pack")["ok"] is False
        assert runtime.maintenance_api.uninstall_pack("rc.demo.pack")["ok"] is False
        assert "状態確認のみ" in runtime.recovery_integration.html
    finally:
        runtime.close()


def test_maintenance_rechecks_digest_and_stops_if_state_changes_after_startup(tmp_path):
    paths, _ = _make_legacy_pack(tmp_path, enabled=False)
    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        assert runtime.maintenance.assess("rc.demo.pack").can_uninstall is True
        # External/tampered change after startup: gating must not rely on category only.
        installed = paths.installed_packs / "rc.demo.pack"
        (installed / "items.json").write_text('{"schema_version":1,"items":[]}', encoding="utf-8")
        status = runtime.maintenance.assess("rc.demo.pack")
        assert status.safety_level == "DIAGNOSTIC_ONLY"
        response = runtime.maintenance_api.uninstall_pack("rc.demo.pack")
        assert response["ok"] is False
        assert PackRepository(paths.pack_db).get_installed_pack("rc.demo.pack") is not None
    finally:
        runtime.close()


def test_recovery_bridge_surface_is_minimal_and_does_not_expose_store_actions(tmp_path):
    paths, _ = _make_legacy_pack(tmp_path, enabled=True)
    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        public_callables = {
            name
            for name in dir(runtime.maintenance_api)
            if not name.startswith("_") and callable(getattr(runtime.maintenance_api, name))
        }
        assert public_callables == {"disable_pack", "uninstall_pack"}
        for forbidden in ("import_pack", "checkout", "add_to_cart", "set_pack_enabled", "get_products"):
            assert not hasattr(runtime.maintenance_api, forbidden)
    finally:
        runtime.close()
