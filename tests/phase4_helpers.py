from __future__ import annotations

import os
from pathlib import Path

from fantasy_store.pack.importer import PackImporter
from fantasy_store.pack.journal import PackJournalStore, PackOperationState
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.persistence.migration import PACK_DB_NAME, USER_DB_NAME, initialize_database
from fantasy_store.persistence.pack_repository import PackRepository
from fantasy_store.runtime.paths import AppPaths
from tests.phase3_helpers import base_items, base_pack, image_bytes, write_vpack


def setup_phase4(tmp_path: Path, *, with_user_db: bool = False) -> tuple[AppPaths, PackRepository]:
    paths = AppPaths(tmp_path / "data")
    paths.create_bootstrap_minimum()
    paths.create_application_dirs()
    initialize_database(paths.pack_db, PACK_DB_NAME)
    if with_user_db:
        initialize_database(paths.user_db, USER_DB_NAME)
    return paths, PackRepository(paths.pack_db)


def make_vpack(tmp_path: Path, *, version: str = "1.0", item_id: str = "item-1", pack_id: str = "demo.pack", name: str | None = None):
    pack = base_pack(pack_id)
    pack["version"] = version
    if name is not None:
        pack["name"] = name
    items = base_items(item_id=item_id)
    return write_vpack(
        tmp_path / f"{pack_id}-{version}-{item_id}.vpack",
        pack=pack,
        items=items,
        assets={"assets/a.png": image_bytes("PNG")},
    )


def install_old(tmp_path: Path, *, enabled: bool = True):
    paths, repo = setup_phase4(tmp_path)
    src = make_vpack(tmp_path, version="1.0", item_id="old-item")
    manager = PackLifecycleManager(paths, repository=repo, move_sleep=lambda _: None)
    manager.import_vpack(src)
    if not enabled:
        manager.set_pack_enabled("demo.pack", False)
    # Old completed journal is irrelevant to crash fixture setup.
    for p in paths.pack_operations.glob("*.json"):
        p.unlink()
    return paths, repo, manager


def prepare_update_operation(tmp_path: Path, *, state: PackOperationState = PackOperationState.READY_TO_SWITCH):
    paths, repo, manager = install_old(tmp_path)
    old = repo.get_installed_pack("demo.pack")
    assert old is not None
    resolver = PackPathResolver(paths)
    journals = PackJournalStore(paths)
    from fantasy_store.domain.ids import new_uuid_v4
    opid = new_uuid_v4()
    staging = resolver.staging_root(opid)
    journal = journals.create(opid, staging_path=staging)
    src = make_vpack(tmp_path, version="1.1", item_id="new-item", name="Demo v2")
    prepared = PackImporter(paths).prepare(src, existing_pack_ids={"demo.pack"}, operation_id=opid)
    installed = resolver.installed_pack_root("demo.pack")
    backup = resolver.backup_pack_root(opid, "demo.pack")
    backup.parent.mkdir(parents=True, exist_ok=True)
    journal = journals.transition(
        journal,
        PackOperationState.VALIDATED,
        operation_type="UPDATE",
        pack_id="demo.pack",
        old_digest=old.content_digest,
        new_digest=prepared.content_digest,
        old_version=old.pack_version,
        new_version=prepared.pack_metadata["version"],
        installed_path=resolver.relative_to_persistent(installed),
        backup_path=resolver.relative_to_persistent(backup),
    )
    if state != PackOperationState.VALIDATED:
        journal = journals.transition(journal, state)
    return paths, repo, prepared, old, journals, journal, installed, staging, backup


def switch_db_to_new(repo: PackRepository, prepared, installed: Path) -> None:
    md = prepared.pack_metadata
    repo.replace_pack_with_items(
        md["pack_id"],
        pack_name=md["name"],
        pack_version=md["version"],
        author=md["author"],
        description=md["description"],
        schema_version=int(md["schema_version"]),
        content_digest=prepared.content_digest,
        install_dir=f"packs/installed/{md['pack_id']}",
        updated_at="2026-09-05T00:00:00.000Z",
        items=prepared.prepared_items_master,
    )
