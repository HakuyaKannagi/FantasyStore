import pytest

from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.money import MoneyLiteral, MoneyValue, price_sort_key, sum_money


def m(sig: str, exp: str = "0") -> MoneyValue:
    return MoneyValue.from_literal(MoneyLiteral(sig, exp))


def test_money_literal_accepts_boundaries():
    assert MoneyLiteral("0", "0").is_zero
    assert MoneyLiteral("385", "8").as_dict() == {"significand": "385", "exponent": "8"}
    MoneyLiteral("1" * 64, "9000000000000000000")


@pytest.mark.parametrize("sig,exp", [
    ("00", "0"), ("01", "0"), ("10", "0"), ("0", "1"),
    ("1", "00"), ("1" * 65, "0"), ("1", "9000000000000000001"),
    ("１", "0"), ("1", "１"),
])
def test_money_literal_rejects_noncanonical(sig, exp):
    with pytest.raises(ValidationError):
        MoneyLiteral(sig, exp)


def test_literal_conversion_is_exact_and_sparse():
    value = m("385", "8")
    assert value.to_canonical_obj() == {
        "terms": [
            {"significand": "38", "exponent": "9"},
            {"significand": "500000000", "exponent": "0"},
        ]
    }
    huge = m("1", "1000000") + m("1", "0")
    assert huge.block_count == 2
    assert huge.to_canonical_obj() == {
        "terms": [
            {"significand": "10", "exponent": "999999"},
            {"significand": "1", "exponent": "0"},
        ]
    }


def test_addition_carry_and_comparison():
    a = MoneyValue.from_canonical_obj({"terms": [{"significand": "999999999", "exponent": "0"}]})
    assert (a + m("1")).to_canonical_obj() == {
        "terms": [{"significand": "1", "exponent": "9"}]
    }
    assert m("1", "100") > m("9", "99")
    assert m("0") < m("1")
    assert m("123") == m("123")


def test_quantity_multiply_and_multi_line_sum_round_trip():
    unit = m("385", "8")
    line = unit.multiply_quantity(999)
    total = sum_money([line, m("7", "1000"), m("1")])
    restored = MoneyValue.from_canonical_obj(total.to_canonical_obj())
    assert restored == total
    assert restored > line


def test_canonical_money_value_rejects_noncanonical_terms():
    bad_values = [
        {"terms": [{"significand": "01", "exponent": "0"}]},
        {"terms": [{"significand": "1000000000", "exponent": "0"}]},
        {"terms": [{"significand": "1", "exponent": "1"}]},
        {"terms": [
            {"significand": "1", "exponent": "0"},
            {"significand": "1", "exponent": "9"},
        ]},
    ]
    for value in bad_values:
        with pytest.raises(ValidationError):
            MoneyValue.from_canonical_obj(value)


def test_price_sort_key_matches_frozen_definition():
    assert price_sort_key(MoneyLiteral("0", "0")) == (-1, "0" * 64)
    magnitude, digits = price_sort_key(MoneyLiteral("385", "8"))
    assert magnitude == 10
    assert len(digits) == 64
    assert digits.startswith("385")
    assert price_sort_key(MoneyLiteral("9", "99")) < price_sort_key(MoneyLiteral("1", "100"))


def test_canonical_json_persistence_round_trip():
    original = m("385", "8") + m("7", "1000")
    text = original.to_canonical_json()
    assert MoneyValue.from_canonical_json(text) == original
    with pytest.raises(ValidationError):
        MoneyValue.from_canonical_json('{"terms": [ {"significand":"1","exponent":"0"} ]}')
