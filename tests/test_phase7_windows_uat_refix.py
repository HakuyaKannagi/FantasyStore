from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fantasy_store.bootstrap import StoreManagerRecoveryRuntime, bootstrap_phase4, bootstrap_phase7
from fantasy_store.domain.errors import PackRecoveryRequiredError
from fantasy_store.pack.journal import PackJournalStore, PackOperationState
from fantasy_store.pack.recovery import PackRecoveryManager
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.runtime.resource_locator import ResourceLocator
from tests.phase4_helpers import make_vpack, setup_phase4


def _completed_history(tmp_path: Path, *, versions=("1.0", "1.1"), pack_id="demo.pack"):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    for index, version in enumerate(versions, start=1):
        manager.import_vpack(make_vpack(tmp_path, version=version, item_id=f"item-{version}", pack_id=pack_id))
    store = PackJournalStore(paths)
    journals = [store.load(p) for p in store.list_final_journals()]
    # Make generation order deterministic independent of filesystem/CPU timing.
    by_new = {j.new_version: j for j in journals}
    for index, version in enumerate(versions, start=1):
        j = by_new[version]
        store.update(j, started_at=f"2026-09-05T00:00:{index:02d}.000Z")
    return paths, repo, manager, store


def test_completed_import_restart_is_current_generation(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0",))
    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    assert all(d.final_state != "RECOVERY_REQUIRED" for d in decisions)
    journal = store.load(store.list_final_journals()[0])
    assert journal.state == PackOperationState.COMPLETED.value


def test_import_update_restart_treats_old_import_as_historical(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1"))
    journals = [store.load(p) for p in store.list_final_journals()]
    old = next(j for j in journals if j.new_version == "1.0")
    latest = next(j for j in journals if j.new_version == "1.1")
    assert old.new_digest == latest.old_digest

    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    by_id = {d.operation_id: d for d in decisions}
    assert by_id[old.operation_id].decision == "historical_superseded"
    assert by_id[old.operation_id].final_state == PackOperationState.COMPLETED.value
    assert by_id[latest.operation_id].final_state == PackOperationState.COMPLETED.value
    # Historical evidence remains durable and is not rewritten to RECOVERY_REQUIRED.
    assert store.load(old.operation_id).state == PackOperationState.COMPLETED.value


def test_multiple_updates_only_latest_generation_is_current_recovery_frontier(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1", "1.2"))
    journals = sorted((store.load(p) for p in store.list_final_journals()), key=lambda j: j.started_at)
    assert journals[0].new_digest == journals[1].old_digest
    assert journals[1].new_digest == journals[2].old_digest
    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    by_id = {d.operation_id: d for d in decisions}
    assert by_id[journals[0].operation_id].decision == "historical_superseded"
    assert by_id[journals[1].operation_id].decision == "historical_superseded"
    assert by_id[journals[2].operation_id].final_state == PackOperationState.COMPLETED.value


def test_historical_journal_previously_mis_mutated_does_not_block_valid_successor(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1"))
    journals = [store.load(p) for p in store.list_final_journals()]
    old = next(j for j in journals if j.new_version == "1.0")
    latest = next(j for j in journals if j.new_version == "1.1")
    store.update(old, state=PackOperationState.RECOVERY_REQUIRED.value)

    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    by_id = {d.operation_id: d for d in decisions}
    assert by_id[old.operation_id].decision == "historical_superseded"
    assert by_id[old.operation_id].final_state == PackOperationState.COMPLETED.value
    assert by_id[latest.operation_id].final_state == PackOperationState.COMPLETED.value
    # Preserve historical evidence rather than silently rewriting old journal state.
    assert store.load(old.operation_id).state == PackOperationState.RECOVERY_REQUIRED.value



def test_multi_pack_generation_histories_recover_independently(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.0", item_id="a1", pack_id="pack.a"))
    manager.import_vpack(make_vpack(tmp_path, version="1.1", item_id="a2", pack_id="pack.a"))
    manager.import_vpack(make_vpack(tmp_path, version="1.0", item_id="b1", pack_id="pack.b"))
    store = PackJournalStore(paths)
    journals = [store.load(p) for p in store.list_final_journals()]
    # Deterministic per-Pack order. Pack B has only one current operation.
    a = sorted((j for j in journals if j.pack_id == "pack.a"), key=lambda j: j.new_version or "")
    b = next(j for j in journals if j.pack_id == "pack.b")
    store.update(a[0], started_at="2026-09-05T00:00:01.000Z")
    store.update(a[1], started_at="2026-09-05T00:00:02.000Z")
    store.update(b, started_at="2026-09-05T00:00:01.500Z")

    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    assert all(d.final_state != "RECOVERY_REQUIRED" for d in decisions)
    assert repo.get_installed_pack("pack.a").pack_version == "1.1"
    assert repo.get_installed_pack("pack.b").pack_version == "1.0"

def test_unknown_current_generation_still_requires_recovery(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1"))
    conn = sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'", ("f" * 64,))
    conn.commit(); conn.close()
    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None).recover_all()
    assert any(d.final_state == "RECOVERY_REQUIRED" for d in decisions)


def test_phase4_restricted_bootstrap_returns_blocked_pack_only_when_explicitly_allowed(tmp_path):
    paths, repo, _, _ = _completed_history(tmp_path, versions=("1.0", "1.1"))
    conn = sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'", ("e" * 64,))
    conn.commit(); conn.close()
    with pytest.raises(PackRecoveryRequiredError):
        bootstrap_phase4(paths.root)

    runtime = bootstrap_phase4(paths.root, allow_recovery_required=True)
    try:
        assert any(d.final_state == "RECOVERY_REQUIRED" for d in runtime.pack_recovery_decisions)
        assert runtime.pack_access.is_recovery_required("demo.pack") is True
    finally:
        runtime.close()


def test_store_manager_recovery_boot_opens_restricted_runtime_and_exposes_no_store_bridge(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1"))
    conn = sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'", ("d" * 64,))
    conn.commit(); conn.close()

    locator = ResourceLocator(Path(__file__).resolve().parents[1])
    runtime = bootstrap_phase7(
        paths.root,
        resource_locator=locator,
        platform_name="posix",
        webview_provider=lambda: (_ for _ in ()).throw(AssertionError("must not import until run_window")),
        store_manager=True,
    )
    try:
        assert isinstance(runtime, StoreManagerRecoveryRuntime)
        assert runtime.recovery_mode is True
        html = runtime.recovery_integration.html
        assert "店長モード（復旧）" in html
        assert "店舗機能は安全のため停止" in html
        assert "demo.pack" in html
        assert "RECOVERY_REQUIRED" in html
        assert "import_pack" not in html and "checkout" not in html
        assert str(paths.root) not in html
    finally:
        runtime.close()


def test_store_manager_healthy_boot_uses_store_manager_ui(tmp_path):
    locator = ResourceLocator(Path(__file__).resolve().parents[1])
    runtime = bootstrap_phase7(
        tmp_path / "healthy",
        resource_locator=locator,
        platform_name="posix",
        webview_provider=lambda: (_ for _ in ()).throw(AssertionError("must not import until run_window")),
        store_manager=True,
    )
    try:
        assert not isinstance(runtime, StoreManagerRecoveryRuntime)
        assert runtime.webview_integration.ui_index.name == "index-store-manager.html"
    finally:
        runtime.close()


def test_store_manager_cli_is_only_supported_manager_flag():
    from fantasy_store.main import _store_manager_requested
    assert _store_manager_requested([]) is False
    assert _store_manager_requested(["--store-manager"]) is True
    assert _store_manager_requested(["--pack-admin"]) is False
    assert _store_manager_requested(["--other"]) is False


def test_normal_recovery_fatal_message_points_to_store_manager_without_raw_detail():
    from fantasy_store.domain.errors import PackRecoveryRequiredError
    from fantasy_store.main import _domain_fatal_message
    message = _domain_fatal_message(PackRecoveryRequiredError("demo.pack", "sensitive internal detail"))
    assert "店長モード" in message
    assert "--store-manager" in message
    assert "sensitive internal detail" not in message


def test_existing_uat_state_old_historical_recovery_required_but_current_successor_boots_normally(tmp_path):
    paths, repo, _, store = _completed_history(tmp_path, versions=("1.0", "1.1"), pack_id="rc.demo.pack")
    journals = [store.load(p) for p in store.list_final_journals()]
    old = next(j for j in journals if j.new_version == "1.0")
    latest = next(j for j in journals if j.new_version == "1.1")
    assert old.new_digest == latest.old_digest
    store.update(old, state=PackOperationState.RECOVERY_REQUIRED.value)

    runtime = bootstrap_phase4(paths.root)
    try:
        assert runtime.pack_lifecycle.repository.get_installed_pack("rc.demo.pack").pack_version == "1.1"
        by_id = {d.operation_id: d for d in runtime.pack_recovery_decisions}
        assert by_id[old.operation_id].decision == "historical_superseded"
        assert by_id[latest.operation_id].final_state == PackOperationState.COMPLETED.value
    finally:
        runtime.close()
