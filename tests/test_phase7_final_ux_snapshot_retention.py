from __future__ import annotations

import threading
import time
from pathlib import Path

from fantasy_store.application.history_maintenance_service import HistoryMaintenanceService
from fantasy_store.bridge.money_mapper import money_display, money_to_dto
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from fantasy_store.persistence.user_repository import CartItem, Order, OrderItemSnapshot, UserSetting
from fantasy_store.snapshot.retention import SNAPSHOT_HISTORY_HARD_LIMIT_BYTES, SnapshotRetentionManager
from tests.phase5_helpers import setup_phase5
from fantasy_store.domain.ids import new_uuid_v4


def _insert_history(env, *, purchased_at: str, filename: str, size: int, quantity: int = 1):
    order_id = new_uuid_v4()
    order = Order(order_id, purchased_at, MoneyValue.from_literal(MoneyLiteral("1", "0")), quantity, 1)
    env.users.insert_order(order)
    target_dir = env.paths.snapshot_images / order_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(b"x" * size)
    rel = target.relative_to(env.paths.user_data).as_posix()
    env.users.insert_order_item(OrderItemSnapshot(
        order_id=order_id,
        line_no=1,
        pack_id="pack.hist",
        item_id="item.hist",
        item_name="購入時商品",
        unit_price=MoneyValue.from_literal(MoneyLiteral("1", "0")),
        quantity=quantity,
        line_total=MoneyValue.from_literal(MoneyLiteral(str(quantity), "0")),
        category="履歴",
        description="snapshot authority",
        attributes_json="{}",
        primary_image_snapshot_path=rel,
        primary_image_snapshot_sha256="0" * 64,
    ))
    return order_id, target, rel


def test_money_human_formatter_threshold_superscript_nine_digits_and_rounding():
    assert money_display(MoneyValue.from_literal(MoneyLiteral("398", "1"))) == "3,980"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("128", "2"))) == "12,800"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("9", "3"))) == "9,000"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("1", "16"))) == "1 × 10¹⁶"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("123456789", "25"))) == "123456789 × 10²⁵"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("1234567894", "20"))) == "約 123456789 × 10²¹"
    assert money_display(MoneyValue.from_literal(MoneyLiteral("1234567896", "20"))) == "約 123456790 × 10²¹"


def test_money_formatter_never_changes_exact_terms():
    value = MoneyValue({5: 123456789, 2: 7, 0: 11})
    before = value.to_canonical_obj()
    dto = money_to_dto(value)
    assert dto["terms"] == before["terms"]
    assert value.to_canonical_obj() == before
    assert "^" not in dto["display"]
    assert "10" in dto["display"]


def test_snapshot_retention_product_limit_constant_is_512_mib():
    assert SNAPSHOT_HISTORY_HARD_LIMIT_BYTES == 536_870_912


def test_snapshot_retention_prunes_oldest_order_image_only_and_keeps_history(tmp_path):
    env = setup_phase5(tmp_path)
    old_id, old_path, _ = _insert_history(env, purchased_at="2026-01-01T00:00:00.000Z", filename="1.png", size=10)
    new_id, new_path, _ = _insert_history(env, purchased_at="2026-02-01T00:00:00.000Z", filename="1.png", size=10)
    manager = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=10)
    result = manager.prune()
    assert result.total_before == 20 and result.total_after == 10
    assert not old_path.exists() and new_path.exists()
    assert env.users.get_order_detail(old_id) is not None
    assert env.users.get_order_detail(new_id) is not None
    assert env.stats.get_statistics().order_count == 2


def test_snapshot_retention_is_line_order_deterministic_within_same_order(tmp_path):
    env = setup_phase5(tmp_path)
    order_id = new_uuid_v4()
    env.users.insert_order(Order(order_id, "2026-01-01T00:00:00.000Z", MoneyValue.from_literal(MoneyLiteral("2", "0")), 2, 2))
    order_dir = env.paths.snapshot_images / order_id
    order_dir.mkdir(parents=True)
    paths = []
    for line_no in (1, 2):
        path = order_dir / f"{line_no}.png"; path.write_bytes(b"x" * 8); paths.append(path)
        env.users.insert_order_item(OrderItemSnapshot(
            order_id=order_id, line_no=line_no, pack_id="p", item_id=f"i{line_no}", item_name=f"I{line_no}",
            unit_price=MoneyValue.from_literal(MoneyLiteral("1", "0")), quantity=1,
            line_total=MoneyValue.from_literal(MoneyLiteral("1", "0")), category="c", description="d",
            attributes_json="{}", primary_image_snapshot_path=path.relative_to(env.paths.user_data).as_posix(),
            primary_image_snapshot_sha256="0" * 64,
        ))
    result = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=8).prune()
    assert result.total_after == 8
    assert not paths[0].exists() and paths[1].exists()


