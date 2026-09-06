from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fantasy_store.application.checkout_service import CheckoutService
from fantasy_store.domain.errors import DomainError
from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from fantasy_store.persistence.user_repository import Order
from fantasy_store.snapshot.cleanup import SnapshotOrphanCleaner
from tests.phase5_helpers import item, make_pack_file, setup_phase5


def test_checkout_one_item_exact_db_snapshot_backup(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1",price={"significand":"123","exponent":"2"})])])
    env.cart.add_to_cart("pack.one","i1",3)
    before=len(env.backup.list_backup_files())
    req=new_uuid_v4(); r=env.checkout.checkout(req)
    assert r.idempotent_replay is False and r.backup_failed is False
    assert r.order.total_quantity==3 and r.order.line_count==1
    assert r.order.total_amount==MoneyValue.from_literal(MoneyLiteral("369","2"))
    assert env.users.get_purchase_request(req).order_id==r.order.order_id
    assert env.users.list_cart()==[]
    assert r.order.lines[0].snapshot_image_available is True
    assert len(env.backup.list_backup_files()) >= before
    assert (env.paths.snapshot_images/r.order.order_id).is_dir()


def test_checkout_multi_item_multi_pack_exact_sparse_money(tmp_path):
    env=setup_phase5(tmp_path,packs=[
        ("pack.a",[item("a1",price={"significand":"1","exponent":"9000000000000000000"})]),
        ("pack.b",[item("b1",price={"significand":"7","exponent":"0"})]),
    ])
    env.cart.add_to_cart("pack.b","b1",5); env.cart.add_to_cart("pack.a","a1",2)
    r=env.checkout.checkout(new_uuid_v4())
    assert r.order.total_amount.block_count==2
    assert r.order.total_quantity==7 and len(r.order.lines)==2
    assert [x.pack_id for x in r.order.lines]==["pack.b","pack.a"]  # cart insertion order is preserved in snapshot lines


def test_checkout_sequential_idempotency(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); req=new_uuid_v4()
    first=env.checkout.checkout(req); second=env.checkout.checkout(req)
    assert second.idempotent_replay is True
    assert second.order.order_id==first.order.order_id
    assert env.users.order_count()==1
    assert len(list(env.paths.snapshot_images.iterdir()))==1


def test_checkout_concurrent_same_request_one_order(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); req=new_uuid_v4()
    results=[]; errors=[]
    def run():
        try: results.append(env.checkout.checkout(req))
        except Exception as e: errors.append(e)
    ts=[threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]; [t.join(5) for t in ts]
    assert not errors and len(results)==2
    assert {r.order.order_id for r in results}.__len__()==1
    assert sorted(r.idempotent_replay for r in results)==[False,True]
    assert env.users.order_count()==1 and len(list(env.paths.snapshot_images.iterdir()))==1


def test_db_commit_then_response_loss_replays_existing(tmp_path):
    lost={"once":True}
    def hook(stage):
        if stage=="after_db_commit" and lost["once"]:
            lost["once"]=False
            raise RuntimeError("response/process loss")
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",1); req=new_uuid_v4()
    with pytest.raises(RuntimeError): env.checkout.checkout(req)
    assert env.users.order_count()==1 and env.users.list_cart()==[]
    r=env.checkout.checkout(req)
    assert r.idempotent_replay and env.users.order_count()==1


@pytest.mark.parametrize("stage", ["db_after_order","db_after_items","db_after_request","db_after_cart_delete"])
def test_checkout_db_failure_rolls_back_and_keeps_cart(tmp_path,stage):
    def hook(name):
        if name==stage: raise RuntimeError("injected db failure")
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",2); req=new_uuid_v4()
    with pytest.raises(DomainError) as exc: env.checkout.checkout(req)
    assert exc.value.code=="DB_WRITE_FAILED"
    assert env.users.order_count()==0 and env.users.get_purchase_request(req) is None
    assert env.users.get_cart_item("pack.one","i1").quantity==2
    assert list(env.paths.snapshot_images.iterdir())==[]


def test_checkout_backup_failure_nonfatal(tmp_path,monkeypatch):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1)
    monkeypatch.setattr(env.backup,"backup_after_checkout",lambda: (_ for _ in ()).throw(OSError("disk")))
    r=env.checkout.checkout(new_uuid_v4())
    assert r.backup_failed is True and env.users.order_count()==1 and env.users.list_cart()==[]


