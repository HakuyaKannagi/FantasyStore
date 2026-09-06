from __future__ import annotations

import os
from pathlib import Path

import pytest

from fantasy_store.domain.errors import PackUninstallRequiresDisabledError, PackValidationError
from fantasy_store.domain.pack_version import PackImportClassification, PackVersion, classify_pack_version
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackJournalStore, PackOperationState
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.pack.recovery import PackRecoveryManager
from fantasy_store.pack.updater import PackImportSkipped, PackLifecycleManager
from tests.phase4_helpers import make_vpack, setup_phase4
from tests.phase5_helpers import item, make_pack_file, setup_phase5


@pytest.mark.parametrize("value", ["0.0", "0.1", "1.0", "1.1", "1.9", "1.10", "1.999", "12.345", "999.999"])
def test_pack_version_schema_valid(value):
    assert str(PackVersion.parse(value)) == value


@pytest.mark.parametrize("value", ["1", "1.", ".1", "1.2.3", "01.2", "1.01", "1.001", "1.1000", "1000.1", "v1.2", "1-beta", "1.2-beta"])
def test_pack_version_schema_invalid(value):
    with pytest.raises(PackValidationError):
        PackVersion.parse(value)


def test_pack_version_comparison_is_numeric_tuple_not_lexicographic():
    assert PackVersion.parse("1.9") < PackVersion.parse("1.10")
    assert PackVersion.parse("1.10") < PackVersion.parse("2.0")
    assert PackVersion.parse("2.0") > PackVersion.parse("1.999")
    assert PackVersion.parse("999.998") < PackVersion.parse("999.999")
    assert PackVersion.parse("1.2") == PackVersion.parse("1.2")


def test_pack_import_classification():
    assert classify_pack_version("1.2", None) == PackImportClassification.NEW
    assert classify_pack_version("1.2", "1.1") == PackImportClassification.UPGRADE
    assert classify_pack_version("1.2", "1.2") == PackImportClassification.REINSTALL
    assert classify_pack_version("1.1", "1.2") == PackImportClassification.DOWNGRADE_SKIPPED


def test_pack_schema_rejects_old_single_component_version_before_compare(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    with pytest.raises(PackValidationError):
        manager.import_vpack(make_vpack(tmp_path, version="1", item_id="legacy"))
    assert repo.list_installed_packs() == []
    assert list(paths.pack_operations.glob("*.json")) == []
    assert list(paths.staging_packs.iterdir()) == []


def test_downgrade_skip_does_not_mutate_current_generation_or_journal(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="current"))
    current = repo.get_installed_pack("demo.pack")
    assert current is not None
    before_digest = current.content_digest
    before_items = [dict(row) for row in repo.list_items_for_pack("demo.pack")]
    before_journals = {p.name: p.read_bytes() for p in paths.pack_operations.glob("*.json")}
    before_fs = compute_content_digest(paths.installed_packs / "demo.pack")

    result = manager.import_vpack(make_vpack(tmp_path, version="1.1", item_id="older"))

    assert isinstance(result, PackImportSkipped)
    assert result.classification == PackImportClassification.DOWNGRADE_SKIPPED
    assert result.installed_version == "1.2"
    assert result.incoming_version == "1.1"
    after = repo.get_installed_pack("demo.pack")
    assert after is not None and after.pack_version == "1.2" and after.content_digest == before_digest
    assert [dict(row) for row in repo.list_items_for_pack("demo.pack")] == before_items
    assert compute_content_digest(paths.installed_packs / "demo.pack") == before_fs
    assert {p.name: p.read_bytes() for p in paths.pack_operations.glob("*.json")} == before_journals
    assert list(paths.staging_packs.iterdir()) == []


def test_same_version_reinstall_creates_new_generation(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    first = manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="old-item"))
    second = manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="new-item"))
    assert second.version_classification == PackImportClassification.REINSTALL
    assert first.content_digest != second.content_digest
    row = repo.get_installed_pack("demo.pack")
    assert row is not None and row.pack_version == "1.2" and row.content_digest == second.content_digest
    assert [r["item_id"] for r in repo.list_items_for_pack("demo.pack")] == ["new-item"]
    journals = [PackJournalStore(paths).load(p) for p in paths.pack_operations.glob("*.json")]
    assert len(journals) == 2
    assert all(j.state == PackOperationState.COMPLETED.value for j in journals)


