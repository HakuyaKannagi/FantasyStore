from __future__ import annotations

from pathlib import Path
import logging

from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from fantasy_store.persistence.user_repository import Order, OrderItemSnapshot
from fantasy_store.snapshot.retention import SnapshotRetentionManager
from tests.phase5_helpers import item, setup_phase5


def _add_old_history(env, *, size: int, purchased_at: str = "2026-01-01T00:00:00.000Z") -> Path:
    order_id = new_uuid_v4()
    env.users.insert_order(Order(order_id, purchased_at, MoneyValue.from_literal(MoneyLiteral("1", "0")), 1, 1))
    directory = env.paths.snapshot_images / order_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "1.png"
    path.write_bytes(b"x" * size)
    rel = path.relative_to(env.paths.user_data).as_posix()
    env.users.insert_order_item(OrderItemSnapshot(
        order_id=order_id,
        line_no=1,
        pack_id="old.pack",
        item_id="old.item",
        item_name="old",
        unit_price=MoneyValue.from_literal(MoneyLiteral("1", "0")),
        quantity=1,
        line_total=MoneyValue.from_literal(MoneyLiteral("1", "0")),
        category="old",
        description="old",
        attributes_json="{}",
        primary_image_snapshot_path=rel,
        primary_image_snapshot_sha256="0" * 64,
    ))
    return path


def _source_size(env) -> int:
    return (env.paths.installed_packs / "pack.one" / "assets" / "a.png").stat().st_size


def test_hard_limit_normal_capacity_saves_whole_order_snapshot(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    env.checkout.snapshot_retention = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=10_000_000)
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    detail = env.history.get_order_detail(result.order.order_id)
    assert detail.lines[0].snapshot_image_available is True
    assert detail.lines[0].snapshot_image_ref is not None


def test_hard_limit_prunes_oldest_before_new_order_write(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    required = _source_size(env)
    old = _add_old_history(env, size=20)
    env.checkout.snapshot_retention = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=required + 19)
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    assert not old.exists()
    assert env.history.get_order_detail(result.order.order_id).lines[0].snapshot_image_available is True
    managed = sum(p.stat().st_size for p in env.paths.snapshot_images.rglob("*") if p.is_file())
    assert managed <= required + 19


def test_hard_limit_delete_failure_skips_new_order_images_but_checkout_succeeds(tmp_path, monkeypatch):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    required = _source_size(env)
    old = _add_old_history(env, size=20)
    records = []
    class Capture(logging.Handler):
        def emit(self, record): records.append(record)
    logger = logging.Logger("snapshot-hard-limit-test", level=logging.DEBUG)
    logger.addHandler(Capture())
    manager = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=required + 19, logger=logger)
    env.checkout.logger = logger
    original_unlink = Path.unlink
    def fail_old_unlink(path, *args, **kwargs):
        if path == old:
            raise PermissionError("injected retention delete failure")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_old_unlink)
    env.checkout.snapshot_retention = manager
    before = sum(p.stat().st_size for p in env.paths.snapshot_images.rglob("*") if p.is_file())
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    after = sum(p.stat().st_size for p in env.paths.snapshot_images.rglob("*") if p.is_file())
    detail = env.history.get_order_detail(result.order.order_id)
    assert env.users.order_count() == 2
    assert env.stats.get_statistics().order_count == 2
    assert detail.lines[0].snapshot_image_available is False
    assert detail.lines[0].snapshot_image_ref is None
    assert old.exists() and after == before
    event_codes = {getattr(record, "event_code", None) for record in records}
    assert "SNAPSHOT_RETENTION_DELETE_FAILED" in event_codes
    assert "SNAPSHOT_RETENTION_CAPACITY_UNAVAILABLE" in event_codes
    assert "SNAPSHOT_RETENTION_NEW_ORDER_SKIPPED" in event_codes


def test_hard_limit_already_over_and_prune_failure_stops_growth(tmp_path, monkeypatch):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    old = _add_old_history(env, size=100)
    manager = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=50)
    monkeypatch.setattr(manager, "_remove_file", lambda *args, **kwargs: False)
    env.checkout.snapshot_retention = manager
    before = old.stat().st_size
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    after = sum(p.stat().st_size for p in env.paths.snapshot_images.rglob("*") if p.is_file())
    detail = env.history.get_order_detail(result.order.order_id)
    assert after == before
    assert detail.lines[0].snapshot_image_available is False
    assert detail.lines[0].snapshot_image_ref is None
    assert result.order.lines[0].name == "i1"


def test_hard_limit_incomplete_capacity_observation_fails_closed_for_images_only(tmp_path, monkeypatch):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    manager = SnapshotRetentionManager(env.users, env.snapshots, max_bytes=10_000_000)
    monkeypatch.setattr(manager, "_physical_managed_files", lambda: ({}, False))
    env.checkout.snapshot_retention = manager
    env.cart.add_to_cart("pack.one", "i1", 1)
    result = env.checkout.checkout(new_uuid_v4())
    detail = env.history.get_order_detail(result.order.order_id)
    assert env.users.order_count() == 1
    assert detail.lines[0].snapshot_image_available is False
    assert detail.lines[0].snapshot_image_ref is None
