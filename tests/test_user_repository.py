from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from fantasy_store.persistence.migration import USER_DB_NAME, initialize_database
from fantasy_store.persistence.user_repository import (
    CartItem,
    Order,
    OrderItemSnapshot,
    PurchaseRequest,
    UserRepository,
    UserSetting,
)


def make_repo(tmp_path: Path) -> tuple[Path, UserRepository]:
    db = tmp_path / "user_data.db"
    initialize_database(db, USER_DB_NAME)
    return db, UserRepository(db)


def money(significand: str, exponent: str = "0") -> MoneyValue:
    return MoneyValue.from_literal(MoneyLiteral(significand, exponent))


def sample_order(order_id: str = "o-1", total: MoneyValue | None = None) -> Order:
    return Order(order_id, "2026-09-05T00:00:00Z", total or money("123"), 2, 1)


def sample_item(order_id: str = "o-1", value: MoneyValue | None = None) -> OrderItemSnapshot:
    value = value or money("123")
    return OrderItemSnapshot(
        order_id=order_id,
        line_no=1,
        pack_id="pack-a",
        item_id="item-a",
        item_name="架空商品",
        unit_price=value,
        quantity=2,
        line_total=value.multiply_quantity(2),
        category="cat",
        description="desc",
        attributes_json='{"x":1}',
    )


def test_cart_crud_and_zero_rows(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    assert repo.list_cart() == []
    assert repo.cart_line_count() == 0
    assert repo.cart_total_quantity() == 0
    assert repo.get_cart_item("pack-a", "item-a") is None

    item = CartItem("pack-a", "item-a", 2, "t1", "t1")
    repo.insert_cart_item(item)
    assert repo.get_cart_item("pack-a", "item-a") == item
    assert repo.cart_line_count() == 1
    assert repo.cart_total_quantity() == 2

    assert repo.update_cart_quantity("pack-a", "item-a", 3, "t2") is True
    assert repo.get_cart_item("pack-a", "item-a").quantity == 3  # type: ignore[union-attr]
    assert repo.delete_cart_item("pack-a", "missing") is False
    assert repo.delete_cart_item("pack-a", "item-a") is True
    assert repo.clear_cart() == 0


def test_cart_quantity_check_is_enforced_by_frozen_ddl(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_cart_item(CartItem("p", "i0", 0, "t", "t"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_cart_item(CartItem("p", "i1000", 1000, "t", "t"))


def test_order_snapshot_purchase_request_and_history_reads(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    order = sample_order()
    repo.insert_order(order)
    item = sample_item()
    row_id = repo.insert_order_item(item)
    assert row_id > 0
    request = PurchaseRequest("req-1", order.order_id, "2026-09-05T00:00:01Z")
    repo.insert_purchase_request(request)

    assert repo.get_order(order.order_id) == order
    items = repo.list_order_items(order.order_id)
    assert len(items) == 1
    assert items[0].order_item_id == row_id
    assert items[0].unit_price == item.unit_price
    assert items[0].line_total == item.line_total
    assert repo.get_purchase_request("req-1") == request
    detail = repo.get_order_detail(order.order_id)
    assert detail is not None and detail.order == order and len(detail.items) == 1
    assert repo.order_count() == 1
    assert repo.purchased_total_quantity() == 2
    assert repo.list_order_history(limit=100) == [order]


def test_history_paging_and_validation(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    for i in range(3):
        repo.insert_order(Order(f"o-{i}", f"2026-09-05T00:00:0{i}Z", money("1"), 1, 1))
    assert [o.order_id for o in repo.list_order_history(2, 0)] == ["o-2", "o-1"]
    assert [o.order_id for o in repo.list_order_history(2, 2)] == ["o-0"]
    with pytest.raises(ValidationError):
        repo.list_order_history(101)
    with pytest.raises(ValidationError):
        repo.list_order_history(1, -1)


def test_foreign_key_is_enforced_for_order_snapshot_and_request(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_order_item(sample_item("missing-order"))
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_purchase_request(PurchaseRequest("req", "missing-order", "t"))


def test_user_settings_crud_and_json_boundary(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    assert repo.list_settings() == []
    setting = UserSetting("theme", '{"name":"default"}', "t1")
    repo.upsert_setting(setting)
    assert repo.get_setting("theme") == setting
    updated = UserSetting("theme", '{"name":"compact"}', "t2")
    repo.upsert_setting(updated)
    assert repo.get_setting("theme") == updated
    assert repo.delete_setting("theme") is True
    assert repo.delete_setting("theme") is False
    with pytest.raises(ValidationError):
        repo.upsert_setting(UserSetting("bad", "not-json", "t"))


@pytest.mark.parametrize(
    "value",
    [
        MoneyValue.zero(),
        money("1", "9000000000000000000"),
        money("1234567890123456789", "0"),
        money("9999999999999999999999999999999999999999999999999999999999999999", "9"),
    ],
)
def test_money_round_trip_through_db_is_exact(tmp_path: Path, value: MoneyValue):
    db, repo = make_repo(tmp_path)
    order = Order("money-order", "t", value, 1, 1)
    repo.insert_order(order)
    restored = repo.get_order(order.order_id)
    assert restored is not None
    assert restored.total_amount == value

    raw = sqlite3.connect(db).execute(
        "SELECT total_amount_json FROM orders WHERE order_id=?", (order.order_id,)
    ).fetchone()[0]
    assert raw == value.to_canonical_json()


def test_noncanonical_money_in_db_is_rejected_on_repository_read(tmp_path: Path):
    db, repo = make_repo(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO orders VALUES(?,?,?,?,?)",
        ("bad", "t", '{"terms": [ ]}', 1, 1),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValidationError):
        repo.get_order("bad")


def test_atomic_order_bundle_rolls_back_every_table_and_keeps_cart(tmp_path: Path):
    db, repo = make_repo(tmp_path)
    repo.insert_cart_item(CartItem("pack-a", "item-a", 2, "t", "t"))
    order = sample_order()
    item = sample_item()
    request = PurchaseRequest("req-1", order.order_id, "t")

    def fail(stage: str) -> None:
        if stage == "after_request":
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        repo.commit_order_bundle(order, [item], request, failure_hook=fail)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM order_items_snapshot").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM purchase_requests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cart_items").fetchone()[0] == 1
    finally:
        conn.close()


def test_atomic_order_bundle_success(tmp_path: Path):
    _, repo = make_repo(tmp_path)
    repo.insert_cart_item(CartItem("pack-a", "item-a", 2, "t", "t"))
    order = sample_order()
    request = PurchaseRequest("req-1", order.order_id, "t")
    repo.commit_order_bundle(order, [sample_item()], request)
    assert repo.order_count() == 1
    assert repo.get_purchase_request("req-1") == request
    assert repo.list_cart() == []