def test_invalid_legacy_installed_version_is_not_auto_converted(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.0", item_id="current"))
    # Controlled fixture representing old Windows UAT metadata. Do not migrate it.
    with repo.transaction() as conn:
        conn.execute("UPDATE installed_packs SET pack_version='1' WHERE pack_id='demo.pack'")
    before = {p.name: p.read_bytes() for p in paths.pack_operations.glob("*.json")}
    with pytest.raises(PackValidationError):
        manager.import_vpack(make_vpack(tmp_path, version="1.1", item_id="new"))
    assert repo.get_installed_pack("demo.pack").pack_version == "1"
    assert {p.name: p.read_bytes() for p in paths.pack_operations.glob("*.json")} == before


def test_enabled_pack_cannot_be_uninstalled(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="i1"))
    with pytest.raises(PackUninstallRequiresDisabledError):
        manager.uninstall_pack("demo.pack")
    assert repo.get_installed_pack("demo.pack") is not None
    assert (paths.installed_packs / "demo.pack").is_dir()


def test_disabled_pack_uninstall_removes_catalog_but_preserves_history(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1", name="Purchased")])])
    env.cart.add_to_cart("pack.one", "i1", 1)
    order = env.checkout.checkout("11111111-1111-4111-8111-111111111111").order
    env.pack_service.set_pack_enabled("pack.one", False)

    removed = env.pack_service.uninstall_pack("pack.one")

    assert removed.pack_id == "pack.one" and removed.enabled is False
    assert env.packs.get_installed_pack("pack.one") is None
    assert env.packs.count_items_for_pack("pack.one") == 0
    assert not (env.paths.installed_packs / "pack.one").exists()
    detail = env.history.get_order_detail(order.order_id)
    assert detail.lines[0].name == "Purchased"
    assert env.stats.get_statistics().order_count == 1


def _prepare_uninstall_journal(tmp_path: Path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="i1"))
    manager.set_pack_enabled("demo.pack", False)
    row = repo.get_installed_pack("demo.pack")
    assert row is not None
    journals = PackJournalStore(paths)
    from fantasy_store.domain.ids import new_uuid_v4
    op = new_uuid_v4()
    resolver = PackPathResolver(paths)
    installed = resolver.installed_pack_root("demo.pack")
    backup = resolver.backup_pack_root(op, "demo.pack")
    staging = resolver.staging_root(op)
    j = journals.create(op, staging_path=staging)
    j = journals.transition(
        j,
        PackOperationState.VALIDATED,
        operation_type="UNINSTALL",
        pack_id="demo.pack",
        old_digest=row.content_digest,
        old_version=row.pack_version,
        new_digest=None,
        new_version=None,
        installed_path=resolver.relative_to_persistent(installed),
        backup_path=resolver.relative_to_persistent(backup),
    )
    j = journals.transition(j, PackOperationState.READY_TO_SWITCH)
    return paths, repo, journals, j, installed, backup, staging


def test_uninstall_recovery_fs_first_restores_old_when_db_not_committed(tmp_path):
    paths, repo, journals, j, installed, backup, _ = _prepare_uninstall_journal(tmp_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(installed, backup)
    j = journals.transition(j, PackOperationState.FILES_SWITCHED)
    decision = PackRecoveryManager(paths, repository=repo, journal_store=journals, retry_delay=0).recover_operation(j)
    assert decision.final_state == "ROLLED_BACK"
    assert repo.get_installed_pack("demo.pack") is not None
    assert installed.is_dir() and not backup.exists()
    assert not journals.path_for(j.operation_id).exists()


def test_uninstall_recovery_db_first_completes_known_old_filesystem(tmp_path):
    paths, repo, journals, j, installed, backup, _ = _prepare_uninstall_journal(tmp_path)
    assert repo.uninstall_pack("demo.pack") is True
    j = journals.transition(j, PackOperationState.FILES_SWITCHED)
    decision = PackRecoveryManager(paths, repository=repo, journal_store=journals, retry_delay=0).recover_operation(j)
    assert decision.final_state == PackOperationState.COMPLETED.value
    assert repo.get_installed_pack("demo.pack") is None
    assert not installed.exists()
    assert journals.load(j.operation_id).state == PackOperationState.COMPLETED.value


def test_uninstall_restart_stays_uninstalled_and_reinstall_older_is_new(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="i1"))
    manager.set_pack_enabled("demo.pack", False)
    manager.uninstall_pack("demo.pack")
    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0).recover_all()
    assert repo.get_installed_pack("demo.pack") is None
    assert all(d.final_state != PackOperationState.RECOVERY_REQUIRED.value for d in decisions)

    reinstalled = manager.import_vpack(make_vpack(tmp_path, version="1.1", item_id="older-after-uninstall"))
    assert reinstalled.version_classification == PackImportClassification.NEW
    assert repo.get_installed_pack("demo.pack").pack_version == "1.1"
    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0).recover_all()
    assert all(d.final_state != PackOperationState.RECOVERY_REQUIRED.value for d in decisions)


