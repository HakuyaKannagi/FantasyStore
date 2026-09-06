import pytest

from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.ids import new_uuid_v4, validate_item_id, validate_pack_id, validate_uuid_v4


def test_uuid_v4_is_lowercase_hyphenated_and_valid():
    value = new_uuid_v4()
    assert validate_uuid_v4(value) == value


@pytest.mark.parametrize("value", ["sample.pack", "abc", "a-b_c.1"])
def test_pack_id_accepts_frozen_shape(value):
    assert validate_pack_id(value) == value


@pytest.mark.parametrize("value", ["con.txt", "nul.foo", "com1.pack", "lpt9.data", "ab", "Aaa", "abc."])
def test_pack_id_rejects_reserved_or_invalid(value):
    with pytest.raises(ValidationError):
        validate_pack_id(value)


def test_item_id_shape():
    assert validate_item_id("x") == "x"
    assert validate_item_id("item_01") == "item_01"
    with pytest.raises(ValidationError):
        validate_item_id("Item")
