from __future__ import annotations

import os
import pytest

from fantasy_store.domain.errors import PackFileLockedError, PackRecoveryRequiredError, PackOperationError
from fantasy_store.pack.file_ops import replace_with_retry
from fantasy_store.pack.journal import PackJournalStore
from fantasy_store.pack.recovery import PackRecoveryManager
from tests.phase4_helpers import install_old, make_vpack, prepare_update_operation


def test_replace_retry_succeeds_after_two_permission_failures(tmp_path, monkeypatch):
    src=tmp_path/'src'; dst=tmp_path/'dst'; src.write_text('x')
    import fantasy_store.pack.file_ops as mod
    real=os.replace; calls={'n':0}
    def flaky(a,b):
        calls['n']+=1
        if calls['n']<3: raise PermissionError('locked')
        return real(a,b)
    monkeypatch.setattr(mod.os,'replace',flaky)
    replace_with_retry(src,dst,attempts=3,base_delay=0,sleep=lambda _:None)
    assert calls['n']==3 and dst.read_text()=='x'


def test_old_to_backup_failure_keeps_old_generation(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path)
    old=repo.get_installed_pack('demo.pack')
    monkeypatch.setattr(manager,'_retry_move',lambda *a,**k: (_ for _ in ()).throw(PermissionError('locked')))
    with pytest.raises(PackFileLockedError): manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    assert repo.get_digest('demo.pack')==old.content_digest
    assert (paths.installed_packs/'demo.pack').is_dir()


def test_new_to_installed_failure_rolls_old_back(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path); old=repo.get_installed_pack('demo.pack')
    real=manager._retry_move; calls={'n':0}
    def sequence(src,dst,pack_id):
        calls['n']+=1
        if calls['n']==2: raise PermissionError('new locked')
        return real(src,dst,pack_id=pack_id)
    monkeypatch.setattr(manager,'_retry_move',sequence)
    with pytest.raises(PackFileLockedError): manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    assert repo.get_digest('demo.pack')==old.content_digest
    assert (paths.installed_packs/'demo.pack').is_dir()


def test_new_to_installed_and_rollback_failure_marks_recovery_required(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path)
    real=manager._retry_move; calls={'n':0}
    def sequence(src,dst,pack_id):
        calls['n']+=1
        if calls['n']>=2: raise PermissionError('locked')
        return real(src,dst,pack_id=pack_id)
    monkeypatch.setattr(manager,'_retry_move',sequence)
    with pytest.raises(PackRecoveryRequiredError): manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    # The injected failure only affects the lifecycle move helper. Immediate
    # observation-based recovery uses its own move path and may safely restore
    # old before the exception returns; persistent recovery rename failure is
    # tested separately below.
    assert repo.get_installed_pack('demo.pack') is not None


def test_db_begin_failure_rolls_files_back_to_old(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path); old=repo.get_installed_pack('demo.pack')
    import fantasy_store.persistence.pack_repository as mod
    monkeypatch.setattr(mod,'begin_immediate',lambda conn: (_ for _ in ()).throw(RuntimeError('begin fail')))
    with pytest.raises(RuntimeError): manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    assert repo.get_digest('demo.pack')==old.content_digest
    from fantasy_store.pack.digest import compute_content_digest
    assert compute_content_digest(paths.installed_packs/'demo.pack')==old.content_digest


def test_recovery_rename_failure_becomes_recovery_required(tmp_path, monkeypatch):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    backup.parent.mkdir(parents=True,exist_ok=True); os.replace(installed,backup)
    mgr=PackRecoveryManager(paths,repository=repo,retry_delay=0,sleep=lambda _:None)
    monkeypatch.setattr(mgr,'_move',lambda *a,**k: (_ for _ in ()).throw(PackRecoveryRequiredError('demo.pack','rename fail')))
    result=mgr.recover_operation(journal)
    assert result.final_state=='RECOVERY_REQUIRED'
    assert journals.load(journal.operation_id).state=='RECOVERY_REQUIRED'


def test_digest_observation_failure_does_not_guess_generation(tmp_path, monkeypatch):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    import fantasy_store.pack.recovery as mod
    monkeypatch.setattr(mod,'compute_content_digest',lambda p: (_ for _ in ()).throw(PermissionError('read')))
    result=PackRecoveryManager(paths,repository=repo,retry_attempts=2,retry_delay=0,sleep=lambda _:None).recover_operation(journal)
    assert result.final_state=='RECOVERY_REQUIRED'


def test_cleanup_failure_after_success_does_not_revert_update(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path)
    import fantasy_store.pack.updater as mod
    monkeypatch.setattr(mod,'remove_tree_best_effort',lambda *a,**k: False)
    prepared=manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    assert repo.get_digest('demo.pack')==prepared.content_digest


def test_replace_retry_all_fail_preserves_source(tmp_path, monkeypatch):
    src=tmp_path/'src'; dst=tmp_path/'dst'; src.write_text('x')
    import fantasy_store.pack.file_ops as mod
    monkeypatch.setattr(mod.os,'replace',lambda *a,**k: (_ for _ in ()).throw(PermissionError('locked')))
    with pytest.raises(PermissionError): replace_with_retry(src,dst,attempts=3,base_delay=0,sleep=lambda _:None)
    assert src.read_text()=='x' and not dst.exists()


def test_journal_failure_after_old_rename_is_immediately_observation_rolled_back(tmp_path, monkeypatch):
    paths, repo, manager=install_old(tmp_path); old=repo.get_installed_pack('demo.pack')
    original=manager.journals.transition
    fired={'v':False}
    def transition(j,state,**changes):
        if state.value=='OLD_BACKED_UP' and not fired['v']:
            fired['v']=True
            raise PackOperationError('injected journal failure',code='PACK_JOURNAL_WRITE_FAILED')
        return original(j,state,**changes)
    monkeypatch.setattr(manager.journals,'transition',transition)
    with pytest.raises(PackOperationError): manager.import_vpack(make_vpack(tmp_path,version='1.1',item_id='new'))
    assert repo.get_digest('demo.pack')==old.content_digest
    from fantasy_store.pack.digest import compute_content_digest
    assert compute_content_digest(paths.installed_packs/'demo.pack')==old.content_digest


def test_staging_digest_unobservable_after_ready_requires_recovery(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    (staging/'pack.json').write_bytes(b'broken')
    result=PackRecoveryManager(paths,repository=repo,retry_attempts=1,retry_delay=0,sleep=lambda _:None).recover_operation(journal)
    assert result.final_state=='RECOVERY_REQUIRED'
