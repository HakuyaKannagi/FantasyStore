from __future__ import annotations

import json
import threading

import pytest

from fantasy_store.domain.errors import DomainError, ProductNotAvailableError, ValidationError
from fantasy_store.domain.money import MoneyLiteral, MoneyValue
from tests.phase5_helpers import item, make_pack_file, setup_phase5


def test_catalog_no_packs_and_categories_empty(tmp_path):
    env = setup_phase5(tmp_path)
    result = env.catalog.get_products()
    assert result.catalog_state == "NO_PACKS" and result.items == () and result.total == 0
    assert env.catalog.get_categories() == ()


def test_catalog_all_disabled(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1")])])
    env.lifecycle.set_pack_enabled("pack.one", False)
    result = env.catalog.get_products()
    assert result.catalog_state == "ALL_PACKS_DISABLED" and result.total == 0
    assert env.catalog.get_categories() == ()


def test_catalog_ready_categories_distinct_and_search_zero(tmp_path):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1", category="B"), item("i2", category="A")]), ("pack.two", [item("j1", category="A")])])
    assert env.catalog.get_categories() == ("A", "B")
    assert env.catalog.get_products().catalog_state == "READY"
    assert env.catalog.get_products(query="does-not-exist").catalog_state == "NO_SEARCH_RESULTS"


@pytest.mark.parametrize("needle,name", [("%", "100% Real"), ("_", "under_score"), ("\\", "slash\\name")])
def test_catalog_like_metacharacters_are_literal(tmp_path, needle, name):
    env = setup_phase5(tmp_path, packs=[("pack.one", [item("i1", name=name), item("i2", name="other")])])
    result = env.catalog.get_products(query=needle)
    assert [x.item_id for x in result.items] == ["i1"]


def test_catalog_category_price_filters_huge_and_sort(tmp_path):
    items = [
        item("low", name="Z", category="C", price={"significand":"1","exponent":"0"}),
        item("mid", name="A", category="C", price={"significand":"2","exponent":"10"}),
        item("huge", name="M", category="D", price={"significand":"9","exponent":"9000000000000000000"}),
    ]
    env = setup_phase5(tmp_path, packs=[("pack.one", items)])
    assert [x.item_id for x in env.catalog.get_products(category="C", sort="price_asc").items] == ["low", "mid"]
    assert [x.item_id for x in env.catalog.get_products(sort="price_desc").items] == ["huge", "mid", "low"]
    assert [x.item_id for x in env.catalog.get_products(sort="name_asc").items] == ["mid", "huge", "low"]
    r = env.catalog.get_products(min_price={"significand":"2","exponent":"10"}, max_price={"significand":"2","exponent":"10"})
    assert [x.item_id for x in r.items] == ["mid"]
    r = env.catalog.get_products(min_price={"significand":"9","exponent":"9000000000000000000"})
    assert [x.item_id for x in r.items] == ["huge"]


def test_catalog_min_greater_than_max_rejected(tmp_path):
    env = setup_phase5(tmp_path)
    with pytest.raises(ValidationError):
        env.catalog.get_products(min_price={"significand":"2","exponent":"0"}, max_price={"significand":"1","exponent":"0"})


@pytest.mark.parametrize("kwargs", [
    {"sort":"bad"}, {"page":0}, {"page_size":0}, {"page_size":101}, {"query":"x"*201}, {"category":"x"*101}
])
def test_catalog_validation(tmp_path, kwargs):
    env=setup_phase5(tmp_path)
    with pytest.raises(ValidationError): env.catalog.get_products(**kwargs)


def test_catalog_pagination(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item(f"i{n}",name=f"N{n}") for n in range(5)])])
    r=env.catalog.get_products(page=2,page_size=2)
    assert r.total==5 and len(r.items)==2 and r.page==2