def test_checkout_pack_disabled_after_cart_rejected_whole_order(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); env.lifecycle.set_pack_enabled("pack.one",False)
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="CHECKOUT_ITEM_UNAVAILABLE" and env.users.order_count()==0 and len(env.users.list_cart())==1


def test_checkout_item_removed_by_update_rejected(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1)
    env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.one",version="1.1",items=[item("other")]))
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="CHECKOUT_ITEM_UNAVAILABLE" and env.users.order_count()==0


def test_checkout_price_change_after_cart_uses_current_generation(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1",price={"significand":"1","exponent":"0"})])])
    env.cart.add_to_cart("pack.one","i1",2)
    env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.one",version="1.1",items=[item("i1",price={"significand":"9","exponent":"3"})]))
    r=env.checkout.checkout(new_uuid_v4())
    assert r.order.lines[0].unit_price==MoneyValue.from_literal(MoneyLiteral("9","3"))
    assert r.order.total_amount==MoneyValue.from_literal(MoneyLiteral("18","3"))


def test_checkout_missing_image_after_cart_rejected(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1)
    (env.paths.installed_packs/"pack.one"/"assets"/"a.png").unlink()
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="CHECKOUT_ITEM_UNAVAILABLE" and len(env.users.list_cart())==1


@pytest.mark.parametrize("target", ["prepare_pending","finalize"])
def test_snapshot_pre_db_failure_keeps_cart_no_order(tmp_path,monkeypatch,target):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1)
    monkeypatch.setattr(env.snapshots,target,lambda *a,**k: (_ for _ in ()).throw(OSError("snapshot failure")))
    with pytest.raises(OSError): env.checkout.checkout(new_uuid_v4())
    assert env.users.order_count()==0 and len(env.users.list_cart())==1


def test_db_rollback_snapshot_cleanup_failure_leaves_orphan_but_not_order(tmp_path,monkeypatch):
    def hook(name):
        if name=="db_after_order": raise RuntimeError("db fail")
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",1)
    monkeypatch.setattr(env.snapshots,"cleanup_final",lambda order_id: False)
    with pytest.raises(DomainError): env.checkout.checkout(new_uuid_v4())
    assert env.users.order_count()==0 and len(env.users.list_cart())==1
    assert len(list(env.paths.snapshot_images.iterdir()))==1


def test_cart_write_started_during_checkout_waits_and_is_not_deleted(tmp_path):
    entered=threading.Event(); release=threading.Event()
    def hook(stage):
        if stage=="before_snapshot":
            entered.set(); assert release.wait(5)
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1"),item("i2")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",1)
    result=[]
    t_checkout=threading.Thread(target=lambda: result.append(env.checkout.checkout(new_uuid_v4())))
    t_checkout.start(); assert entered.wait(5)
    add_done=threading.Event()
    t_add=threading.Thread(target=lambda: (env.cart.add_to_cart("pack.one","i2",1),add_done.set()))
    t_add.start(); time.sleep(.05); assert not add_done.is_set()
    release.set(); t_checkout.join(5); t_add.join(5)
    assert add_done.is_set() and env.users.get_cart_item("pack.one","i2") is not None
    assert env.users.get_cart_item("pack.one","i1") is None


