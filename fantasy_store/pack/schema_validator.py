from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.runtime.resource_locator import ResourceLocator


class LocalSchemaValidator:
    def __init__(self, resource_locator: ResourceLocator | None = None) -> None:
        locator = resource_locator or ResourceLocator()
        self._pack_schema = self._load(locator.resolve("resources/schema/pack-v1.json"))
        self._items_schema = self._load(locator.resolve("resources/schema/items-v1.json"))
        Draft202012Validator.check_schema(self._pack_schema)
        Draft202012Validator.check_schema(self._items_schema)
        self._pack_validator = Draft202012Validator(self._pack_schema)
        self._items_validator = Draft202012Validator(self._items_schema)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as fp:
            value = json.load(fp)
        if not isinstance(value, dict):
            raise RuntimeError("local JSON Schema root must be an object")
        return value

    @staticmethod
    def _validate(validator: Draft202012Validator, value: Any, label: str) -> None:
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        if errors:
            first = errors[0]
            location = "/".join(str(p) for p in first.absolute_path) or "$"
            raise PackValidationError(f"{label} schema violation at {location}: {first.message}")

    def validate_pack(self, value: Any) -> None:
        self._validate(self._pack_validator, value, "pack.json")

    def validate_items(self, value: Any) -> None:
        self._validate(self._items_validator, value, "items.json")
