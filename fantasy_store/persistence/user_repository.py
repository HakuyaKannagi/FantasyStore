from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from fantasy_store.domain.money import MoneyValue
from fantasy_store.domain.errors import ValidationError
from .connection import begin_immediate, connect


@dataclass(frozen=True, slots=True)
class CartItem:
    pack_id: str
    item_id: str
    quantity: int
    added_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    purchased_at: str
    total_amount: MoneyValue
    total_quantity: int
    line_count: int


@dataclass(frozen=True, slots=True)
class OrderItemSnapshot:
    order_id: str
    line_no: int
    pack_id: str
    item_id: str
    item_name: str
    unit_price: MoneyValue
    quantity: int
    line_total: MoneyValue
    category: str
    description: str
    attributes_json: str
    primary_image_snapshot_path: str | None = None
    primary_image_snapshot_sha256: str | None = None
    order_item_id: int | None = None


@dataclass(frozen=True, slots=True)
class PurchaseRequest:
    request_id: str
    order_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class UserSetting:
    setting_key: str
    setting_value_json: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class OrderDetail:
    order: Order
    items: tuple[OrderItemSnapshot, ...]


FailureHook = Callable[[str], None]


def _serialize_money(value: MoneyValue) -> str:
    if not isinstance(value, MoneyValue):
        raise ValidationError("money value must be MoneyValue")
    text = value.to_canonical_json()
    # Round-trip at the persistence boundary prevents accepting a serializer
    # regression that no longer matches MoneyValue's canonical form.
    MoneyValue.from_canonical_json(text)
    return text


def _deserialize_money(text: str) -> MoneyValue:
    return MoneyValue.from_canonical_json(text)


def _validate_json_text(text: str, field: str) -> None:
    if not isinstance(text, str):
        raise ValidationError(f"{field} must be JSON text")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{field} must contain valid JSON") from exc


def _cart_from_row(row: sqlite3.Row) -> CartItem:
    return CartItem(row["pack_id"], row["item_id"], row["quantity"], row["added_at"], row["updated_at"])


def _order_from_row(row: sqlite3.Row) -> Order:
    return Order(
        order_id=row["order_id"],
        purchased_at=row["purchased_at"],
        total_amount=_deserialize_money(row["total_amount_json"]),
        total_quantity=row["total_quantity"],
        line_count=row["line_count"],
    )


def _order_item_from_row(row: sqlite3.Row) -> OrderItemSnapshot:
    return OrderItemSnapshot(
        order_item_id=row["order_item_id"],
        order_id=row["order_id"],
        line_no=row["line_no"],
        pack_id=row["pack_id"],
        item_id=row["item_id"],
        item_name=row["item_name"],
        unit_price=_deserialize_money(row["unit_price_json"]),
        quantity=row["quantity"],
        line_total=_deserialize_money(row["line_total_json"]),
        category=row["category"],
        description=row["description"],
        attributes_json=row["attributes_json"],
        primary_image_snapshot_path=row["primary_image_snapshot_path"],
        primary_image_snapshot_sha256=row["primary_image_snapshot_sha256"],
    )