def test_checkout_holds_pack_read_lock_through_commit_blocking_update(tmp_path):
    entered=threading.Event(); release=threading.Event()
    def hook(stage):
        if stage=="before_snapshot": entered.set(); release.wait(5)
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1",name="Old")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",1)
    checkout_result=[]
    tc=threading.Thread(target=lambda: checkout_result.append(env.checkout.checkout(new_uuid_v4())))
    tc.start(); assert entered.wait(5)
    update_done=threading.Event()
    def update():
        env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.one",version="1.1",items=[item("i1",name="New")]))
        update_done.set()
    tu=threading.Thread(target=update); tu.start(); time.sleep(.05)
    assert not update_done.is_set() and env.packs.get_installed_pack("pack.one").pack_version=="1.0"
    release.set(); tc.join(5); tu.join(5)
    assert update_done.is_set() and checkout_result[0].order.lines[0].name=="Old"
    assert env.packs.get_installed_pack("pack.one").pack_version=="1.1"


def test_multi_pack_lock_order_and_reverse_release(tmp_path):
    trace=[]
    env=setup_phase5(tmp_path,packs=[("pack.c",[item("c1")]),("pack.a",[item("a1")]),("pack.b",[item("b1")])],lock_trace=trace.append)
    env.cart.add_to_cart("pack.c","c1",1); env.cart.add_to_cart("pack.a","a1",1); env.cart.add_to_cart("pack.b","b1",1)
    trace.clear(); env.checkout.checkout(new_uuid_v4())
    acq=[x for x in trace if x.startswith("pack_read_acquired")]
    rel=[x for x in trace if x.startswith("pack_read_released")]
    assert acq==["pack_read_acquired:pack.a","pack_read_acquired:pack.b","pack_read_acquired:pack.c"]
    assert rel==["pack_read_released:pack.c","pack_read_released:pack.b","pack_read_released:pack.a"]
    assert trace.index("sqlite_transaction_begin") > trace.index("pack_read_acquired:pack.c")


def test_multi_pack_middle_timeout_releases_prior_and_does_not_start_db(tmp_path):
    trace=[]
    env=setup_phase5(tmp_path,packs=[("pack.a",[item("a1")]),("pack.b",[item("b1")])],lock_timeout=.02,lock_trace=trace.append)
    env.cart.add_to_cart("pack.a","a1",1); env.cart.add_to_cart("pack.b","b1",1)
    trace.clear()
    env.access.acquire_write("pack.b")
    try:
        with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
        assert exc.value.code=="PACK_BUSY"
    finally: env.access.release_write("pack.b")
    assert "sqlite_transaction_begin" not in trace
    # pack.a read lock was released after pack.b timeout
    env.access.acquire_write("pack.a",timeout=.01); env.access.release_write("pack.a")
    assert env.users.order_count()==0 and len(env.users.list_cart())==2