def test_catalog_busy_pack_image_null_other_pack_available(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")]),("pack.two",[item("j1")])])
    env.access.acquire_write("pack.one")
    try:
        r=env.catalog.get_products()
    finally:
        env.access.release_write("pack.one")
    by={x.pack_id:x for x in r.items}
    assert by["pack.one"].primary_image_ref is None
    assert by["pack.two"].primary_image_ref is not None


def test_product_detail_normal_disabled_missing_busy(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])],lock_timeout=0.02)
    assert env.catalog.get_product_detail("pack.one","i1").item_id=="i1"
    with pytest.raises(ProductNotAvailableError): env.catalog.get_product_detail("pack.one","missing")
    env.lifecycle.set_pack_enabled("pack.one",False)
    with pytest.raises(ProductNotAvailableError): env.catalog.get_product_detail("pack.one","i1")
    env.lifecycle.set_pack_enabled("pack.one",True)
    env.access.acquire_write("pack.one")
    try:
        with pytest.raises(DomainError) as exc: env.catalog.get_product_detail("pack.one","i1")
        assert exc.value.code=="PACK_BUSY"
    finally: env.access.release_write("pack.one")


def test_cart_add_accumulates_and_limits(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    assert env.cart.add_to_cart("pack.one","i1",5).line.quantity==5
    assert env.cart.add_to_cart("pack.one","i1",994).line.quantity==999
    with pytest.raises(DomainError) as exc: env.cart.add_to_cart("pack.one","i1",1)
    assert exc.value.code=="CART_QUANTITY_LIMIT"
    assert env.users.get_cart_item("pack.one","i1").quantity==999


@pytest.mark.parametrize("qty", [0,1000,-1,True])
def test_cart_quantity_validation(tmp_path,qty):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    with pytest.raises(ValidationError): env.cart.add_to_cart("pack.one","i1",qty)


def test_cart_update_remove_clear(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1"),item("i2")])])
    env.cart.add_to_cart("pack.one","i1",1); env.cart.add_to_cart("pack.one","i2",2)
    assert env.cart.update_cart_item("pack.one","i1",999).quantity==999
    assert env.cart.remove_cart_item("pack.one","missing") is False
    assert env.cart.remove_cart_item("pack.one","i1") is True
    assert env.cart.clear_cart()==1
    assert env.cart.clear_cart()==0


def test_cart_disabled_add_rejected_and_unavailable_line_retained(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1")])])
    env.cart.add_to_cart("pack.one","i1",2)
    env.lifecycle.set_pack_enabled("pack.one",False)
    with pytest.raises(ProductNotAvailableError): env.cart.add_to_cart("pack.one","i1",1)
    c=env.cart.get_cart()
    assert len(c.lines)==1 and c.lines[0].unavailable_reason=="PACK_DISABLED" and c.total_amount.is_zero


def test_cart_item_not_found_and_pack_not_installed_states(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[item("i1"),item("i2")])])
    env.cart.add_to_cart("pack.one","i1",1); env.cart.add_to_cart("pack.one","i2",1)
    with env.packs.transaction() as conn:
        conn.execute("DELETE FROM items_master WHERE pack_id=? AND item_id=?",("pack.one","i1"))
    c=env.cart.get_cart(); by={x.item_id:x for x in c.lines}
    assert by["i1"].unavailable_reason=="ITEM_NOT_FOUND" and by["i2"].available
    with env.packs.transaction() as conn:
        conn.execute("DELETE FROM installed_packs WHERE pack_id=?",("pack.one",))
    c=env.cart.get_cart()
    assert all(x.unavailable_reason=="PACK_NOT_INSTALLED" for x in c.lines)


def test_cart_exact_money_available_only(tmp_path):
    env=setup_phase5(tmp_path,packs=[("pack.one",[
        item("i1",price={"significand":"1","exponent":"9000000000000000000"}),
        item("i2",price={"significand":"1","exponent":"0"}),
    ])])
    env.cart.add_to_cart("pack.one","i1",2); env.cart.add_to_cart("pack.one","i2",3)
    env.lifecycle.set_pack_enabled("pack.one",False)
    assert env.cart.get_cart().total_amount.is_zero
    env.lifecycle.set_pack_enabled("pack.one",True)
    c=env.cart.get_cart()
    assert c.total_amount.block_count==2 and c.total_quantity==5