class UserRepository:
    """Short-lived SQLite repository for the frozen user_data.db schema.

    It intentionally contains no pack availability checks, checkout business
    decisions, UI/Bridge logic, or arbitrary path handling.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    # ---- cart ---------------------------------------------------------
    def list_cart(self) -> list[CartItem]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT pack_id,item_id,quantity,added_at,updated_at FROM cart_items ORDER BY added_at, pack_id, item_id"
            ).fetchall()
            return [_cart_from_row(row) for row in rows]
        finally:
            conn.close()

    def get_cart_item(self, pack_id: str, item_id: str) -> CartItem | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT pack_id,item_id,quantity,added_at,updated_at FROM cart_items WHERE pack_id=? AND item_id=?",
                (pack_id, item_id),
            ).fetchone()
            return _cart_from_row(row) if row else None
        finally:
            conn.close()

    def insert_cart_item(self, item: CartItem) -> None:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                conn.execute(
                    "INSERT INTO cart_items(pack_id,item_id,quantity,added_at,updated_at) VALUES(?,?,?,?,?)",
                    (item.pack_id, item.item_id, item.quantity, item.added_at, item.updated_at),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def update_cart_quantity(self, pack_id: str, item_id: str, quantity: int, updated_at: str) -> bool:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                cur = conn.execute(
                    "UPDATE cart_items SET quantity=?, updated_at=? WHERE pack_id=? AND item_id=?",
                    (quantity, updated_at, pack_id, item_id),
                )
                conn.execute("COMMIT")
                return cur.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def delete_cart_item(self, pack_id: str, item_id: str) -> bool:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                cur = conn.execute("DELETE FROM cart_items WHERE pack_id=? AND item_id=?", (pack_id, item_id))
                conn.execute("COMMIT")
                return cur.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def clear_cart(self) -> int:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                cur = conn.execute("DELETE FROM cart_items")
                conn.execute("COMMIT")
                return cur.rowcount
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def cart_line_count(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM cart_items").fetchone()[0])
        finally:
            conn.close()

    def cart_total_quantity(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COALESCE(SUM(quantity),0) FROM cart_items").fetchone()[0])
        finally:
            conn.close()

    # ---- orders -------------------------------------------------------
    def insert_order(self, order: Order) -> None:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                self._insert_order(conn, order)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def get_order(self, order_id: str) -> Order | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            return _order_from_row(row) if row else None
        finally:
            conn.close()

    def list_order_history(self, limit: int, offset: int = 0) -> list[Order]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 100):
            raise ValidationError("history limit must be 1..100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationError("history offset must be a non-negative integer")
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY purchased_at DESC, order_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [_order_from_row(row) for row in rows]
        finally:
            conn.close()

    def insert_order_item(self, item: OrderItemSnapshot) -> int:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                row_id = self._insert_order_item(conn, item)
                conn.execute("COMMIT")
                return row_id
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def list_order_items(self, order_id: str) -> list[OrderItemSnapshot]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM order_items_snapshot WHERE order_id=? ORDER BY line_no",
                (order_id,),
            ).fetchall()
            return [_order_item_from_row(row) for row in rows]
        finally:
            conn.close()

    def get_order_detail(self, order_id: str) -> OrderDetail | None:
        order = self.get_order(order_id)
        if order is None:
            return None
        return OrderDetail(order=order, items=tuple(self.list_order_items(order_id)))

    def order_count(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        finally:
            conn.close()

    def purchased_total_quantity(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COALESCE(SUM(total_quantity),0) FROM orders").fetchone()[0])
        finally:
            conn.close()

    # ---- purchase requests ------------------------------------------
    def insert_purchase_request(self, request: PurchaseRequest) -> None:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                self._insert_purchase_request(conn, request)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def get_purchase_request(self, request_id: str) -> PurchaseRequest | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT request_id,order_id,created_at FROM purchase_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            return PurchaseRequest(row["request_id"], row["order_id"], row["created_at"]) if row else None
        finally:
            conn.close()

    # ---- settings ----------------------------------------------------
    def get_setting(self, setting_key: str) -> UserSetting | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT setting_key,setting_value_json,updated_at FROM user_settings WHERE setting_key=?",
                (setting_key,),
            ).fetchone()
            return UserSetting(row["setting_key"], row["setting_value_json"], row["updated_at"]) if row else None
        finally:
            conn.close()

    def list_settings(self) -> list[UserSetting]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT setting_key,setting_value_json,updated_at FROM user_settings ORDER BY setting_key"
            ).fetchall()
            return [UserSetting(r["setting_key"], r["setting_value_json"], r["updated_at"]) for r in rows]
        finally:
            conn.close()

    def upsert_setting(self, setting: UserSetting) -> None:
        # Key allow-listing intentionally belongs to the later Application layer.
        _validate_json_text(setting.setting_value_json, "setting_value_json")
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                conn.execute(
                    """INSERT INTO user_settings(setting_key,setting_value_json,updated_at)
                       VALUES(?,?,?)
                       ON CONFLICT(setting_key) DO UPDATE SET
                         setting_value_json=excluded.setting_value_json,
                         updated_at=excluded.updated_at""",
                    (setting.setting_key, setting.setting_value_json, setting.updated_at),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def delete_setting(self, setting_key: str) -> bool:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                cur = conn.execute("DELETE FROM user_settings WHERE setting_key=?", (setting_key,))
                conn.execute("COMMIT")
                return cur.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


    def add_cart_quantity(
        self, pack_id: str, item_id: str, quantity: int, added_at: str, updated_at: str
    ) -> CartItem:
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not (1 <= quantity <= 999):
            raise ValidationError("cart quantity must be 1..999")
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                row = conn.execute(
                    "SELECT quantity,added_at FROM cart_items WHERE pack_id=? AND item_id=?",
                    (pack_id, item_id),
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO cart_items(pack_id,item_id,quantity,added_at,updated_at) VALUES(?,?,?,?,?)",
                        (pack_id, item_id, quantity, added_at, updated_at),
                    )
                    result = CartItem(pack_id, item_id, quantity, added_at, updated_at)
                else:
                    new_quantity = int(row["quantity"]) + quantity
                    if new_quantity > 999:
                        raise ValidationError("cart quantity would exceed 999", code="CART_QUANTITY_LIMIT")
                    conn.execute(
                        "UPDATE cart_items SET quantity=?,updated_at=? WHERE pack_id=? AND item_id=?",
                        (new_quantity, updated_at, pack_id, item_id),
                    )
                    result = CartItem(pack_id, item_id, new_quantity, row["added_at"], updated_at)
                conn.execute("COMMIT")
                return result
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def list_order_totals(self) -> list[tuple[MoneyValue, int]]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute("SELECT total_amount_json,total_quantity FROM orders ORDER BY purchased_at,order_id").fetchall()
            return [(_deserialize_money(row["total_amount_json"]), int(row["total_quantity"])) for row in rows]
        finally:
            conn.close()

    def list_snapshot_paths(self) -> list[str]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT primary_image_snapshot_path FROM order_items_snapshot WHERE primary_image_snapshot_path IS NOT NULL"
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            conn.close()

    def list_snapshot_retention_candidates(self) -> list[tuple[str, str, int, str]]:
        """Referenced snapshot paths in deterministic oldest-order order."""
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT o.purchased_at,o.order_id,i.line_no,i.primary_image_snapshot_path
                   FROM order_items_snapshot AS i
                   JOIN orders AS o ON o.order_id=i.order_id
                   WHERE i.primary_image_snapshot_path IS NOT NULL
                   ORDER BY o.purchased_at ASC,o.order_id ASC,i.line_no ASC"""
            ).fetchall()
            return [(str(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in rows]
        finally:
            conn.close()

    def reset_purchase_history(self) -> int:
        """Delete all order authority in one SQLite transaction.

        order_items_snapshot and purchase_requests are removed by their frozen
        ON DELETE CASCADE foreign keys. Cart, settings, packs, and pack state are
        deliberately untouched.
        """
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                count = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
                conn.execute("DELETE FROM orders")
                conn.execute("COMMIT")
                return count
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    # ---- Phase-5-safe transaction boundary --------------------------
    def commit_order_bundle(
        self,
        order: Order,
        items: Sequence[OrderItemSnapshot],
        request: PurchaseRequest,
        *,
        clear_cart: bool = True,
        failure_hook: FailureHook | None = None,
    ) -> None:
        """Persist the DB-only checkout bundle atomically.

        This is not CheckoutService: it performs no pack checks, money
        calculation, image copy, lock acquisition, ID generation, or Bridge work.
        """
        if request.order_id != order.order_id:
            raise ValidationError("purchase request order_id must match order")
        if any(item.order_id != order.order_id for item in items):
            raise ValidationError("all snapshot items must belong to the order")

        # Validate all money before beginning the write transaction.
        _serialize_money(order.total_amount)
        for item in items:
            _serialize_money(item.unit_price)
            _serialize_money(item.line_total)
            _validate_json_text(item.attributes_json, "attributes_json")

        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                self._insert_order(conn, order)
                if failure_hook:
                    failure_hook("after_order")
                for item in items:
                    self._insert_order_item(conn, item)
                if failure_hook:
                    failure_hook("after_items")
                self._insert_purchase_request(conn, request)
                if failure_hook:
                    failure_hook("after_request")
                if clear_cart:
                    conn.execute("DELETE FROM cart_items")
                if failure_hook:
                    failure_hook("after_cart_delete")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @staticmethod
    def _insert_order(conn: sqlite3.Connection, order: Order) -> None:
        conn.execute(
            "INSERT INTO orders(order_id,purchased_at,total_amount_json,total_quantity,line_count) VALUES(?,?,?,?,?)",
            (
                order.order_id,
                order.purchased_at,
                _serialize_money(order.total_amount),
                order.total_quantity,
                order.line_count,
            ),
        )

    @staticmethod
    def _insert_order_item(conn: sqlite3.Connection, item: OrderItemSnapshot) -> int:
        _validate_json_text(item.attributes_json, "attributes_json")
        cur = conn.execute(
            """INSERT INTO order_items_snapshot(
                order_id,line_no,pack_id,item_id,item_name,unit_price_json,quantity,line_total_json,
                category,description,attributes_json,primary_image_snapshot_path,primary_image_snapshot_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item.order_id,
                item.line_no,
                item.pack_id,
                item.item_id,
                item.item_name,
                _serialize_money(item.unit_price),
                item.quantity,
                _serialize_money(item.line_total),
                item.category,
                item.description,
                item.attributes_json,
                item.primary_image_snapshot_path,
                item.primary_image_snapshot_sha256,
            ),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _insert_purchase_request(conn: sqlite3.Connection, request: PurchaseRequest) -> None:
        conn.execute(
            "INSERT INTO purchase_requests(request_id,order_id,created_at) VALUES(?,?,?)",
            (request.request_id, request.order_id, request.created_at),
        )
