from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fantasy_store.bootstrap import bootstrap_phase6
from fantasy_store.bridge.api import BridgeApi
from fantasy_store.bridge.file_picker import DeterministicFilePicker
from fantasy_store.bridge.image_resolver import LocalImageResolver
from fantasy_store.bridge.money_mapper import money_to_dto
from fantasy_store.domain.errors import ProductNotAvailableError, ValidationError
from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.runtime.resource_locator import ResourceLocator
from fantasy_store.pack.asset_resolver import AssetResolver
from tests.phase5_helpers import item, make_pack_file, setup_phase5


PRODUCT_REQ = {
    "query": "", "category": None, "min_price": None, "max_price": None,
    "sort": "name_asc", "page": 1, "page_size": 24,
}


def make_bridge(tmp_path: Path):
    env = setup_phase5(tmp_path, packs=[("demo.pack", [item("item-1", name="Demo")])])
    picker = DeterministicFilePicker(None)
    bridge = BridgeApi(
        catalog=env.catalog,
        cart=env.cart,
        checkout=env.checkout,
        history=env.history,
        stats=env.stats,
        packs=env.pack_service,
        file_picker=picker,
    )
    return env, picker, bridge


def assert_success(response):
    assert response["ok"] is True
    assert response["error"] is None
    assert isinstance(response["data"], dict)


def assert_failure(response, code):
    assert response["ok"] is False
    assert response["data"] is None
    assert response["error"]["code"] == code
    assert response["error"]["details"] is None
    assert isinstance(response["error"]["message"], str)


def test_money_dto_zero_normal_huge_and_multiterm():
    cases = [
        MoneyValue.zero(),
        MoneyValue.from_literal(MoneyLiteral("385", "8")),
        MoneyValue.from_literal(MoneyLiteral("12345", "1000000000000")),
        MoneyValue({2: 7, 0: 11}),
    ]
    for value in cases:
        before = value.to_canonical_obj()
        dto = money_to_dto(value)
        assert dto["terms"] == before["terms"]
        assert isinstance(dto["display"], str)
        assert value.to_canonical_obj() == before
    assert money_to_dto(MoneyValue.zero())["display"] == "0"
    assert money_to_dto(MoneyValue.from_literal(MoneyLiteral("385", "8")))["display"] == "38,500,000,000"
    display = money_to_dto(MoneyValue.from_literal(MoneyLiteral("1", "1000000000000")))["display"]
    assert "× 10" in display
    assert "^" not in display
    assert "¹" in display


def test_all_public_bridge_apis_success_and_fixed_dtos(tmp_path):
    env, picker, bridge = make_bridge(tmp_path)
    products = bridge.get_products(PRODUCT_REQ); assert_success(products)
    assert set(products["data"]) == {"items", "total", "page", "page_size", "catalog_state"}
    assert set(products["data"]["items"][0]) == {"pack_id", "item_id", "name", "price", "category", "primary_image_ref"}
    assert set(products["data"]["items"][0]["price"]) == {"terms", "display"}
    assert all(isinstance(t["significand"], str) and isinstance(t["exponent"], str) for t in products["data"]["items"][0]["price"]["terms"])

    categories = bridge.get_categories(); assert_success(categories); assert set(categories["data"]) == {"categories"}
    detail = bridge.get_product_detail({"pack_id": "demo.pack", "item_id": "item-1"}); assert_success(detail)
    assert set(detail["data"]["product"]) == {"pack_id", "item_id", "name", "price", "category", "primary_image_ref", "description", "attributes", "image_refs"}

    cart = bridge.get_cart(); assert_success(cart); assert set(cart["data"]) == {"lines", "total_amount", "total_quantity", "has_unavailable"}
    added = bridge.add_to_cart({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 2}); assert_success(added)
    assert set(added["data"]) == {"line", "cart_total_quantity"}
    assert set(added["data"]["line"]) == {"pack_id", "item_id", "quantity", "available", "unavailable_reason", "name", "unit_price", "line_total", "primary_image_ref"}
    updated = bridge.update_cart_item({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 3}); assert_success(updated); assert set(updated["data"]) == {"line"}
    removed = bridge.remove_cart_item({"pack_id": "demo.pack", "item_id": "item-1"}); assert_success(removed); assert set(removed["data"]) == {"removed"}
    cleared = bridge.clear_cart(); assert_success(cleared); assert set(cleared["data"]) == {"cleared_count"}

    bridge.add_to_cart({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 2})
    request_id = new_uuid_v4()
    checkout = bridge.checkout({"request_id": request_id}); assert_success(checkout)
    assert set(checkout["data"]) == {"order", "idempotent_replay"}
    order = checkout["data"]["order"]
    assert set(order) == {"order_id", "purchased_at", "total_amount", "total_quantity", "line_count", "lines"}
    assert set(order["lines"][0]) == {"line_no", "pack_id", "item_id", "name", "unit_price", "quantity", "line_total", "category", "description", "attributes", "snapshot_image_ref", "snapshot_image_available"}

    history = bridge.get_order_history({"page": 1, "page_size": 24}); assert_success(history); assert set(history["data"]) == {"orders", "total", "page", "page_size"}
    order_detail = bridge.get_order_detail({"order_id": order["order_id"]}); assert_success(order_detail); assert set(order_detail["data"]) == {"order"}
    stats = bridge.get_statistics(); assert_success(stats); assert set(stats["data"]) == {"total_amount", "order_count", "total_quantity"}
    packs = bridge.get_packs(); assert_success(packs); assert set(packs["data"]) == {"packs", "pack_state"}
    assert set(packs["data"]["packs"][0]) == {"pack_id", "name", "version", "author", "description", "enabled", "busy"}
    toggled = bridge.set_pack_enabled({"pack_id": "demo.pack", "enabled": False}); assert_success(toggled); assert set(toggled["data"]) == {"pack"}
    bridge.set_pack_enabled({"pack_id": "demo.pack", "enabled": True})

    picker.selection = make_pack_file(tmp_path, "second.pack", items=[item("x")])
    imported = bridge.import_pack(); assert_success(imported); assert imported["data"]["status"] == "IMPORTED"; assert imported["data"]["pack"]["pack_id"] == "second.pack"
    picker.selection = make_pack_file(tmp_path, "second.pack", version="1.1", items=[item("x", name="new")], filename="second-v2.vpack")
    updated_pack = bridge.import_pack(); assert_success(updated_pack); assert updated_pack["data"]["status"] == "UPDATED"
    picker.selection = None
    cancelled = bridge.import_pack(); assert_success(cancelled); assert cancelled["data"] == {"status": "CANCELLED", "pack": None}


