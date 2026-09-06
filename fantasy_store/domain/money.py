from __future__ import annotations

from dataclasses import dataclass
import json
from functools import total_ordering
from typing import Any, Iterable, Mapping

from fantasy_store.config import (
    MONEY_BASE,
    MONEY_BLOCK_DIGITS,
    MONEY_MAX_EXPONENT,
    MONEY_MAX_SIGNIFICAND_DIGITS,
)
from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class MoneyLiteral:
    significand: str
    exponent: str

    def __post_init__(self) -> None:
        _validate_money_literal_parts(self.significand, self.exponent)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MoneyLiteral":
        if set(value) != {"significand", "exponent"}:
            raise ValidationError("MoneyLiteral must contain exactly significand and exponent")
        return cls(significand=value["significand"], exponent=value["exponent"])

    @property
    def exponent_int(self) -> int:
        return int(self.exponent)

    @property
    def is_zero(self) -> bool:
        return self.significand == "0"

    def as_dict(self) -> dict[str, str]:
        return {"significand": self.significand, "exponent": self.exponent}


def _validate_money_literal_parts(significand: Any, exponent: Any) -> None:
    if not isinstance(significand, str) or not isinstance(exponent, str):
        raise ValidationError("MoneyLiteral fields must be decimal strings")
    if not (1 <= len(significand) <= MONEY_MAX_SIGNIFICAND_DIGITS) or not significand.isascii() or not significand.isdigit():
        raise ValidationError("significand must be a 1-64 digit ASCII decimal string")
    if significand != "0":
        if significand[0] == "0":
            raise ValidationError("non-zero significand must not have a leading zero")
        if significand[-1] == "0":
            raise ValidationError("non-zero significand must not have a trailing zero")
    if not exponent or not exponent.isascii() or not exponent.isdigit():
        raise ValidationError("exponent must be an ASCII non-negative decimal string")
    if len(exponent) > 1 and exponent[0] == "0":
        raise ValidationError("exponent must be canonical without leading zeros")
    exponent_int = int(exponent)
    if exponent_int > MONEY_MAX_EXPONENT:
        raise ValidationError("exponent exceeds the frozen MoneyLiteral maximum")
    if significand == "0" and exponent != "0":
        raise ValidationError("zero has exactly one canonical representation: 0 × 10^0")


