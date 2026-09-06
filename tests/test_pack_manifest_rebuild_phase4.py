from __future__ import annotations

import json
import sqlite3

import pytest

from fantasy_store.domain.errors import DatabaseVersionUnsupportedError
from fantasy_store.pack.manifest_state import PackManifestStateStore
from fantasy_store.pack.recovery import PackRecoveryManager
from fantasy_store.persistence.migration import PACK_DB_NAME, initialize_database
from fantasy_store.persistence.pack_repository import PackRepository
from tests.phase4_helpers import install_old


def _corrupt_db(path):
    for suffix in ('', '-wal', '-shm'):
        p = type(path)(str(path)+suffix)
        if p.exists():
            p.unlink()
    path.write_bytes(b'not sqlite')


def test_manifest_db_corruption_rebuild_restores_enabled_only_same_digest(tmp_path):
    paths, repo, manager = install_old(tmp_path, enabled=True)
    before = repo.get_installed_pack('demo.pack')
    assert before and paths.pack_manifest_state_backup.exists()
    _corrupt_db(paths.pack_db)
    rec = PackRecoveryManager(paths, retry_delay=0, sleep=lambda _:None)
    assert rec.ensure_manifest_database() is True
    after = rec.repository.get_installed_pack('demo.pack')
    assert after is not None
    assert after.is_enabled is True
    assert after.content_digest == before.content_digest
    assert rec.repository.count_items_for_pack('demo.pack') == 1
    assert any(paths.pack_recovery_hold.iterdir())


def test_manifest_db_rebuild_without_state_backup_disables_all(tmp_path):
    paths, repo, manager = install_old(tmp_path, enabled=True)
    paths.pack_manifest_state_backup.unlink()
    _corrupt_db(paths.pack_db)
    rec = PackRecoveryManager(paths, retry_delay=0, sleep=lambda _:None)
    rec.ensure_manifest_database()
    after = rec.repository.get_installed_pack('demo.pack')
    assert after is not None and after.is_enabled is False


def test_manifest_db_rebuild_digest_mismatch_disables_pack(tmp_path):
    paths, repo, manager = install_old(tmp_path, enabled=True)
    data=json.loads(paths.pack_manifest_state_backup.read_text(encoding='utf-8'))
    data['packs'][0]['content_digest']='0'*64
    paths.pack_manifest_state_backup.write_text(json.dumps(data),encoding='utf-8')
    _corrupt_db(paths.pack_db)
    rec=PackRecoveryManager(paths,retry_delay=0,sleep=lambda _:None); rec.ensure_manifest_database()
    assert rec.repository.get_installed_pack('demo.pack').is_enabled is False


def test_manifest_db_rebuild_invalid_state_backup_disables_pack(tmp_path):
    paths, repo, manager = install_old(tmp_path, enabled=True)
    paths.pack_manifest_state_backup.write_text('{bad',encoding='utf-8')
    _corrupt_db(paths.pack_db)
    rec=PackRecoveryManager(paths,retry_delay=0,sleep=lambda _:None); rec.ensure_manifest_database()
    assert rec.repository.get_installed_pack('demo.pack').is_enabled is False


def test_manifest_db_rebuild_skips_invalid_installed_pack(tmp_path):
    paths, repo, manager = install_old(tmp_path)
    # Add an invalid orphan installed directory; it must not be registered.
    bad=paths.installed_packs/'bad.pack'; bad.mkdir(); (bad/'junk').write_text('x')
    _corrupt_db(paths.pack_db)
    rec=PackRecoveryManager(paths,retry_delay=0,sleep=lambda _:None); rec.ensure_manifest_database()
    assert rec.repository.get_installed_pack('demo.pack') is not None
    assert rec.repository.get_installed_pack('bad.pack') is None
    assert bad.exists()


def test_manifest_consistency_rebuilds_stale_items_master_only(tmp_path):
    paths, repo, manager = install_old(tmp_path)
    row=repo.get_installed_pack('demo.pack')
    conn=sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE items_master SET item_name='stale' WHERE pack_id='demo.pack'"); conn.commit(); conn.close()
    rec=PackRecoveryManager(paths,repository=repo,retry_delay=0,sleep=lambda _:None)
    rec.verify_manifest_consistency()
    assert repo.list_items_for_pack('demo.pack')[0]['item_name'] != 'stale'
    assert repo.get_installed_pack('demo.pack') == row


def test_future_manifest_schema_is_not_rebuilt_as_corruption(tmp_path):
    paths, repo, manager = install_old(tmp_path)
    conn=sqlite3.connect(paths.pack_db)
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2"); conn.commit(); conn.close()
    before=paths.pack_db.read_bytes()
    rec=PackRecoveryManager(paths,retry_delay=0,sleep=lambda _:None)
    with pytest.raises(DatabaseVersionUnsupportedError): rec.ensure_manifest_database()
    assert paths.pack_db.read_bytes()==before


def test_user_data_unchanged_by_manifest_rebuild(tmp_path):
    from fantasy_store.persistence.migration import USER_DB_NAME
    from fantasy_store.runtime.paths import AppPaths
    paths, repo, manager = install_old(tmp_path)
    initialize_database(paths.user_db, USER_DB_NAME)
    conn=sqlite3.connect(paths.user_db); conn.execute("INSERT INTO user_settings VALUES('x','1','t')"); conn.commit(); conn.close()
    before=paths.user_db.read_bytes()
    _corrupt_db(paths.pack_db)
    rec=PackRecoveryManager(paths,retry_delay=0,sleep=lambda _:None); rec.ensure_manifest_database()
    assert paths.user_db.read_bytes()==before