def test_import_pack_has_no_path_argument():
    params = list(inspect.signature(BridgeApi.import_pack).parameters)
    assert params == ["self"]


@pytest.mark.parametrize("method,args", [
    ("get_products", ({},)),
    ("get_product_detail", ({},)),
    ("add_to_cart", ({},)),
    ("update_cart_item", ({},)),
    ("remove_cart_item", ({},)),
    ("checkout", ({"request_id": "not-a-uuid"},)),
    ("get_order_history", ({},)),
    ("get_order_detail", ({"order_id": "bad"},)),
    ("set_pack_enabled", ({"pack_id": "demo.pack", "enabled": "yes"},)),
])
def test_bridge_request_validation(method, args, tmp_path):
    _, _, bridge = make_bridge(tmp_path)
    response = getattr(bridge, method)(*args)
    assert_failure(response, "VALIDATION_INVALID_ARGUMENT")


def _valid_calls(bridge):
    return {
        "get_products": (bridge.catalog, "get_products", (PRODUCT_REQ,)),
        "get_categories": (bridge.catalog, "get_categories", ()),
        "get_product_detail": (bridge.catalog, "get_product_detail", ({"pack_id": "demo.pack", "item_id": "item-1"},)),
        "get_cart": (bridge.cart, "get_cart", ()),
        "add_to_cart": (bridge.cart, "add_to_cart", ({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 1},)),
        "update_cart_item": (bridge.cart, "update_cart_item", ({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 1},)),
        "remove_cart_item": (bridge.cart, "remove_cart_item", ({"pack_id": "demo.pack", "item_id": "item-1"},)),
        "clear_cart": (bridge.cart, "clear_cart", ()),
        "checkout": (bridge.checkout_service, "checkout", ({"request_id": new_uuid_v4()},)),
        "get_order_history": (bridge.history, "get_order_history", ({"page": 1, "page_size": 24},)),
        "get_order_detail": (bridge.history, "get_order_detail", ({"order_id": new_uuid_v4()},)),
        "get_statistics": (bridge.stats, "get_statistics", ()),
        "get_packs": (bridge.packs, "get_packs", ()),
        "set_pack_enabled": (bridge.packs, "set_pack_enabled", ({"pack_id": "demo.pack", "enabled": True},)),
    }


