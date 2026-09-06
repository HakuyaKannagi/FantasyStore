from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fantasy_store.bootstrap import StoreManagerRecoveryRuntime, bootstrap_phase7
from fantasy_store.domain.errors import DatabaseRecoveryError, PackRecoveryRequiredError, PackStartupSafetyError
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackJournalStore
from fantasy_store.runtime.paths import AppPaths
from fantasy_store.runtime.resource_locator import ResourceLocator
from tests.phase4_helpers import make_vpack, setup_phase4
from fantasy_store.pack.updater import PackLifecycleManager


def _locator() -> ResourceLocator:
    return ResourceLocator(Path(__file__).resolve().parents[1])


def _no_window_provider():
    raise AssertionError("pywebview must not be imported until run_window")


def _completed_history(tmp_path: Path, *, versions=("1.0", "1.1"), pack_id="demo.pack"):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    for index, version in enumerate(versions, start=1):
        manager.import_vpack(make_vpack(tmp_path, version=version, item_id=f"item-{version}", pack_id=pack_id))
    store = PackJournalStore(paths)
    journals = [store.load(p) for p in store.list_final_journals()]
    by_new = {j.new_version: j for j in journals}
    for index, version in enumerate(versions, start=1):
        store.update(by_new[version], started_at=f"2026-09-05T00:00:{index:02d}.000Z")
    return paths, repo, store


def test_manifest_consistency_failure_normal_boot_fails_closed_but_store_manager_is_restricted(tmp_path):
    paths, _, _ = _completed_history(tmp_path, versions=("1.0",))
    conn = sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET install_dir=? WHERE pack_id='demo.pack'", ("packs/installed/not-demo",))
    conn.commit(); conn.close()

    with pytest.raises(PackStartupSafetyError) as normal:
        bootstrap_phase7(paths.root, resource_locator=_locator(), platform_name="posix")
    assert normal.value.diagnostic.category == "PACK_INSTALLED_DB_MISMATCH"

    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        assert isinstance(runtime, StoreManagerRecoveryRuntime)
        assert runtime.recovery_mode is True
        assert runtime.recovery_decisions == ()
        assert runtime.safety_failures[0].category == "PACK_INSTALLED_DB_MISMATCH"
        html = runtime.recovery_integration.html
        assert "店長モード（復旧）" in html
        assert "PACK_INSTALLED_DB_MISMATCH" in html
        assert "demo.pack" in html
        assert "店舗機能は安全のため停止" in html
        assert "import_pack" not in html and "checkout" not in html
        assert str(paths.root) not in html
    finally:
        runtime.close()


def test_legacy_version_after_healthy_lineage_enters_restricted_manager_without_mutation(tmp_path):
    paths, _, store = _completed_history(tmp_path, versions=("1.0", "2.0"), pack_id="rc.demo.pack")
    journals = [store.load(p) for p in store.list_final_journals()]
    old = next(j for j in journals if j.new_version == "1.0")
    latest = next(j for j in journals if j.new_version == "2.0")
    assert old.new_digest == latest.old_digest

    installed = paths.installed_packs / "rc.demo.pack"
    pack_json = installed / "pack.json"
    payload = json.loads(pack_json.read_text(encoding="utf-8"))
    payload["version"] = "2"  # legacy Windows UAT value; intentionally invalid now
    pack_json.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    legacy_digest = compute_content_digest(installed)

    conn = sqlite3.connect(paths.pack_db)
    conn.execute(
        "UPDATE installed_packs SET pack_version=?, content_digest=? WHERE pack_id=?",
        ("2", legacy_digest, "rc.demo.pack"),
    )
    conn.commit(); conn.close()
    store.update(latest, new_version="2", new_digest=legacy_digest)

    # Recovery lineage remains healthy: old generation is superseded and the
    # current filesystem/DB digest matches the latest journal generation.
    from fantasy_store.persistence.pack_repository import PackRepository
    from fantasy_store.pack.recovery import PackRecoveryManager
    decisions = PackRecoveryManager(paths, repository=PackRepository(paths.pack_db), retry_delay=0, sleep=lambda _: None).recover_all()
    by_id = {d.operation_id: d for d in decisions}
    assert by_id[old.operation_id].decision == "historical_superseded"
    assert all(d.final_state != "RECOVERY_REQUIRED" for d in decisions)

    with pytest.raises(PackStartupSafetyError) as normal:
        bootstrap_phase7(paths.root, resource_locator=_locator(), platform_name="posix")
    assert normal.value.diagnostic.category == "PACK_VERSION_SCHEMA_INCOMPATIBLE"
    assert normal.value.diagnostic.pack_version == "2"

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
        assert diagnostic.category == "PACK_VERSION_SCHEMA_INCOMPATIBLE"
        assert diagnostic.pack_id == "rc.demo.pack"
        assert diagnostic.pack_version == "2"
        html = runtime.recovery_integration.html
        assert "PACK_VERSION_SCHEMA_INCOMPATIBLE" in html
        assert "rc.demo.pack" in html
        assert ">2<" in html
        assert "MAJOR.MINOR" in html
        assert "PackValidationError" not in html
        assert str(paths.root) not in html
    finally:
        runtime.close()

    # No automatic legacy migration or destructive correction is permitted.
    conn = sqlite3.connect(paths.pack_db)
    value = conn.execute("SELECT pack_version FROM installed_packs WHERE pack_id='rc.demo.pack'").fetchone()[0]
    conn.close()
    assert value == "2"
    assert json.loads(pack_json.read_text(encoding="utf-8"))["version"] == "2"


def test_store_manager_does_not_absorb_unrecoverable_user_database_failure(tmp_path):
    paths = AppPaths(tmp_path / "user-db-fatal")
    paths.create_bootstrap_minimum()
    paths.create_application_dirs()
    paths.user_db.write_bytes(b"not a sqlite database")

    with pytest.raises(DatabaseRecoveryError):
        bootstrap_phase7(
            paths.root,
            resource_locator=_locator(),
            platform_name="posix",
            webview_provider=_no_window_provider,
            store_manager=True,
        )


def test_healthy_store_manager_still_uses_normal_manager_runtime(tmp_path):
    runtime = bootstrap_phase7(
        tmp_path / "healthy-manager",
        resource_locator=_locator(),
        platform_name="posix",
        webview_provider=_no_window_provider,
        store_manager=True,
    )
    try:
        assert not isinstance(runtime, StoreManagerRecoveryRuntime)
        assert runtime.webview_integration.ui_index.name == "index-store-manager.html"
    finally:
        runtime.close()
