from __future__ import annotations

import pytest

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.image_reference_validator import ImageReferenceValidator
from fantasy_store.pack.validator import PackSemanticValidator
from tests.phase3_helpers import base_items, base_pack, image_bytes


def refs(tmp_path):
    root=tmp_path/"p"; (root/"assets").mkdir(parents=True); (root/"assets/a.png").write_bytes(image_bytes("PNG"))
    return ImageReferenceValidator(root,{"assets/a.png"},{"assets/a.png"})


def test_prepare_item_record_money_and_canonical_json(tmp_path):
    _, records=PackSemanticValidator().validate_and_prepare(base_pack(), base_items(price={"significand":"123","exponent":"9000000000000000000"}), refs(tmp_path))
    r=records[0]
    assert r.price_significand=="123" and r.price_exponent==9000000000000000000
    assert r.price_magnitude==9000000000000000002 and len(r.price_sort_digits)==64
    assert r.attributes_json == '{"color":"blue","flag":true,"rank":2}'
    assert r.images_json == '["assets/a.png"]'
    assert "Item One" in r.search_text and "blue" in r.search_text

@pytest.mark.parametrize("price", [
    {"significand":"0","exponent":"0"},
    {"significand":"1","exponent":"9000000000000000000"},
])
def test_money_valid_boundaries(tmp_path, price):
    _,records=PackSemanticValidator().validate_and_prepare(base_pack(), base_items(price=price), refs(tmp_path)); assert records

@pytest.mark.parametrize("price", [
    {"significand":"01","exponent":"0"}, {"significand":"10","exponent":"0"},
    {"significand":"0","exponent":"1"}, {"significand":"1","exponent":"9000000000000000001"},
])
def test_money_invalid_matches_moneyliteral(tmp_path, price):
    with pytest.raises(PackValidationError): PackSemanticValidator().validate_and_prepare(base_pack(), base_items(price=price), refs(tmp_path))


def test_duplicate_item_id(tmp_path):
    d=base_items(); d["items"].append(dict(d["items"][0]))
    with pytest.raises(PackValidationError): PackSemanticValidator().validate_and_prepare(base_pack(), d, refs(tmp_path))


def test_pack_reserved_name(tmp_path):
    p=base_pack("con.txt")
    with pytest.raises(PackValidationError): PackSemanticValidator().validate_and_prepare(p, base_items(), refs(tmp_path))


def test_attributes_64k_limit(tmp_path):
    d=base_items(attrs={"x":"z"*(64*1024)})
    with pytest.raises(PackValidationError): PackSemanticValidator().validate_and_prepare(base_pack(), d, refs(tmp_path))


def test_control_char_rejected(tmp_path):
    d=base_items(); d["items"][0]["description"]="bad\x01"
    with pytest.raises(PackValidationError): PackSemanticValidator().validate_and_prepare(base_pack(), d, refs(tmp_path))