@pytest.mark.parametrize("bridge_method", [
    "get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart", "update_cart_item",
    "remove_cart_item", "clear_cart", "checkout", "get_order_history", "get_order_detail", "get_statistics",
    "get_packs", "set_pack_enabled",
])
def test_every_nonpicker_api_maps_expected_domain_error(bridge_method, tmp_path, monkeypatch):
    _, _, bridge = make_bridge(tmp_path)
    target, attr, args = _valid_calls(bridge)[bridge_method]
    def fail(*a, **k): raise ProductNotAvailableError("/secret/path SELECT *")
    monkeypatch.setattr(target, attr, fail)
    response = getattr(bridge, bridge_method)(*args)
    assert_failure(response, "PRODUCT_NOT_AVAILABLE")
    rendered = json.dumps(response, ensure_ascii=False)
    assert "/secret/path" not in rendered and "SELECT *" not in rendered


@pytest.mark.parametrize("bridge_method", [
    "get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart", "update_cart_item",
    "remove_cart_item", "clear_cart", "checkout", "get_order_history", "get_order_detail", "get_statistics",
    "get_packs", "set_pack_enabled",
])
def test_every_nonpicker_api_hides_unexpected_exception(bridge_method, tmp_path, monkeypatch):
    _, _, bridge = make_bridge(tmp_path)
    target, attr, args = _valid_calls(bridge)[bridge_method]
    def fail(*a, **k): raise RuntimeError("sqlite SELECT secret FROM x; /tmp/private traceback")
    monkeypatch.setattr(target, attr, fail)
    response = getattr(bridge, bridge_method)(*args)
    assert_failure(response, "INTERNAL_ERROR")
    rendered = json.dumps(response, ensure_ascii=False)
    assert "SELECT secret" not in rendered and "/tmp/private" not in rendered and "RuntimeError" not in rendered


def test_import_pack_picker_failures_and_invalid_selection(tmp_path):
    env, picker, bridge = make_bridge(tmp_path)
    picker.error = RuntimeError("C:/secret/path")
    response = bridge.import_pack(); assert_failure(response, "INTERNAL_ERROR"); assert "secret" not in json.dumps(response)
    picker.error = None
    invalid = tmp_path / "not-a-pack.txt"; invalid.write_text("x")
    picker.selection = invalid
    response = bridge.import_pack(); assert_failure(response, "VALIDATION_INVALID_ARGUMENT")


def test_local_image_resolver_pack_snapshot_and_escape_cases(tmp_path):
    env, _, bridge = make_bridge(tmp_path)
    resolver = LocalImageResolver(env.assets, env.snapshots, env.users)
    detail = bridge.get_product_detail({"pack_id": "demo.pack", "item_id": "item-1"})["data"]["product"]
    pack_ref = detail["primary_image_ref"]
    url = resolver.ui_url(pack_ref)
    assert url.startswith("fantasy-image://resource/")
    resolved = resolver.resolve_ui_url(url)
    assert resolved.path.is_file() and resolved.mime_type == "image/png"

    bridge.add_to_cart({"pack_id": "demo.pack", "item_id": "item-1", "quantity": 1})
    order = bridge.checkout({"request_id": new_uuid_v4()})["data"]["order"]
    snap_ref = order["lines"][0]["snapshot_image_ref"]
    snap = resolver.resolve(snap_ref)
    assert snap.path.is_file() and snap.mime_type == "image/png"

    for bad in [
        "../etc/passwd", "/absolute", "C:/windows/file", "http://example.invalid/x",
        "pack-asset:demo.pack:../evil.png", "pack-asset:other.pack:assets/a.png",
        "snapshot:not-a-uuid:1.png", "unknown:thing",
    ]:
        with pytest.raises(Exception): resolver.resolve(bad)
    with pytest.raises(Exception): resolver.resolve_ui_url("file:///tmp/x")
    with pytest.raises(Exception): resolver.resolve_ui_url("https://example.invalid/x")


def test_phase6_bootstrap_builds_bridge_and_ui_without_window(tmp_path):
    locator = ResourceLocator(base=Path.cwd())
    runtime = bootstrap_phase6(tmp_path / "data", file_picker=DeterministicFilePicker(None), resource_locator=locator)
    try:
        assert runtime.ui_index == Path.cwd() / "ui" / "index.html"
        assert runtime.ui_index.is_file()
        assert runtime.bridge.get_packs()["ok"] is True
        assert not hasattr(runtime, "window")
    finally:
        runtime.close()

def test_phase6_bootstrap_default_resource_locator(tmp_path):
    runtime = bootstrap_phase6(tmp_path / "default-locator", file_picker=DeterministicFilePicker(None))
    try:
        assert runtime.ui_index.is_file()
        assert runtime.ui_index.name == "index.html"
    finally:
        runtime.close()
