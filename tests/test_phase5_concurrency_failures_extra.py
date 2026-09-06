from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from fantasy_store.domain.errors import DomainError, ValidationError
from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.pack.updater import PackLifecycleManager
from tests.phase5_helpers import item, make_pack_file, setup_phase5


@pytest.mark.parametrize("operation", ["update", "remove", "clear"])
def test_cart_mutation_waits_while_checkout_holds_cart_lock(tmp_path, operation):
    entered=threading.Event(); release=threading.Event()
    def hook(stage):
        if stage=="before_snapshot":
            entered.set(); release.wait(5)
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1"),item("i2")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.one","i1",1)
    if operation=="update": env.cart.add_to_cart("pack.one","i2",1)
    checkout_done=[]
    tc=threading.Thread(target=lambda: checkout_done.append(env.checkout.checkout(new_uuid_v4())))
    tc.start(); assert entered.wait(5)
    started=threading.Event(); done=threading.Event(); errors=[]
    def mutate():
        started.set()
        try:
            if operation=="update": env.cart.update_cart_item("pack.one","i2",2)
            elif operation=="remove": env.cart.remove_cart_item("pack.one","i1")
            else: env.cart.clear_cart()
        except Exception as exc: errors.append(exc)
        finally: done.set()
    tm=threading.Thread(target=mutate); tm.start(); assert started.wait(2); time.sleep(.05)
    assert not done.is_set()
    release.set(); tc.join(5); tm.join(5)
    assert done.is_set()
    # update can legitimately fail after checkout removed the row; the key
    # invariant is serialization, not a stale pre-checkout mutation.
    if operation=="update": assert errors and isinstance(errors[0],ValidationError)


def test_add_to_cart_pack_writer_timeout_returns_pack_busy(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],lock_timeout=.02)
    env.access.acquire_write("pack.one")
    try:
        with pytest.raises(DomainError) as exc: env.cart.add_to_cart("pack.one","i1",1)
        assert exc.value.code=="PACK_BUSY"
    finally: env.access.release_write("pack.one")
    assert env.users.list_cart()==[]


def test_add_to_cart_lock_order_trace_cart_pack_sqlite(tmp_path):
    trace=[]
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],lock_trace=trace.append)
    trace.clear(); env.cart.add_to_cart("pack.one","i1",1)
    assert trace.index("cart_acquired") < trace.index("pack_read_acquired:pack.one") < trace.index("sqlite_transaction_begin")
    assert trace.index("pack_read_released:pack.one") < trace.index("cart_released")


def test_update_already_holding_writer_makes_checkout_use_whole_new_generation(tmp_path):
    old_backed=threading.Event(); release=threading.Event()
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1",name="Old",price={"significand":"1","exponent":"0"})])],lock_timeout=2)
    env.cart.add_to_cart("pack.one","i1",1)
    def lifecycle_hook(stage):
        if stage=="OLD_RENAME_DONE":
            old_backed.set(); release.wait(5)
    updater=PackLifecycleManager(
        env.paths, repository=env.packs, coordinator=env.access,
        move_sleep=lambda _:None, failure_hook=lifecycle_hook, lock_timeout=2,
    )
    update_errors=[]
    tu=threading.Thread(target=lambda: updater.import_vpack(make_pack_file(tmp_path,"pack.one",version="1.1",items=[item("i1",name="New",price={"significand":"9","exponent":"3"})])))
    tu.start(); assert old_backed.wait(5)
    result=[]; errors=[]
    tc=threading.Thread(target=lambda: result.append(env.checkout.checkout(new_uuid_v4())))
    tc.start(); time.sleep(.05); assert not result and not errors
    release.set(); tu.join(5); tc.join(5)
    assert result and result[0].order.lines[0].name=="New"
    assert result[0].order.lines[0].unit_price.to_canonical_obj()=={"terms":[{"significand":"9000","exponent":"0"}]}


def test_checkout_pack_a_does_not_block_unrelated_pack_b_update(tmp_path):
    entered=threading.Event(); release=threading.Event()
    def hook(stage):
        if stage=="before_snapshot": entered.set(); release.wait(5)
    env=setup_phase5(tmp_path,packs=[("pack.a",[item("a1")]),("pack.b",[item("b1")])],checkout_failure_hook=hook)
    env.cart.add_to_cart("pack.a","a1",1)
    tc=threading.Thread(target=lambda: env.checkout.checkout(new_uuid_v4()))
    tc.start(); assert entered.wait(5)
    done=threading.Event()
    tb=threading.Thread(target=lambda:(env.lifecycle.import_vpack(make_pack_file(tmp_path,"pack.b",version="1.1",items=[item("b1",name="B2")])),done.set()))
    tb.start(); tb.join(3)
    assert done.is_set() and env.packs.get_installed_pack("pack.b").pack_version=="1.1"
    release.set(); tc.join(5)