def test_store_manager_bridge_exposes_uninstall_only_in_manager_surface(tmp_path):
    from fantasy_store.bridge.api import BridgeApi
    from fantasy_store.bridge.file_picker import DeterministicFilePicker
    from fantasy_store.bridge.store_manager_api import StoreManagerBridgeApi

    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    env.pack_service.set_pack_enabled("pack.one", False)
    kwargs = dict(
        catalog=env.catalog,
        cart=env.cart,
        checkout=env.checkout,
        history=env.history,
        stats=env.stats,
        packs=env.pack_service,
        file_picker=DeterministicFilePicker(None),
    )
    normal = BridgeApi(**kwargs)
    manager = StoreManagerBridgeApi(**kwargs)
    assert not hasattr(normal, "uninstall_pack")
    response = manager.uninstall_pack({"pack_id": "pack.one"})
    assert response["ok"] is True
    assert response["data"]["pack"]["pack_id"] == "pack.one"
    assert env.packs.get_installed_pack("pack.one") is None


def test_bridge_import_reports_version_classification_and_downgrade_skip(tmp_path):
    from fantasy_store.bridge.file_picker import DeterministicFilePicker
    from fantasy_store.bridge.store_manager_api import StoreManagerBridgeApi

    env = setup_phase5(tmp_path)
    picker = DeterministicFilePicker(make_pack_file(tmp_path, "pack.one", version="1.2", items=[item("i1")]))
    bridge = StoreManagerBridgeApi(
        catalog=env.catalog, cart=env.cart, checkout=env.checkout, history=env.history,
        stats=env.stats, packs=env.pack_service, file_picker=picker,
    )
    first = bridge.import_pack()
    assert first["ok"] and first["data"]["classification"] == "NEW"
    picker.selection = make_pack_file(tmp_path, "pack.one", version="1.1", items=[item("old")], filename="older.vpack")
    skipped = bridge.import_pack()
    assert skipped["ok"] is True
    assert skipped["data"]["status"] == "DOWNGRADE_SKIPPED"
    assert skipped["data"]["installed_version"] == "1.2"
    assert skipped["data"]["incoming_version"] == "1.1"


