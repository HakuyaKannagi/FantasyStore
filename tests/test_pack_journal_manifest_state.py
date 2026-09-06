from __future__ import annotations

import json
import os
import pytest

from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.domain.errors import PackOperationError
from fantasy_store.pack.journal import PackJournalStore, PackOperationState
from fantasy_store.pack.manifest_state import PackManifestStateStore
from fantasy_store.pack.path_resolver import PackPathResolver
from tests.phase4_helpers import setup_phase4, install_old


def test_journal_atomic_write_and_load(tmp_path):
    paths,_=setup_phase4(tmp_path); store=PackJournalStore(paths); op=new_uuid_v4(); resolver=PackPathResolver(paths)
    j=store.create(op, staging_path=resolver.staging_root(op))
    j=store.transition(j, PackOperationState.VALIDATING)
    loaded=store.load(op)
    assert loaded.state=="VALIDATING" and loaded.operation_id==op
    assert not store.path_for(op).with_suffix('.json.tmp').exists()


def test_journal_replace_failure_does_not_replace_final(tmp_path, monkeypatch):
    paths,_=setup_phase4(tmp_path); store=PackJournalStore(paths); op=new_uuid_v4(); resolver=PackPathResolver(paths)
    j=store.create(op, staging_path=resolver.staging_root(op)); before=store.path_for(op).read_bytes()
    import fantasy_store.pack.journal as mod
    monkeypatch.setattr(mod.os, "replace", lambda *a,**k: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(PackOperationError): store.transition(j, PackOperationState.VALIDATING)
    assert store.path_for(op).read_bytes()==before
    assert store.path_for(op).with_suffix('.json.tmp').exists()


def test_manifest_state_generation_and_load(tmp_path):
    paths,repo,manager=install_old(tmp_path)
    store=PackManifestStateStore(paths.pack_manifest_state_backup)
    state=store.load(); e=state['demo.pack']
    assert e.is_enabled is True and len(e.content_digest)==64


def test_manifest_state_unknown_version_rejected(tmp_path):
    paths,_=setup_phase4(tmp_path); p=paths.pack_manifest_state_backup; p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps({"format_version":2,"generated_at":"x","packs":[]}),encoding='utf-8')
    with pytest.raises(PackOperationError): PackManifestStateStore(p).load()


def test_orphan_journal_tmp_without_final_is_left_untouched(tmp_path):
    from fantasy_store.pack.recovery import PackRecoveryManager
    paths,repo=setup_phase4(tmp_path)
    tmp=paths.pack_operations/'deadbeef.json.tmp'; tmp.write_text('{}',encoding='utf-8')
    PackRecoveryManager(paths,repository=repo,retry_delay=0,sleep=lambda _:None).cleanup_orphans()
    assert tmp.exists()


def test_journal_tmp_with_final_is_safe_cleanup(tmp_path):
    from fantasy_store.pack.recovery import PackRecoveryManager
    paths,repo=setup_phase4(tmp_path); store=PackJournalStore(paths); op=new_uuid_v4(); resolver=PackPathResolver(paths)
    store.create(op,staging_path=resolver.staging_root(op))
    tmp=store.path_for(op).with_suffix('.json.tmp'); tmp.write_text('partial',encoding='utf-8')
    PackRecoveryManager(paths,repository=repo,retry_delay=0,sleep=lambda _:None).cleanup_orphans()
    assert not tmp.exists()


def test_manifest_state_write_failure_leaves_existing_final(tmp_path, monkeypatch):
    paths,repo,manager=install_old(tmp_path)
    final=paths.pack_manifest_state_backup; before=final.read_bytes()
    store=PackManifestStateStore(final)
    import fantasy_store.pack.manifest_state as mod
    monkeypatch.setattr(mod.os,'replace',lambda *a,**k: (_ for _ in ()).throw(OSError('replace')))
    with pytest.raises(PackOperationError): store.write_repository_state(repo)
    assert final.read_bytes()==before
    assert final.with_suffix('.json.tmp').exists()