def test_history_uses_snapshot_not_current_pack_after_update_disable(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1",name="Old Name",price={"significand":"1","exponent":"0"})])])
    env.cart.add_to_cart("pack.one","i1",1); order=env.checkout.checkout(new_uuid_v4()).order
    env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.one",version="1.1",items=[item("i1",name="New Name",price={"significand":"9","exponent":"9"})]))
    env.lifecycle.set_pack_enabled("pack.one",False)
    detail=env.history.get_order_detail(order.order_id)
    assert detail.lines[0].name=="Old Name"
    assert detail.lines[0].unit_price==MoneyValue.from_literal(MoneyLiteral("1","0"))


def test_history_image_missing_corrupt_digest_mismatch_nonfatal_no_current_fallback(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); order=env.checkout.checkout(new_uuid_v4()).order
    line=env.users.list_order_items(order.order_id)[0]
    image=env.snapshots.resolve_history_path(line.primary_image_snapshot_path)
    image.unlink()
    d=env.history.get_order_detail(order.order_id)
    assert d.lines[0].snapshot_image_available is False and d.lines[0].snapshot_image_ref is None and d.lines[0].name=="i1"
    # Current pack asset still exists, proving there was no fallback.
    assert (env.paths.installed_packs/"pack.one"/"assets"/"a.png").exists()


def test_history_digest_mismatch_nonfatal(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); order=env.checkout.checkout(new_uuid_v4()).order
    line=env.users.list_order_items(order.order_id)[0]
    image=env.snapshots.resolve_history_path(line.primary_image_snapshot_path)
    image.write_bytes(image.read_bytes()+b"x")
    d=env.history.get_order_detail(order.order_id)
    assert d.lines[0].snapshot_image_available is False and d.lines[0].name=="i1"


def test_history_zero_pagination_and_unknown(tmp_path):
    env=setup_phase5(tmp_path)
    assert env.history.get_order_history().total==0
    with pytest.raises(DomainError) as exc: env.history.get_order_detail(new_uuid_v4())
    assert exc.value.code=="ORDER_NOT_FOUND"
    with pytest.raises(DomainError): env.history.get_order_history(page=0)


def test_statistics_zero_and_sparse_exact_no_sql_sum_dependency(tmp_path):
    env=setup_phase5(tmp_path)
    z=env.stats.get_statistics(); assert z.total_amount.is_zero and z.order_count==0 and z.total_quantity==0
    a=MoneyValue.from_literal(MoneyLiteral("1","9000000000000000000")); b=MoneyValue.from_literal(MoneyLiteral("7","0"))
    env.users.insert_order(Order(new_uuid_v4(),"2026-01-01T00:00:00.000Z",a,9223372036854775807,1))
    # DDL total_quantity is INTEGER; a single max value is valid. A second small
    # value makes SQLite SUM(total_quantity) overflow, while Python aggregation remains exact.
    env.users.insert_order(Order(new_uuid_v4(),"2026-01-02T00:00:00.000Z",b,1,1))
    s=env.stats.get_statistics()
    assert s.total_amount.block_count==2 and s.order_count==2 and s.total_quantity==9223372036854775808


def _set_old(path: Path, hours=25):
    ts=(datetime.now(timezone.utc)-timedelta(hours=hours)).timestamp(); os.utime(path,(ts,ts))


def test_snapshot_orphan_cleanup_age_reference_and_unrelated(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1); order=env.checkout.checkout(new_uuid_v4()).order
    referenced=env.paths.snapshot_images/order.order_id; _set_old(referenced)
    old_pending=env.paths.snapshot_pending/new_uuid_v4(); old_pending.mkdir(); _set_old(old_pending)
    fresh_final=env.paths.snapshot_images/new_uuid_v4(); fresh_final.mkdir()
    old_final=env.paths.snapshot_images/new_uuid_v4(); old_final.mkdir(); _set_old(old_final)
    unrelated=env.paths.snapshot_images/"not-managed"; unrelated.mkdir(); _set_old(unrelated)
    removed=SnapshotOrphanCleaner(env.paths,env.users).cleanup()
    assert old_pending in removed and old_final in removed
    assert referenced.exists() and fresh_final.exists() and unrelated.exists()


def test_snapshot_cleanup_failure_nonfatal(tmp_path,monkeypatch):
    env=setup_phase5(tmp_path)
    old=env.paths.snapshot_pending/new_uuid_v4(); old.mkdir(); _set_old(old)
    monkeypatch.setattr("fantasy_store.snapshot.cleanup.shutil.rmtree",lambda p: (_ for _ in ()).throw(PermissionError("locked")))
    assert SnapshotOrphanCleaner(env.paths,env.users).cleanup()==()
    assert old.exists()


def test_pack_service_states_and_enable_delegation(tmp_path):
    env=setup_phase5(tmp_path)
    assert env.pack_service.get_packs().pack_state=="NO_PACKS"
    env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.one",items=[item("i1")]))
    assert env.pack_service.get_packs().pack_state=="READY"
    p=env.pack_service.set_pack_enabled("pack.one",False)
    assert p.enabled is False and env.pack_service.get_packs().pack_state=="ALL_PACKS_DISABLED"
    assert (env.paths.installed_packs/"pack.one").is_dir() and env.packs.count_items_for_pack("pack.one")==1
