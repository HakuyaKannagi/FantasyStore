from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from fantasy_store.application.cart_checkout_coordinator import CartCheckoutCoordinator
from fantasy_store.application.locks import multi_pack_read_locks
from fantasy_store.application.models import AddCartResult, CartLine, CartResult, pack_asset_ref
from fantasy_store.domain.errors import (
    CartQuantityLimitError,
    DatabaseWriteError,
    DomainError,
    ProductNotAvailableError,
    ValidationError,
)
from fantasy_store.domain.ids import validate_item_id, validate_pack_id
from fantasy_store.domain.money import MoneyLiteral, MoneyValue, sum_money
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.persistence.pack_repository import PackRepository
from fantasy_store.persistence.user_repository import CartItem, UserRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _quantity(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 999):
        raise ValidationError("quantity must be 1..999")
    return value


def _money(row) -> MoneyValue:
    return MoneyValue.from_literal(MoneyLiteral(str(row["price_significand"]), str(row["price_exponent"])))


def _primary_image(row) -> str | None:
    try:
        images = json.loads(row["images_json"])
    except Exception:
        return None
    return images[0] if isinstance(images, list) and images and isinstance(images[0], str) else None


class CartService:
    def __init__(
        self,
        users: UserRepository,
        packs: PackRepository,
        pack_access: PackAccessCoordinator,
        cart_checkout: CartCheckoutCoordinator,
        assets: AssetResolver,
        *,
        lock_timeout: float = 2.0,
        lock_trace=None,
    ) -> None:
        self.users = users
        self.packs = packs
        self.pack_access = pack_access
        self.cart_checkout = cart_checkout
        self.assets = assets
        self.lock_timeout = lock_timeout
        self.lock_trace = lock_trace

    def _available_item(self, pack_id: str, item_id: str):
        pack = self.packs.get_installed_pack(pack_id)
        if pack is None or not pack.is_enabled:
            raise ProductNotAvailableError()
        row = self.packs.get_item(pack_id, item_id)
        if row is None:
            raise ProductNotAvailableError()
        return row

    def _resolve_line_locked(self, item: CartItem) -> CartLine:
        pack = self.packs.get_installed_pack(item.pack_id)
        if pack is None:
            return CartLine(item.pack_id, item.item_id, item.quantity, False, "PACK_NOT_INSTALLED", None, None, None, None)
        if not pack.is_enabled:
            return CartLine(item.pack_id, item.item_id, item.quantity, False, "PACK_DISABLED", None, None, None, None)
        row = self.packs.get_item(item.pack_id, item.item_id)
        if row is None:
            return CartLine(item.pack_id, item.item_id, item.quantity, False, "ITEM_NOT_FOUND", None, None, None, None)
        unit = _money(row)
        line_total = unit.multiply_quantity(item.quantity)
        primary = _primary_image(row)
        image_ref = None
        if primary is not None:
            try:
                self.assets.resolve(item.pack_id, primary)
                image_ref = pack_asset_ref(item.pack_id, primary)
            except Exception:
                image_ref = None
        return CartLine(
            item.pack_id,
            item.item_id,
            item.quantity,
            True,
            None,
            str(row["item_name"]),
            unit,
            line_total,
            image_ref,
        )

    @staticmethod
    def _db_write(call):
        try:
            return call()
        except DomainError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseWriteError(str(exc)) from exc

    def add_to_cart(self, pack_id: str, item_id: str, quantity: int) -> AddCartResult:
        validate_pack_id(pack_id)
        validate_item_id(item_id)
        quantity = _quantity(quantity)
        with self.cart_checkout.exclusive():
            with self.pack_access.read_lock(pack_id, timeout=self.lock_timeout):
                if self.lock_trace is not None:
                    self.lock_trace(f"pack_read_acquired:{pack_id}")
                self._available_item(pack_id, item_id)
                now = _utc_now()
                if self.lock_trace is not None:
                    self.lock_trace("sqlite_transaction_begin")
                try:
                    item = self._db_write(lambda: self.users.add_cart_quantity(pack_id, item_id, quantity, now, now))
                except ValidationError as exc:
                    if exc.code == "CART_QUANTITY_LIMIT":
                        raise CartQuantityLimitError() from exc
                    raise
                line = self._resolve_line_locked(item)
            if self.lock_trace is not None:
                self.lock_trace(f"pack_read_released:{pack_id}")
            return AddCartResult(line, self.users.cart_total_quantity())

    def update_cart_item(self, pack_id: str, item_id: str, quantity: int) -> CartLine:
        validate_pack_id(pack_id)
        validate_item_id(item_id)
        quantity = _quantity(quantity)
        with self.cart_checkout.exclusive():
            found = self._db_write(lambda: self.users.update_cart_quantity(pack_id, item_id, quantity, _utc_now()))
            if not found:
                raise ValidationError("cart item does not exist")
        # Resolve after the DB write; this method never acquires Pack before Cart
        # in reverse because the Cart lock has already been released.
        cart = self.get_cart()
        for line in cart.lines:
            if line.pack_id == pack_id and line.item_id == item_id:
                return line
        raise ValidationError("cart item disappeared")

    def remove_cart_item(self, pack_id: str, item_id: str) -> bool:
        validate_pack_id(pack_id)
        validate_item_id(item_id)
        with self.cart_checkout.exclusive():
            return self._db_write(lambda: self.users.delete_cart_item(pack_id, item_id))

    def clear_cart(self) -> int:
        with self.cart_checkout.exclusive():
            return self._db_write(self.users.clear_cart)

    def get_cart(self) -> CartResult:
        items = self.users.list_cart()
        pack_ids = [item.pack_id for item in items]
        with multi_pack_read_locks(
            self.pack_access,
            pack_ids,
            timeout=self.lock_timeout,
            trace=self.lock_trace,
        ):
            lines = tuple(self._resolve_line_locked(item) for item in items)
        totals = [line.line_total for line in lines if line.available and line.line_total is not None]
        return CartResult(
            lines=lines,
            total_amount=sum_money(totals),
            total_quantity=sum(item.quantity for item in items),
            has_unavailable=any(not line.available for line in lines),
        )