def test_uninstall_recovery_handles_old_move_before_journal_update(tmp_path):
    paths, repo, journals, j, installed, backup, _ = _prepare_uninstall_journal(tmp_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(installed, backup)
    # Durable journal still says READY_TO_SWITCH: physical observation wins.
    decision = PackRecoveryManager(paths, repository=repo, journal_store=journals, retry_delay=0).recover_operation(j)
    assert decision.final_state == "ROLLED_BACK"
    assert installed.is_dir()
    assert repo.get_installed_pack("demo.pack") is not None


def test_uninstall_recovery_handles_db_commit_before_journal_update(tmp_path):
    paths, repo, journals, j, installed, backup, _ = _prepare_uninstall_journal(tmp_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(installed, backup)
    assert repo.uninstall_pack("demo.pack") is True
    # Durable journal is stale, but DB absence + known-old backup proves commit.
    decision = PackRecoveryManager(paths, repository=repo, journal_store=journals, retry_delay=0).recover_operation(j)
    assert decision.final_state == PackOperationState.COMPLETED.value
    assert repo.get_installed_pack("demo.pack") is None
    assert not installed.exists() and not backup.exists()
    assert journals.load(j.operation_id).state == PackOperationState.COMPLETED.value


def test_uninstall_unknown_generation_remains_recovery_required(tmp_path):
    paths, repo, journals, j, installed, backup, _ = _prepare_uninstall_journal(tmp_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(installed, backup)
    # Tamper operation-owned backup into an unknown generation without touching DB.
    (backup / "pack.json").write_text('{"broken":true}', encoding="utf-8")
    decision = PackRecoveryManager(paths, repository=repo, journal_store=journals, retry_delay=0).recover_operation(j)
    assert decision.final_state == PackOperationState.RECOVERY_REQUIRED.value
    assert journals.load(j.operation_id).state == PackOperationState.RECOVERY_REQUIRED.value


def test_uninstall_db_failure_recovers_old_generation_without_history_loss(tmp_path, monkeypatch):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1", name="Purchased")])])
    env.cart.add_to_cart("pack.one", "i1", 1)
    order = env.checkout.checkout("22222222-2222-4222-8222-222222222222").order
    env.pack_service.set_pack_enabled("pack.one", False)
    row_before = env.packs.get_installed_pack("pack.one")
    assert row_before is not None
    digest_before = row_before.content_digest

    def fail_db_uninstall(pack_id: str, *, failure_hook=None):
        raise RuntimeError("injected uninstall DB failure")

    monkeypatch.setattr(env.packs, "uninstall_pack", fail_db_uninstall)
    with pytest.raises(RuntimeError, match="injected uninstall DB failure"):
        env.pack_service.uninstall_pack("pack.one")

    # Observation-based recovery restores the pre-operation generation because
    # the DB never committed the uninstall.
    row_after = env.packs.get_installed_pack("pack.one")
    assert row_after is not None and row_after.content_digest == digest_before
    assert compute_content_digest(env.paths.installed_packs / "pack.one") == digest_before
    assert env.history.get_order_detail(order.order_id).lines[0].name == "Purchased"
    assert env.stats.get_statistics().order_count == 1


def test_completed_uninstall_journal_is_durable_history_and_not_reinstalled(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(make_vpack(tmp_path, version="1.2", item_id="i1"))
    manager.set_pack_enabled("demo.pack", False)
    manager.uninstall_pack("demo.pack")

    journals = [PackJournalStore(paths).load(p) for p in paths.pack_operations.glob("*.json")]
    uninstall = [j for j in journals if j.operation_type == "UNINSTALL"]
    assert len(uninstall) == 1
    assert uninstall[0].state == PackOperationState.COMPLETED.value
    assert uninstall[0].old_version == "1.2"
    assert uninstall[0].new_version is None
    assert repo.get_installed_pack("demo.pack") is None
    assert not (paths.installed_packs / "demo.pack").exists()

    decisions = PackRecoveryManager(paths, repository=repo, retry_delay=0).recover_all()
    assert all(d.final_state != PackOperationState.RECOVERY_REQUIRED.value for d in decisions)
    assert repo.get_installed_pack("demo.pack") is None


def test_pack_id_change_between_version_preflight_and_full_validation_is_rejected(tmp_path, monkeypatch):
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    source_a = make_vpack(tmp_path, pack_id="demo.pack", version="1.0", item_id="a")
    source_b = make_vpack(tmp_path, pack_id="other.pack", version="1.0", item_id="b")
    real_prepare = manager.importer.prepare

    def changed_prepare(source_path, *, existing_pack_ids=(), operation_id=None, phase_callback=None):
        return real_prepare(
            source_b,
            existing_pack_ids=existing_pack_ids,
            operation_id=operation_id,
            phase_callback=phase_callback,
        )

    monkeypatch.setattr(manager.importer, "prepare", changed_prepare)
    with pytest.raises(PackValidationError, match="changed during validation"):
        manager.import_vpack(source_a)

    assert repo.list_installed_packs() == []
    assert list(paths.pack_operations.glob("*.json")) == []
    assert list(paths.staging_packs.iterdir()) == []
