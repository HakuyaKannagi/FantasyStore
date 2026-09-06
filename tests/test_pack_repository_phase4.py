from __future__ import annotations

import pytest

from fantasy_store.persistence.pack_repository import PackRepository
from tests.phase4_helpers import install_old, make_vpack
from fantasy_store.pack.updater import PackLifecycleManager


def test_new_import_inserts_pack_items_digest_and_manifest_state(tmp_path):
    from tests.phase4_helpers import setup_phase4
    paths, repo = setup_phase4(tmp_path)
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    prepared = manager.import_vpack(make_vpack(tmp_path, version="1.0", item_id="i1"))
    row = repo.get_installed_pack("demo.pack")
    assert row is not None
    assert row.content_digest == prepared.content_digest
    assert row.is_enabled is True
    assert repo.count_items_for_pack("demo.pack") == 1
    assert (paths.installed_packs / "demo.pack").is_dir()
    assert paths.pack_manifest_state_backup.is_file()
    journal_files = list(paths.pack_operations.glob("*.json"))
    assert len(journal_files) == 1
    assert '"state":"COMPLETED"' in journal_files[0].read_text(encoding="utf-8")
    assert not any(paths.staging_packs.iterdir())


def test_update_is_complete_replace_and_preserves_enabled_and_installed_at(tmp_path):
    paths, repo, manager = install_old(tmp_path, enabled=False)
    before = repo.get_installed_pack("demo.pack")
    assert before is not None and before.is_enabled is False
    prepared = manager.import_vpack(make_vpack(tmp_path, version="1.1", item_id="new-item", name="New Name"))
    after = repo.get_installed_pack("demo.pack")
    assert after is not None
    assert after.pack_version == "1.1"
    assert after.pack_name == "New Name"
    assert after.is_enabled is False
    assert after.installed_at == before.installed_at
    assert after.updated_at != before.updated_at
    ids = [row["item_id"] for row in repo.list_items_for_pack("demo.pack")]
    assert ids == ["new-item"]
    assert after.content_digest == prepared.content_digest
    assert not any(paths.pack_backups.iterdir())


def test_repository_update_transaction_rollback_preserves_old_pack(tmp_path):
    paths, repo, manager = install_old(tmp_path)
    old = repo.get_installed_pack("demo.pack")
    old_items = [dict(row) for row in repo.list_items_for_pack("demo.pack")]
    prepared = manager.importer.prepare(
        make_vpack(tmp_path, version="1.1", item_id="new-item"),
        existing_pack_ids={"demo.pack"},
    )
    md = prepared.pack_metadata
    def fail(stage):
        if stage == "after_items_insert":
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError):
        repo.replace_pack_with_items(
            "demo.pack",
            pack_name=md["name"], pack_version=md["version"], author=md["author"],
            description=md["description"], schema_version=1,
            content_digest=prepared.content_digest,
            install_dir="packs/installed/demo.pack", updated_at="x",
            items=prepared.prepared_items_master, failure_hook=fail,
        )
    assert repo.get_installed_pack("demo.pack") == old
    assert [dict(row) for row in repo.list_items_for_pack("demo.pack")] == old_items


def test_enable_disable_keeps_files_and_items(tmp_path):
    paths, repo, manager = install_old(tmp_path)
    installed = paths.installed_packs / "demo.pack"
    before_files = sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*") if p.is_file())
    before_items = [dict(r) for r in repo.list_items_for_pack("demo.pack")]
    manager.set_pack_enabled("demo.pack", False)
    assert repo.get_installed_pack("demo.pack").is_enabled is False
    assert sorted(p.relative_to(installed).as_posix() for p in installed.rglob("*") if p.is_file()) == before_files
    assert [dict(r) for r in repo.list_items_for_pack("demo.pack")] == before_items
    manager.set_pack_enabled("demo.pack", True)
    assert repo.get_installed_pack("demo.pack").is_enabled is True


def test_manifest_backup_failure_does_not_roll_back_enable(tmp_path, monkeypatch):
    paths, repo, manager = install_old(tmp_path)
    monkeypatch.setattr(manager.manifest_state, "write_repository_state", lambda repo: (_ for _ in ()).throw(OSError("disk")))
    manager.set_pack_enabled("demo.pack", False)
    assert repo.get_installed_pack("demo.pack").is_enabled is False


def test_disable_does_not_delete_user_cart(tmp_path):
    from tests.phase4_helpers import setup_phase4
    from fantasy_store.persistence.migration import USER_DB_NAME, initialize_database
    from fantasy_store.persistence.user_repository import CartItem, UserRepository
    paths,repo=setup_phase4(tmp_path,with_user_db=True)
    manager=PackLifecycleManager(paths,repository=repo,move_sleep=lambda _:None)
    manager.import_vpack(make_vpack(tmp_path,version='1.0',item_id='i1'))
    users=UserRepository(paths.user_db)
    users.insert_cart_item(CartItem('demo.pack','i1',2,'t','t'))
    manager.set_pack_enabled('demo.pack',False)
    assert users.get_cart_item('demo.pack','i1').quantity==2


def test_phase3_staging_validation_holds_no_pack_write_lock(tmp_path, monkeypatch):
    from tests.phase4_helpers import setup_phase4
    paths,repo=setup_phase4(tmp_path)
    manager=PackLifecycleManager(paths,repository=repo,move_sleep=lambda _:None)
    original=manager.importer.prepare
    observed=[]
    def wrapped(*a,**k):
        observed.append(manager.coordinator.is_write_locked('demo.pack'))
        return original(*a,**k)
    monkeypatch.setattr(manager.importer,'prepare',wrapped)
    manager.import_vpack(make_vpack(tmp_path,version='1.0',item_id='i1'))
    assert observed==[False]
