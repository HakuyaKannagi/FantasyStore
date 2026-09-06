from __future__ import annotations

import os
from pathlib import Path

import pytest

from fantasy_store.domain.errors import PackRecoveryRequiredError
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.journal import PackOperationState
from fantasy_store.pack.recovery import PackRecoveryManager
from fantasy_store.pack.manifest_state import PackManifestStateStore
from tests.phase4_helpers import prepare_update_operation, switch_db_to_new, setup_phase4, make_vpack
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.pack.importer import PackImporter
from fantasy_store.pack.journal import PackJournalStore
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.domain.ids import new_uuid_v4


def recovery(paths, repo):
    return PackRecoveryManager(paths, repository=repo, retry_delay=0, sleep=lambda _: None)


@pytest.mark.parametrize("initial_state", [
    PackOperationState.STAGING,
    PackOperationState.VALIDATING,
    PackOperationState.VALIDATED,
    PackOperationState.READY_TO_SWITCH,
])
def test_pre_switch_states_discard_staging_keep_old(tmp_path, initial_state):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path, state=initial_state)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert compute_content_digest(installed) == old.content_digest
    assert repo.get_digest("demo.pack") == old.content_digest
    assert not staging.exists()


def test_old_rename_before_journal_update_restores_old(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path, state=PackOperationState.READY_TO_SWITCH)
    os.replace(installed, backup)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.decision == "restore_old_generation"
    assert compute_content_digest(installed) == old.content_digest
    assert not staging.exists()


def test_old_backed_up_state_restores_old(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup)
    journal = journals.transition(journal, PackOperationState.OLD_BACKED_UP)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert repo.get_digest("demo.pack") == old.content_digest
    assert compute_content_digest(installed) == old.content_digest


def test_new_rename_before_files_switched_journal_rolls_back(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup)
    journal = journals.transition(journal, PackOperationState.OLD_BACKED_UP)
    os.replace(staging, installed)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert compute_content_digest(installed) == old.content_digest
    assert repo.get_digest("demo.pack") == old.content_digest


def test_files_switched_db_old_rolls_back(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup); os.replace(staging, installed)
    journal = journals.transition(journal, PackOperationState.FILES_SWITCHED)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert compute_content_digest(installed) == old.content_digest


@pytest.mark.parametrize("state", [PackOperationState.FILES_SWITCHED, PackOperationState.DB_SWITCHED, PackOperationState.COMPLETED])
def test_db_new_installed_new_completes_success_even_if_journal_lags(tmp_path, state):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup); os.replace(staging, installed)
    switch_db_to_new(repo, prepared, installed)
    journal = journals.transition(journal, state)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "COMPLETED"
    assert repo.get_digest("demo.pack") == prepared.content_digest
    assert compute_content_digest(installed) == prepared.content_digest
    assert not backup.exists()
    assert PackJournalStore(paths).load(journal.operation_id).state == "COMPLETED"


def test_rolling_back_after_new_retreat_crash_recovers_old(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup)
    # simulate new became installed and recovery moved it back to staging
    tmp_new = staging
    os.replace(tmp_new, installed)
    journal = journals.transition(journal, PackOperationState.ROLLING_BACK)
    os.replace(installed, staging)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert compute_content_digest(installed) == old.content_digest


def test_rolling_back_after_old_restore_crash_finishes_cleanup(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup)
    journal = journals.transition(journal, PackOperationState.ROLLING_BACK)
    os.replace(backup, installed)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "ROLLED_BACK"
    assert compute_content_digest(installed) == old.content_digest
    assert not staging.exists()


def test_unknown_installed_digest_becomes_recovery_required(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    # modify a byte in old pack while DB still points to old digest
    p = installed / "pack.json"
    p.write_bytes(p.read_bytes() + b" ")
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "RECOVERY_REQUIRED"
    assert PackJournalStore(paths).load(journal.operation_id).state == "RECOVERY_REQUIRED"


def test_unknown_backup_digest_becomes_recovery_required(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    os.replace(installed, backup)
    (backup / "pack.json").write_bytes((backup / "pack.json").read_bytes() + b" ")
    journal = journals.transition(journal, PackOperationState.OLD_BACKED_UP)
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "RECOVERY_REQUIRED"


def test_db_digest_unknown_not_old_or_new_requires_recovery(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    conn = __import__('sqlite3').connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'", ('f'*64,)); conn.commit(); conn.close()
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "RECOVERY_REQUIRED"


def test_journal_path_tamper_does_not_touch_external_path(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    external = tmp_path / "keep"; external.mkdir(); (external/"x").write_text("keep")
    journal = journals.update(journal, backup_path="../keep")
    result = recovery(paths, repo).recover_operation(journal)
    assert result.final_state == "RECOVERY_REQUIRED"
    assert (external/"x").read_text()=="keep"


def test_recovery_required_is_preserved_on_next_observation(tmp_path):
    paths, repo, prepared, old, journals, journal, installed, staging, backup = prepare_update_operation(tmp_path)
    conn = __import__('sqlite3').connect(paths.pack_db)
    conn.execute("UPDATE installed_packs SET content_digest=? WHERE pack_id='demo.pack'", ('e'*64,)); conn.commit(); conn.close()
    mgr=recovery(paths, repo)
    first=mgr.recover_operation(journal); second=mgr.recover_operation(journals.load(journal.operation_id))
    assert first.final_state==second.final_state=="RECOVERY_REQUIRED"


def test_partial_staging_without_pack_id_is_safe_to_discard(tmp_path):
    paths, repo = setup_phase4(tmp_path)
    op=new_uuid_v4(); resolver=PackPathResolver(paths); staging=resolver.staging_root(op); staging.mkdir(); (staging/'partial').write_text('x')
    store=PackJournalStore(paths); journal=store.create(op,staging_path=staging)
    result=recovery(paths,repo).recover_operation(journal)
    assert result.final_state=='ROLLED_BACK' and not staging.exists() and not store.path_for(op).exists()
