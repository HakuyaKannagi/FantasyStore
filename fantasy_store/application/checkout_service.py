from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

from fantasy_store.application.cart_checkout_coordinator import CartCheckoutCoordinator
from fantasy_store.application.history_service import HistoryService
from fantasy_store.application.locks import multi_pack_read_locks
from fantasy_store.application.models import CheckoutResult
from fantasy_store.domain.errors import (
    CheckoutItemUnavailableError,
    DatabaseWriteError,
    DomainError,
    FileAccessDeniedError,
    FileDiskFullError,
    PackValidationError,
)
from fantasy_store.domain.ids import new_uuid_v4, validate_uuid_v4
from fantasy_store.domain.money import MoneyLiteral, MoneyValue, sum_money
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.persistence.pack_repository import PackRepository
from fantasy_store.persistence.user_backup import UserDataBackupManager
from fantasy_store.persistence.user_repository import (
    Order,
    OrderItemSnapshot,
    PurchaseRequest,
    UserRepository,
)
from fantasy_store.snapshot.manager import SnapshotManager, SnapshotSource
from fantasy_store.snapshot.retention import SnapshotRetentionManager


FailureHook = Callable[[str], None]
TraceHook = Callable[[str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class CheckoutService:
    def __init__(
        self,
        users: UserRepository,
        packs: PackRepository,
        pack_access: PackAccessCoordinator,
        cart_checkout: CartCheckoutCoordinator,
        assets: AssetResolver,
        snapshots: SnapshotManager,
        history: HistoryService,
        backup_manager: UserDataBackupManager,
        *,
        snapshot_retention: SnapshotRetentionManager | None = None,
        logger: logging.Logger | None = None,
        lock_timeout: float = 2.0,
        failure_hook: FailureHook | None = None,
        lock_trace: TraceHook | None = None,
    ) -> None:
        self.users = users
        self.packs = packs
        self.pack_access = pack_access
        self.cart_checkout = cart_checkout
        self.assets = assets
        self.snapshots = snapshots
        self.history = history
        self.backup_manager = backup_manager
        self.snapshot_retention = snapshot_retention
        self.logger = logger or logging.getLogger(__name__)
        self.lock_timeout = lock_timeout
        self.failure_hook = failure_hook
        self.lock_trace = lock_trace

    def _checkpoint(self, name: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(name)

    def _db_failure_hook(self, name: str) -> None:
        if self.lock_trace is not None and name == "after_order":
            self.lock_trace("sqlite_transaction_active")
        self._checkpoint(f"db_{name}")

    def checkout(self, request_id: str) -> CheckoutResult:
        validate_uuid_v4(request_id)
        self.logger.info("checkout start", extra={"event_code": "CHECKOUT_START", "request_id": request_id})

        committed_order_id: str | None = None
        with self.cart_checkout.exclusive():
            existing = self.users.get_purchase_request(request_id)
            if existing is not None:
                detail = self.history.get_order_detail(existing.order_id)
                self.logger.info(
                    "checkout idempotent replay",
                    extra={"event_code": "CHECKOUT_IDEMPOTENT_REPLAY", "request_id": request_id, "order_id": existing.order_id},
                )
                return CheckoutResult(detail, True, False)

            cart = self.users.list_cart()
            if not cart:
                raise CheckoutItemUnavailableError("cart is empty")
            pack_ids = [line.pack_id for line in cart]

            with multi_pack_read_locks(
                self.pack_access,
                pack_ids,
                timeout=self.lock_timeout,
                trace=self.lock_trace,
            ):
                rows: list[tuple[object, object]] = []
                snapshot_sources: list[SnapshotSource] = []
                unit_values: list[MoneyValue] = []
                line_values: list[MoneyValue] = []

                # Re-read all current pack/item state only after every Pack read
                # lock is held, so a multi-pack order has one stable generation
                # per participating pack through snapshot and DB commit.
                for line_no, cart_line in enumerate(cart, start=1):
                    pack = self.packs.get_installed_pack(cart_line.pack_id)
                    if pack is None or not pack.is_enabled:
                        raise CheckoutItemUnavailableError(f"pack unavailable: {cart_line.pack_id}")
                    row = self.packs.get_item(cart_line.pack_id, cart_line.item_id)
                    if row is None:
                        raise CheckoutItemUnavailableError(f"item unavailable: {cart_line.pack_id}/{cart_line.item_id}")
                    try:
                        images = json.loads(row["images_json"])
                        if not isinstance(images, list) or not images or not isinstance(images[0], str):
                            raise ValueError("primary image missing")
                        source = self.assets.resolve(cart_line.pack_id, images[0])
                    except Exception as exc:
                        raise CheckoutItemUnavailableError(
                            f"primary image unavailable: {cart_line.pack_id}/{cart_line.item_id}"
                        ) from exc
                    unit = MoneyValue.from_literal(
                        MoneyLiteral(str(row["price_significand"]), str(row["price_exponent"]))
                    )
                    line_total = unit.multiply_quantity(cart_line.quantity)
                    rows.append((cart_line, row))
                    unit_values.append(unit)
                    line_values.append(line_total)
                    snapshot_sources.append(SnapshotSource(line_no, source))

                total_amount = sum_money(line_values)
                total_quantity = sum(line.quantity for line in cart)
                self._checkpoint("before_snapshot")

                snapshot_images_enabled = True
                if self.snapshot_retention is not None:
                    # Validate the purchase-time source images and determine the
                    # exact bytes a whole-order snapshot copy would add before
                    # creating pending files. Capacity failure is image-only: the
                    # order remains valid and commits with null snapshot paths.
                    try:
                        required_snapshot_bytes = self.snapshots.estimate_sources_bytes(snapshot_sources)
                    except PackValidationError as exc:
                        self.logger.warning(
                            "checkout snapshot image validation failed: %s", exc,
                            extra={"event_code": "CHECKOUT_ITEM_UNAVAILABLE", "request_id": request_id},
                        )
                        raise CheckoutItemUnavailableError("primary image failed purchase-time validation") from exc
                    try:
                        capacity = self.snapshot_retention.ensure_capacity(required_snapshot_bytes)
                        snapshot_images_enabled = capacity.capacity_available
                    except Exception as exc:
                        snapshot_images_enabled = False
                        self.logger.warning(
                            "snapshot capacity admission failed; order images will be skipped: %s",
                            exc,
                            extra={"event_code": "SNAPSHOT_RETENTION_CAPACITY_UNAVAILABLE", "request_id": request_id},
                        )
                    if not snapshot_images_enabled:
                        self.logger.warning(
                            "new order purchase-history snapshot images skipped by hard-limit policy",
                            extra={
                                "event_code": "SNAPSHOT_RETENTION_NEW_ORDER_SKIPPED",
                                "request_id": request_id,
                                "required_bytes": required_snapshot_bytes,
                            },
                        )

                prepared = ()
                if snapshot_images_enabled:
                    try:
                        prepared = self.snapshots.prepare_pending(request_id, snapshot_sources)
                    except PackValidationError as exc:
                        self.logger.warning(
                            "checkout snapshot image validation failed: %s", exc,
                            extra={"event_code": "CHECKOUT_ITEM_UNAVAILABLE", "request_id": request_id},
                        )
                        raise CheckoutItemUnavailableError("primary image failed purchase-time validation") from exc
                    self._checkpoint("after_snapshot_pending")

                order_id = new_uuid_v4()
                final_dir = None
                try:
                    if snapshot_images_enabled:
                        final_dir = self.snapshots.finalize(request_id, order_id)
                        self._checkpoint("after_snapshot_final")
                    purchased_at = _utc_now()
                    order = Order(
                        order_id=order_id,
                        purchased_at=purchased_at,
                        total_amount=total_amount,
                        total_quantity=total_quantity,
                        line_count=len(cart),
                    )
                    by_line = {entry.line_no: entry for entry in prepared}
                    snapshot_rows: list[OrderItemSnapshot] = []
                    for line_no, ((cart_line, row), unit, line_total) in enumerate(
                        zip(rows, unit_values, line_values), start=1
                    ):
                        entry = by_line.get(line_no)
                        snapshot_rows.append(
                            OrderItemSnapshot(
                                order_id=order_id,
                                line_no=line_no,
                                pack_id=cart_line.pack_id,
                                item_id=cart_line.item_id,
                                item_name=str(row["item_name"]),
                                unit_price=unit,
                                quantity=cart_line.quantity,
                                line_total=line_total,
                                category=str(row["category"]),
                                description=str(row["description"]),
                                attributes_json=str(row["attributes_json"]),
                                primary_image_snapshot_path=(
                                    self.snapshots.relative_snapshot_path(order_id, entry.filename) if entry is not None else None
                                ),
                                primary_image_snapshot_sha256=(entry.sha256 if entry is not None else None),
                            )
                        )
                    request = PurchaseRequest(request_id, order_id, purchased_at)
                    if self.lock_trace is not None:
                        self.lock_trace("sqlite_transaction_begin")
                    try:
                        self.users.commit_order_bundle(
                            order,
                            snapshot_rows,
                            request,
                            clear_cart=True,
                            failure_hook=self._db_failure_hook,
                        )
                    except Exception as exc:
                        self.logger.error(
                            "checkout DB rollback: %s", exc,
                            extra={"event_code": "CHECKOUT_DB_ROLLBACK", "request_id": request_id, "order_id": order_id},
                        )
                        try:
                            cleaned = self.snapshots.cleanup_final(order_id)
                        except Exception as cleanup_exc:
                            cleaned = False
                            self.logger.warning(
                                "checkout rollback snapshot cleanup failed: %s", cleanup_exc,
                                extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED", "request_id": request_id, "order_id": order_id},
                            )
                        if not cleaned:
                            self.logger.warning(
                                "checkout rollback left snapshot orphan candidate",
                                extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED", "request_id": request_id, "order_id": order_id},
                            )
                        if isinstance(exc, DomainError):
                            raise
                        raise DatabaseWriteError(str(exc)) from exc
                    committed_order_id = order_id
                    self._checkpoint("after_db_commit")
                except Exception:
                    if committed_order_id is None:
                        try:
                            cleaned = self.snapshots.cleanup_final(order_id) if final_dir is not None else self.snapshots.cleanup_pending(request_id)
                        except Exception as cleanup_exc:
                            cleaned = False
                            self.logger.warning(
                                "checkout snapshot cleanup failed: %s", cleanup_exc,
                                extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED", "request_id": request_id},
                            )
                        if not cleaned:
                            self.logger.warning(
                                "checkout failure left snapshot orphan candidate",
                                extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED", "request_id": request_id},
                            )
                    raise

            assert committed_order_id is not None
            if self.snapshot_retention is not None:
                try:
                    self.snapshot_retention.prune()
                except Exception as exc:
                    self.logger.warning(
                        "snapshot retention after checkout failed: %s",
                        exc,
                        extra={"event_code": "SNAPSHOT_RETENTION_FAILED", "order_id": committed_order_id},
                    )
            # History reset shares this coordinator. Build the committed order
            # response before releasing it so reset cannot delete the just-
            # committed order between COMMIT and response materialization.
            self._checkpoint("before_response")
            detail = self.history.get_order_detail(committed_order_id)

        # A committed order cannot be rolled back by this non-transactional
        # protection backup. Pack and Cart locks are intentionally released first.
        backup_failed = False
        try:
            self.backup_manager.backup_after_checkout()
        except Exception as exc:
            backup_failed = True
            self.logger.warning(
                "checkout user data backup failed: %s",
                exc,
                extra={"event_code": "DB_BACKUP_FAILED", "request_id": request_id, "order_id": committed_order_id},
            )

        assert committed_order_id is not None
        self.logger.info(
            "checkout complete",
            extra={"event_code": "CHECKOUT_COMPLETE", "request_id": request_id, "order_id": committed_order_id},
        )
        return CheckoutResult(detail, False, backup_failed)