def test_history_reset_is_atomic_for_db_and_preserves_cart_settings(tmp_path):
    env = setup_phase5(tmp_path)
    _order_id, snapshot_path, _ = _insert_history(env, purchased_at="2026-01-01T00:00:00.000Z", filename="1.png", size=10)
    env.users.insert_cart_item(CartItem("pack.cart", "item.cart", 2, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    env.users.upsert_setting(UserSetting("demo.setting", '{"x":1}', "2026-01-01T00:00:00Z"))
    service = HistoryMaintenanceService(env.users, env.snapshots, env.cart_lock)
    result = service.reset_all()
    assert result.deleted_orders == 1 and result.failed_snapshot_directories == 0
    assert env.users.order_count() == 0
    assert env.stats.get_statistics().order_count == 0
    assert env.stats.get_statistics().total_quantity == 0
    assert env.users.list_order_totals() == []
    assert env.users.get_cart_item("pack.cart", "item.cart").quantity == 2
    assert env.users.get_setting("demo.setting") is not None
    assert not snapshot_path.exists()


def test_history_reset_waits_for_checkout_coordinator(tmp_path):
    env = setup_phase5(tmp_path)
    service = HistoryMaintenanceService(env.users, env.snapshots, env.cart_lock)
    entered = threading.Event(); finished = threading.Event()
    def reset():
        entered.set(); service.reset_all(); finished.set()
    with env.cart_lock.exclusive():
        t = threading.Thread(target=reset); t.start(); assert entered.wait(2)
        time.sleep(0.05)
        assert not finished.is_set()
    t.join(2)
    assert finished.is_set()


def test_checkout_success_invokes_retention_and_can_retire_its_own_image(tmp_path):
    from fantasy_store.snapshot.retention import SnapshotRetentionManager
    from tests.phase5_helpers import item
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    env.checkout.snapshot_retention = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=0)
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    assert env.users.order_count() == 1
    assert result.order.lines[0].name == "i1"
    refreshed = env.history.get_order_detail(result.order.order_id)
    assert refreshed.lines[0].snapshot_image_available is False
    assert refreshed.lines[0].snapshot_image_ref is None


def test_store_manager_bridge_history_reset_is_manager_only(tmp_path):
    from fantasy_store.application.history_maintenance_service import HistoryMaintenanceService
    from fantasy_store.bridge.api import BridgeApi
    from fantasy_store.bridge.file_picker import DeterministicFilePicker
    from fantasy_store.bridge.store_manager_api import StoreManagerBridgeApi
    env = setup_phase5(tmp_path)
    maintenance = HistoryMaintenanceService(env.users, env.snapshots, env.cart_lock)
    kwargs = dict(
        catalog=env.catalog, cart=env.cart, checkout=env.checkout, history=env.history,
        stats=env.stats, packs=env.pack_service, file_picker=DeterministicFilePicker(None),
    )
    normal = BridgeApi(**kwargs)
    manager = StoreManagerBridgeApi(**kwargs, history_maintenance=maintenance)
    assert not hasattr(normal, "reset_purchase_history")
    response = manager.reset_purchase_history()
    assert response["ok"] is True
    assert response["data"]["deleted_orders"] == 0


def test_phase5_bootstrap_runs_snapshot_retention_pass(tmp_path, monkeypatch):
    from fantasy_store.bootstrap import bootstrap_phase4, bootstrap_phase5
    from fantasy_store.snapshot.retention import SnapshotRetentionManager, SnapshotRetentionResult
    called = []
    monkeypatch.setattr(SnapshotRetentionManager, "prune", lambda self: (called.append(True) or SnapshotRetentionResult(0, 0, ())))
    phase4 = bootstrap_phase4(tmp_path / "runtime")
    phase5 = bootstrap_phase5(phase4_runtime=phase4)
    try:
        assert called == [True]
    finally:
        phase5.close()


def test_history_reset_and_real_checkout_are_serialized(tmp_path):
    from tests.phase5_helpers import item
    entered = threading.Event(); release = threading.Event(); checkout_done = threading.Event(); reset_done = threading.Event()
    def hook(stage):
        if stage == "before_snapshot":
            entered.set(); assert release.wait(3)
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])], checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one", "i1", 1)
    service = HistoryMaintenanceService(env.users, env.snapshots, env.cart_lock)
    def checkout():
        env.checkout.checkout(new_uuid_v4()); checkout_done.set()
    def reset():
        service.reset_all(); reset_done.set()
    tc = threading.Thread(target=checkout); tc.start(); assert entered.wait(2)
    tr = threading.Thread(target=reset); tr.start(); time.sleep(0.05)
    assert not reset_done.is_set()
    release.set(); tc.join(3); tr.join(3)
    assert checkout_done.is_set() and reset_done.is_set()
    assert env.users.order_count() == 0
    assert env.stats.get_statistics().order_count == 0


def test_snapshot_retention_counts_recent_unreferenced_managed_final_files_in_hard_limit(tmp_path):
    env = setup_phase5(tmp_path)
    _order_id, referenced_path, referenced_rel = _insert_history(
        env, purchased_at="2026-01-01T00:00:00.000Z", filename="1.png", size=8
    )
    orphan_order_id = new_uuid_v4()
    orphan_dir = env.paths.snapshot_images / orphan_order_id
    orphan_dir.mkdir(parents=True)
    orphan_path = orphan_dir / "1.png"
    orphan_path.write_bytes(b"o" * 8)

    # The physical managed root is 16 bytes even though only 8 are DB-referenced.
    # Capacity pressure retires the non-authoritative orphan first and leaves the
    # authoritative history image intact.
    result = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=8).prune()
    assert result.total_before == 16 and result.total_after == 8
    assert not orphan_path.exists()
    assert referenced_path.exists()
    assert referenced_rel not in result.removed_paths
