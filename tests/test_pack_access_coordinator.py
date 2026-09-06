from __future__ import annotations

import threading
import time
import pytest

from fantasy_store.domain.errors import PackBusyError
from fantasy_store.pack.access_coordinator import PackAccessCoordinator


def test_multiple_readers_can_enter():
    c = PackAccessCoordinator()
    entered=[]
    barrier=threading.Barrier(3)
    def reader():
        with c.read_lock("p"):
            entered.append(1); barrier.wait(); barrier.wait()
    ts=[threading.Thread(target=reader) for _ in range(2)]
    for t in ts:t.start()
    barrier.wait(timeout=1)
    assert len(entered)==2
    barrier.wait(timeout=1)
    for t in ts:t.join(1)


def test_writer_waits_for_reader_then_enters():
    c=PackAccessCoordinator(); reader_ready=threading.Event(); release=threading.Event(); writer_entered=threading.Event()
    def r():
        with c.read_lock("p"):
            reader_ready.set(); release.wait(1)
    def w():
        with c.write_lock("p", timeout=1): writer_entered.set()
    tr=threading.Thread(target=r); tr.start(); assert reader_ready.wait(1)
    tw=threading.Thread(target=w); tw.start(); time.sleep(.03); assert not writer_entered.is_set()
    release.set(); assert writer_entered.wait(1); tr.join(1); tw.join(1)


def test_writer_blocks_reader_and_timeout_returns_pack_busy():
    c=PackAccessCoordinator()
    with c.write_lock("p"):
        with pytest.raises(PackBusyError): c.acquire_read("p", timeout=.01)


def test_exception_releases_write_lock():
    c=PackAccessCoordinator()
    with pytest.raises(RuntimeError):
        with c.write_lock("p"):
            raise RuntimeError("x")
    with c.read_lock("p", timeout=.1): pass


def test_unrelated_packs_do_not_block_each_other():
    c=PackAccessCoordinator()
    with c.write_lock("a"):
        with c.read_lock("b", timeout=.05): pass


def test_waiting_writer_blocks_new_readers_writer_preference():
    c=PackAccessCoordinator(); first=threading.Event(); release=threading.Event(); writer=threading.Event(); second=threading.Event()
    def r1():
        with c.read_lock("p"):
            first.set(); release.wait(1)
    def w():
        with c.write_lock("p", timeout=1):
            writer.set(); time.sleep(.03)
    def r2():
        with c.read_lock("p", timeout=1): second.set()
    t1=threading.Thread(target=r1); t1.start(); assert first.wait(1)
    tw=threading.Thread(target=w); tw.start(); time.sleep(.02)
    t2=threading.Thread(target=r2); t2.start(); time.sleep(.02); assert not second.is_set()
    release.set(); assert writer.wait(1); tw.join(1); assert second.wait(1)
    t1.join(1); t2.join(1)


def test_recovery_required_blocks_future_pack_access():
    from fantasy_store.domain.errors import PackRecoveryRequiredError
    c=PackAccessCoordinator(); c.mark_recovery_required('p')
    with pytest.raises(PackRecoveryRequiredError): c.acquire_read('p',timeout=.01)
    with pytest.raises(PackRecoveryRequiredError): c.acquire_write('p',timeout=.01)
    c.clear_recovery_required('p')
    with c.read_lock('p',timeout=.05): pass
