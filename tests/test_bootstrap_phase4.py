from __future__ import annotations

import os
import sqlite3
import pytest

from fantasy_store.bootstrap import bootstrap_phase4
from fantasy_store.domain.errors import PackRecoveryRequiredError
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackOperationState
from tests.phase4_helpers import prepare_update_operation, setup_phase4


def test_phase4_bootstrap_fresh_pack_zero_is_normal(tmp_path):
    root=tmp_path/'data'
    runtime=bootstrap_phase4(root)
    try:
        assert runtime.user_schema_version==1
        assert runtime.pack_schema_version==1
        assert runtime.pack_recovery_decisions==()
        assert list(runtime.paths.installed_packs.iterdir())==[]
    finally:
        runtime.close()


def test_phase4_bootstrap_recovers_old_rename_journal_delay_before_ready(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    backup.parent.mkdir(parents=True,exist_ok=True)
    os.replace(installed,backup)
    # user DB does not exist yet; bootstrap creates it, then observes pack crash.
    runtime=bootstrap_phase4(paths.root)
    try:
        assert compute_content_digest(installed)==old.content_digest
        assert runtime.pack_lifecycle.repository.get_digest('demo.pack')==old.content_digest
        assert any(d.final_state=='ROLLED_BACK' for d in runtime.pack_recovery_decisions)
    finally:
        runtime.close()


def test_phase4_bootstrap_stops_on_unresolvable_recovery(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    conn=sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'",('f'*64,)); conn.commit(); conn.close()
    with pytest.raises(PackRecoveryRequiredError):
        bootstrap_phase4(paths.root)
    # Lock must have been released by failed bootstrap.
    runtime_path=paths.lock_file
    from fantasy_store.runtime.app_lock import AppInstanceLock
    lock=AppInstanceLock(runtime_path); lock.acquire(); lock.release()
