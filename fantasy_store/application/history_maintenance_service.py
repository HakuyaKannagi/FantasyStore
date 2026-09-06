from __future__ import annotations

import logging
from dataclasses import dataclass

from fantasy_store.application.cart_checkout_coordinator import CartCheckoutCoordinator
from fantasy_store.persistence.user_repository import UserRepository
from fantasy_store.snapshot.manager import SnapshotManager


@dataclass(frozen=True, slots=True)
class HistoryResetResult:
    deleted_orders: int
    removed_snapshot_directories: int
    failed_snapshot_directories: int


class HistoryMaintenanceService:
    """Manager-only destructive maintenance for the complete purchase history.

    Checkout and reset share CartCheckoutCoordinator so a checkout cannot commit
    while history is being reset, nor can reset delete a concurrently committing
    order. The SQLite history deletion is one transaction. Snapshot filesystem
    cleanup follows after that DB commit and is intentionally not treated as the
    same ACID transaction.
    """

    def __init__(
        self,
        repository: UserRepository,
        snapshots: SnapshotManager,
        cart_checkout: CartCheckoutCoordinator,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.snapshots = snapshots
        self.cart_checkout = cart_checkout
        self.logger = logger or logging.getLogger(__name__)

    def reset_all(self) -> HistoryResetResult:
        with self.cart_checkout.exclusive():
            deleted = self.repository.reset_purchase_history()
            removed, failed = self.snapshots.cleanup_all_history_images()
        self.logger.info(
            "purchase history reset completed",
            extra={
                "event_code": "PURCHASE_HISTORY_RESET",
                "deleted_orders": deleted,
                "removed_snapshot_directories": removed,
                "failed_snapshot_directories": failed,
            },
        )
        return HistoryResetResult(deleted, removed, failed)
