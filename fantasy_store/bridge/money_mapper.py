from __future__ import annotations

from fantasy_store.domain.money import MoneyValue

_NORMAL_INTEGER_MAGNITUDE_MAX = 15  # value < 10^16
_MAX_COEFFICIENT_DIGITS = 9
_SUPERSCRIPT = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _superscript(value: int) -> str:
    return str(value).translate(_SUPERSCRIPT)


def _terms(value: MoneyValue) -> list[dict[str, str]]:
    return value.to_canonical_obj()["terms"]


def _magnitude(terms: list[dict[str, str]]) -> int:
    top = terms[0]
    return int(top["exponent"]) + len(top["significand"]) - 1


def _exact_small_integer(terms: list[dict[str, str]]) -> int:
    # Called only below 10^16, so materialization is bounded and tiny.
    return sum(int(term["significand"]) * (10 ** int(term["exponent"])) for term in terms)


def _lowest_nonzero_exponent(terms: list[dict[str, str]]) -> int:
    low = terms[-1]
    digits = low["significand"]
    trailing = len(digits) - len(digits.rstrip("0"))
    return int(low["exponent"]) + trailing


def _exact_compact_coefficient(terms: list[dict[str, str]], exponent: int) -> int:
    # Used only when the whole significant span is <=9 digits. The lowest
    # canonical base-10^9 block may itself contain trailing zeros, so factor
    # those zeros out with integer division instead of a negative power.
    total = 0
    for term in terms:
        sig = int(term["significand"])
        delta = int(term["exponent"]) - exponent
        if delta >= 0:
            total += sig * (10 ** delta)
        else:
            total += sig // (10 ** (-delta))
    return total


def _leading_digits(terms: list[dict[str, str]], count: int) -> str:
    """Return the leading decimal digits without expanding a huge exponent gap."""
    top = terms[0]
    top_index = int(top["exponent"]) // 9
    by_index = {int(term["exponent"]) // 9: int(term["significand"]) for term in terms}
    text = top["significand"]
    index = top_index - 1
    while len(text) < count:
        # At most two blocks are needed for 10 leading digits because a block is
        # 9 digits wide. Missing blocks are exactly nine zeros.
        text += f"{by_index.get(index, 0):09d}"
        index -= 1
    return text[:count]


def money_display(value: MoneyValue) -> str:
    terms = _terms(value)
    if not terms:
        return "0"

    magnitude = _magnitude(terms)
    if magnitude <= _NORMAL_INTEGER_MAGNITUDE_MAX:
        return f"{_exact_small_integer(terms):,}"

    low_exponent = _lowest_nonzero_exponent(terms)
    significant_span = magnitude - low_exponent + 1
    if significant_span <= _MAX_COEFFICIENT_DIGITS:
        coefficient = _exact_compact_coefficient(terms, low_exponent)
        return f"{coefficient} × 10{_superscript(low_exponent)}"

    prefix = _leading_digits(terms, _MAX_COEFFICIENT_DIGITS + 1)
    coefficient = int(prefix[:_MAX_COEFFICIENT_DIGITS])
    round_digit = int(prefix[_MAX_COEFFICIENT_DIGITS])
    if round_digit >= 5:
        coefficient += 1
    exponent = magnitude - (_MAX_COEFFICIENT_DIGITS - 1)
    if coefficient >= 10 ** _MAX_COEFFICIENT_DIGITS:
        coefficient //= 10
        exponent += 1
    return f"約 {coefficient} × 10{_superscript(exponent)}"


def money_to_dto(value: MoneyValue) -> dict[str, object]:
    obj = value.to_canonical_obj()
    return {
        "terms": [
            {"significand": term["significand"], "exponent": term["exponent"]}
            for term in obj["terms"]
        ],
        "display": money_display(value),
    }
