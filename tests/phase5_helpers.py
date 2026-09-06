from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fantasy_store.application.cart_checkout_coordinator import CartCheckoutCoordinator
from fantasy_store.application.cart_service import CartService
from fantasy_store.application.catalog_service import CatalogService
from fantasy_store.application.checkout_service import CheckoutService
from fantasy_store.application.history_service import HistoryService
from fantasy_store.application.pack_service import PackService
from fantasy_store.application.stats_service import StatsService
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.pack.updater import PackLifecycleManager
from fantasy_store.persistence.user_backup import UserDataBackupManager
from fantasy_store.persistence.user_repository import UserRepository
from fantasy_store.snapshot.manager import SnapshotManager
from tests.phase3_helpers import base_pack, image_bytes, write_vpack
from tests.phase4_helpers import setup_phase4


@dataclass
class Phase5Env:
    paths: object
    packs: object
    users: UserRepository
    access: PackAccessCoordinator
    lifecycle: PackLifecycleManager
    assets: AssetResolver
    cart_lock: CartCheckoutCoordinator
    snapshots: SnapshotManager
    backup: UserDataBackupManager
    catalog: CatalogService
    cart: CartService
    history: HistoryService
    stats: StatsService
    pack_service: PackService
    checkout: CheckoutService


def item(item_id: str, *, name: str | None = None, price=None, category="Category", description="Description", image="assets/a.png", attrs=None):
    return {
        "item_id": item_id,
        "name": name or item_id,
        "price": price or {"significand": "123", "exponent": "2"},
        "category": category,
        "description": description,
        "attributes": {"tag": "value"} if attrs is None else attrs,
        "images": [image],
    }


def make_pack_file(tmp_path: Path, pack_id: str, *, version="1.0", items=None, name=None, filename=None):
    pack = base_pack(pack_id)
    pack["version"] = version
    pack["name"] = name or f"Pack {pack_id}"
    items_doc = {"schema_version": 1, "items": items or [item("item-1")]}
    return write_vpack(
        tmp_path / (filename or f"{pack_id}-{version}.vpack"),
        pack=pack,
        items=items_doc,
        assets={"assets/a.png": image_bytes("PNG"), "assets/b.webp": image_bytes("WEBP")},
    )


def setup_phase5(tmp_path: Path, *, packs: list[tuple[str, list[dict]]] | None = None, lock_timeout=2.0, checkout_failure_hook=None, lock_trace=None) -> Phase5Env:
    paths, pack_repo = setup_phase4(tmp_path, with_user_db=True)
    access = PackAccessCoordinator()
    lifecycle = PackLifecycleManager(paths, repository=pack_repo, coordinator=access, move_sleep=lambda _: None, lock_timeout=lock_timeout)
    for pack_id, items in packs or []:
        lifecycle.import_vpack(make_pack_file(tmp_path, pack_id, items=items))
    users = UserRepository(paths.user_db)
    assets = AssetResolver(PackPathResolver(paths))
    cart_lock = CartCheckoutCoordinator(trace=lock_trace)
    snapshots = SnapshotManager(paths)
    backup = UserDataBackupManager(paths.user_db, paths.user_backups, paths.recovery_hold)
    backup.ensure_initial_backup()
    catalog = CatalogService(pack_repo, access, assets, lock_timeout=lock_timeout)
    cart = CartService(users, pack_repo, access, cart_lock, assets, lock_timeout=lock_timeout, lock_trace=lock_trace)
    history = HistoryService(users, snapshots)
    stats = StatsService(users)
    pack_service = PackService(pack_repo, access, lifecycle)
    checkout = CheckoutService(
        users, pack_repo, access, cart_lock, assets, snapshots, history, backup,
        lock_timeout=lock_timeout, failure_hook=checkout_failure_hook, lock_trace=lock_trace,
    )
    return Phase5Env(paths, pack_repo, users, access, lifecycle, assets, cart_lock, snapshots, backup, catalog, cart, history, stats, pack_service, checkout)
