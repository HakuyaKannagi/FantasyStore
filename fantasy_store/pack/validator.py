from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from fantasy_store.config import VPACK_MAX_ATTRIBUTES_JSON_BYTES
from fantasy_store.domain.errors import PackValidationError, ValidationError
from fantasy_store.domain.ids import validate_item_id, validate_pack_id
from fantasy_store.domain.money import MoneyLiteral, price_sort_key
from .image_reference_validator import ImageReferenceValidator


def _contains_forbidden_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _walk_strings(value: Any) -> None:
    if isinstance(value, str):
        if "\x00" in value or _contains_forbidden_control(value):
            raise PackValidationError("JSON string contains NUL/control characters")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _walk_strings(key)
            _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            _walk_strings(child)


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PackValidationError(f"value cannot be canonically serialized: {exc}") from exc


def _search_scalar(value: Any) -> str:
    if isinstance(value, str):
        return value
    return canonical_json(value)


def make_search_text(item: Mapping[str, Any]) -> str:
    # Freeze text does not define case-folding or compatibility folding.
    # NFC is lossless normalization and preserves displayed/searchable content.
    values = [item["name"], item["category"], item["description"]]
    for key in sorted(item["attributes"]):
        values.append(_search_scalar(item["attributes"][key]))
    return "\n".join(unicodedata.normalize("NFC", str(v)) for v in values)


@dataclass(frozen=True, slots=True)
class PreparedItemRecord:
    pack_id: str
    item_id: str
    item_name: str
    price_significand: str
    price_exponent: int
    price_magnitude: int
    price_sort_digits: str
    category: str
    description: str
    attributes_json: str
    images_json: str
    search_text: str


class PackSemanticValidator:
    def validate_and_prepare(
        self,
        pack: Mapping[str, Any],
        items_doc: Mapping[str, Any],
        image_refs: ImageReferenceValidator,
    ) -> tuple[dict[str, Any], tuple[PreparedItemRecord, ...]]:
        _walk_strings(pack)
        _walk_strings(items_doc)
        try:
            pack_id = validate_pack_id(pack["pack_id"])
        except ValidationError as exc:
            raise PackValidationError(exc.message) from exc

        prepared_items: list[PreparedItemRecord] = []
        validated_items: list[dict[str, Any]] = []
        item_ids: set[str] = set()
        item_nfc: set[str] = set()
        for raw in items_doc["items"]:
            try:
                item_id = validate_item_id(raw["item_id"])
            except ValidationError as exc:
                raise PackValidationError(exc.message) from exc
            if item_id in item_ids:
                raise PackValidationError("duplicate Item ID in items.json")
            item_ids.add(item_id)
            nfc_id = unicodedata.normalize("NFC", item_id)
            if nfc_id in item_nfc:
                raise PackValidationError("Item ID NFC collision")
            item_nfc.add(nfc_id)
            try:
                money = MoneyLiteral.from_mapping(raw["price"])
            except ValidationError as exc:
                raise PackValidationError(exc.message) from exc
            attrs_json = canonical_json(raw["attributes"])
            if len(attrs_json.encode("utf-8")) > VPACK_MAX_ATTRIBUTES_JSON_BYTES:
                raise PackValidationError("attributes canonical JSON exceeds 64 KiB")
            normalized_images = image_refs.validate_many(raw["images"])
            images_json = canonical_json(list(normalized_images))
            magnitude, sort_digits = price_sort_key(money)
            normalized_item = dict(raw)
            normalized_item["images"] = list(normalized_images)
            validated_items.append(normalized_item)
            prepared_items.append(
                PreparedItemRecord(
                    pack_id=pack_id,
                    item_id=item_id,
                    item_name=raw["name"],
                    price_significand=money.significand,
                    price_exponent=money.exponent_int,
                    price_magnitude=magnitude,
                    price_sort_digits=sort_digits,
                    category=raw["category"],
                    description=raw["description"],
                    attributes_json=attrs_json,
                    images_json=images_json,
                    search_text=make_search_text(raw),
                )
            )
        result = dict(items_doc)
        result["items"] = validated_items
        return result, tuple(prepared_items)