@total_ordering
class MoneyValue:
    """Exact, sparse base-10^9 non-negative money value.

    The mapping is {block_index: block_value}; block_index N represents a
    coefficient multiplied by 10^(9*N). Zero blocks are never stored.
    """

    __slots__ = ("_blocks",)

    def __init__(self, blocks: Mapping[int, int] | None = None) -> None:
        normalized: dict[int, int] = {}
        if blocks:
            for index, value in blocks.items():
                if not isinstance(index, int) or index < 0:
                    raise ValidationError("MoneyValue block index must be a non-negative integer")
                if not isinstance(value, int) or not (0 <= value < MONEY_BASE):
                    raise ValidationError("MoneyValue block value must be in base-10^9 range")
                if value:
                    normalized[index] = value
        self._blocks = normalized

    @classmethod
    def zero(cls) -> "MoneyValue":
        return cls()

    @classmethod
    def from_literal(cls, literal: MoneyLiteral | Mapping[str, Any]) -> "MoneyValue":
        if not isinstance(literal, MoneyLiteral):
            literal = MoneyLiteral.from_mapping(literal)
        if literal.is_zero:
            return cls.zero()

        exponent = literal.exponent_int
        block_offset, residual_digits = divmod(exponent, MONEY_BLOCK_DIGITS)
        # Only the <=64 digit significand plus at most 8 residual zeros is
        # materialized. The potentially huge exponent is represented solely
        # by block indices.
        compact = int(literal.significand) * (10 ** residual_digits)
        blocks: dict[int, int] = {}
        index = block_offset
        while compact:
            compact, block = divmod(compact, MONEY_BASE)
            if block:
                blocks[index] = block
            index += 1
        return cls(blocks)

    @classmethod
    def from_canonical_obj(cls, value: Mapping[str, Any]) -> "MoneyValue":
        if set(value) != {"terms"} or not isinstance(value["terms"], list):
            raise ValidationError("canonical MoneyValue must contain exactly a terms array")
        blocks: dict[int, int] = {}
        previous_exponent: int | None = None
        for term in value["terms"]:
            if not isinstance(term, Mapping) or set(term) != {"significand", "exponent"}:
                raise ValidationError("each MoneyValue term must contain significand and exponent")
            sig = term["significand"]
            exp = term["exponent"]
            if not isinstance(sig, str) or not sig.isascii() or not sig.isdigit() or sig.startswith("0"):
                raise ValidationError("MoneyValue term significand must be canonical decimal")
            sig_int = int(sig)
            if not (1 <= sig_int < MONEY_BASE):
                raise ValidationError("MoneyValue term significand must be 1..999999999")
            if not isinstance(exp, str) or not exp.isascii() or not exp.isdigit() or (len(exp) > 1 and exp.startswith("0")):
                raise ValidationError("MoneyValue term exponent must be canonical decimal")
            exp_int = int(exp)
            if exp_int % MONEY_BLOCK_DIGITS:
                raise ValidationError("MoneyValue term exponent must be a multiple of 9")
            if previous_exponent is not None and exp_int >= previous_exponent:
                raise ValidationError("MoneyValue terms must be in strict exponent-descending order")
            previous_exponent = exp_int
            index = exp_int // MONEY_BLOCK_DIGITS
            if index in blocks:
                raise ValidationError("MoneyValue terms must not duplicate an exponent")
            blocks[index] = sig_int
        return cls(blocks)

    @property
    def is_zero(self) -> bool:
        return not self._blocks

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    def to_canonical_obj(self) -> dict[str, list[dict[str, str]]]:
        return {
            "terms": [
                {"significand": str(self._blocks[index]), "exponent": str(index * MONEY_BLOCK_DIGITS)}
                for index in sorted(self._blocks, reverse=True)
            ]
        }

    def to_canonical_json(self) -> str:
        return json.dumps(self.to_canonical_obj(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_canonical_json(cls, text: str) -> "MoneyValue":
        if not isinstance(text, str):
            raise ValidationError("canonical MoneyValue JSON must be text")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError("invalid canonical MoneyValue JSON") from exc
        if not isinstance(value, Mapping):
            raise ValidationError("canonical MoneyValue JSON root must be an object")
        result = cls.from_canonical_obj(value)
        # Persistence accepts one canonical byte-level serialization so later
        # Repository code cannot silently preserve alternate equivalent JSON.
        if text != result.to_canonical_json():
            raise ValidationError("MoneyValue JSON is not in canonical serialization form")
        return result

    def _add_block(self, target: dict[int, int], index: int, amount: int) -> None:
        while amount:
            total = target.get(index, 0) + amount
            carry, block = divmod(total, MONEY_BASE)
            if block:
                target[index] = block
            else:
                target.pop(index, None)
            amount = carry
            index += 1

    def __add__(self, other: object) -> "MoneyValue":
        if not isinstance(other, MoneyValue):
            return NotImplemented
        target = dict(self._blocks)
        for index, value in other._blocks.items():
            self._add_block(target, index, value)
        return MoneyValue(target)

    def __radd__(self, other: object) -> "MoneyValue":
        if other == 0:
            return self
        return self.__add__(other)

    def multiply_quantity(self, quantity: int) -> "MoneyValue":
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise ValidationError("quantity multiplier must be a non-negative integer")
        if quantity == 0 or self.is_zero:
            return MoneyValue.zero()
        target: dict[int, int] = {}
        for index, value in self._blocks.items():
            self._add_block(target, index, value * quantity)
        return MoneyValue(target)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MoneyValue) and self._blocks == other._blocks

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, MoneyValue):
            return NotImplemented
        if self.is_zero:
            return not other.is_zero
        if other.is_zero:
            return False
        left_max = max(self._blocks)
        right_max = max(other._blocks)
        if left_max != right_max:
            return left_max < right_max
        for index in sorted(set(self._blocks) | set(other._blocks), reverse=True):
            left = self._blocks.get(index, 0)
            right = other._blocks.get(index, 0)
            if left != right:
                return left < right
        return False

    def __repr__(self) -> str:
        return f"MoneyValue({self.to_canonical_obj()!r})"


def sum_money(values: Iterable[MoneyValue]) -> MoneyValue:
    total = MoneyValue.zero()
    for value in values:
        total = total + value
    return total


def price_sort_key(literal: MoneyLiteral | Mapping[str, Any]) -> tuple[int, str]:
    if not isinstance(literal, MoneyLiteral):
        literal = MoneyLiteral.from_mapping(literal)
    if literal.is_zero:
        return -1, "0" * MONEY_MAX_SIGNIFICAND_DIGITS
    magnitude = literal.exponent_int + len(literal.significand) - 1
    digits = literal.significand.ljust(MONEY_MAX_SIGNIFICAND_DIGITS, "0")
    return magnitude, digits