@pytest.mark.parametrize("failure_kind", ["copy", "validate", "hash"])
def test_snapshot_pending_failures_keep_cart_and_no_db(tmp_path, monkeypatch, failure_kind):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",1)
    if failure_kind=="copy":
        monkeypatch.setattr("fantasy_store.snapshot.manager.shutil.copyfileobj",lambda *a,**k: (_ for _ in ()).throw(OSError("read/write fail")))
    elif failure_kind=="validate":
        original=env.snapshots.image_validator.validate; calls={"n":0}
        def fail_second(path):
            calls["n"]+=1
            if calls["n"]>=2: raise OSError("decode fail")
            return original(path)
        monkeypatch.setattr(env.snapshots.image_validator,"validate",fail_second)
    else:
        monkeypatch.setattr(env.snapshots,"_hash_file",lambda p: (_ for _ in ()).throw(OSError("hash fail")))
    with pytest.raises(Exception): env.checkout.checkout(new_uuid_v4())
    assert env.users.order_count()==0 and len(env.users.list_cart())==1


def test_snapshot_pending_create_failure_keeps_cart(tmp_path,monkeypatch):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    original=Path.mkdir
    def fail(path,*a,**k):
        if ".pending" in str(path): raise PermissionError("denied")
        return original(path,*a,**k)
    monkeypatch.setattr(Path,"mkdir",fail)
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="FS_ACCESS_DENIED"
    assert env.users.order_count()==0 and len(env.users.list_cart())==1


def test_history_corrupt_image_nonfatal(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    order=env.checkout.checkout(new_uuid_v4()).order
    snap=env.users.list_order_items(order.order_id)[0]
    path=env.snapshots.resolve_history_path(snap.primary_image_snapshot_path)
    path.write_bytes(b"not-image")
    d=env.history.get_order_detail(order.order_id)
    assert d.lines[0].snapshot_image_available is False and d.lines[0].name=="i1"


def test_history_pagination_multiple_orders(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    for _ in range(3):
        env.cart.add_to_cart("pack.one","i1",1); env.checkout.checkout(new_uuid_v4())
    p1=env.history.get_order_history(page=1,page_size=2); p2=env.history.get_order_history(page=2,page_size=2)
    assert p1.total==3 and len(p1.orders)==2 and len(p2.orders)==1


def test_pack_service_busy_flag(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.access.acquire_write("pack.one")
    try: assert env.pack_service.get_packs().packs[0].busy is True
    finally: env.access.release_write("pack.one")


def test_snapshot_cleanup_keeps_referenced_even_if_corrupt(tmp_path):
    from datetime import datetime,timedelta,timezone
    import os
    from fantasy_store.snapshot.cleanup import SnapshotOrphanCleaner
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    order=env.checkout.checkout(new_uuid_v4()).order
    root=env.paths.snapshot_images/order.order_id
    snap=env.users.list_order_items(order.order_id)[0]
    path=env.snapshots.resolve_history_path(snap.primary_image_snapshot_path); path.write_bytes(b"corrupt")
    old=(datetime.now(timezone.utc)-timedelta(hours=25)).timestamp(); os.utime(root,(old,old))
    SnapshotOrphanCleaner(env.paths,env.users).cleanup()
    assert root.exists()


def test_checkout_corrupt_source_image_maps_to_checkout_unavailable(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    source=env.paths.installed_packs/"pack.one"/"assets"/"a.png"; source.write_bytes(b"corrupt")
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="CHECKOUT_ITEM_UNAVAILABLE"
    assert env.users.order_count()==0 and len(env.users.list_cart())==1


def test_checkout_db_begin_failure_keeps_cart_and_cleans_final(tmp_path,monkeypatch):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    monkeypatch.setattr("fantasy_store.persistence.user_repository.begin_immediate",lambda conn: (_ for _ in ()).throw(OSError("begin fail")))
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="DB_WRITE_FAILED"
    assert env.users.order_count()==0 and len(env.users.list_cart())==1 and list(env.paths.snapshot_images.iterdir())==[]


def test_history_unreadable_image_nonfatal(tmp_path,monkeypatch):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    order=env.checkout.checkout(new_uuid_v4()).order
    monkeypatch.setattr(env.snapshots.image_validator,"validate",lambda p: (_ for _ in ()).throw(PermissionError("unreadable")))
    d=env.history.get_order_detail(order.order_id)
    assert d.lines[0].snapshot_image_available is False and d.lines[0].name=="i1"


def test_snapshot_cleanup_keeps_fresh_pending(tmp_path):
    from fantasy_store.snapshot.cleanup import SnapshotOrphanCleaner
    env=setup_phase5(tmp_path)
    fresh=env.paths.snapshot_pending/new_uuid_v4(); fresh.mkdir()
    SnapshotOrphanCleaner(env.paths,env.users).cleanup()
    assert fresh.exists()


def test_cart_checkout_coordinator_releases_after_exception(tmp_path):
    env=setup_phase5(tmp_path)
    with pytest.raises(RuntimeError):
        with env.cart_lock.exclusive():
            raise RuntimeError("boom")
    with env.cart_lock.exclusive():
        pass


def test_snapshot_disk_full_maps_to_frozen_fs_error(tmp_path,monkeypatch):
    import errno
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])]); env.cart.add_to_cart("pack.one","i1",1)
    monkeypatch.setattr("fantasy_store.snapshot.manager.shutil.copyfileobj",lambda *a,**k: (_ for _ in ()).throw(OSError(errno.ENOSPC,"full")))
    with pytest.raises(DomainError) as exc: env.checkout.checkout(new_uuid_v4())
    assert exc.value.code=="FS_DISK_FULL"
    assert env.users.order_count()==0 and len(env.users.list_cart())==1
