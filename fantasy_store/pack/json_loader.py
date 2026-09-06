from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fantasy_store.domain.errors import PackValidationError

_UTF16_32_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PackValidationError(f"non-standard JSON numeric constant is forbidden: {value}")


def loads_strict_json_bytes(data: bytes) -> Any:
    if any(data.startswith(bom) for bom in _UTF16_32_BOMS):
        raise PackValidationError("UTF-16/UTF-32 JSON is forbidden")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PackValidationError("JSON is not valid UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_pairs_no_duplicates, parse_constant=_reject_constant)
    except PackValidationError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise PackValidationError(f"malformed JSON: {exc}") from exc


def load_strict_json(path: Path) -> Any:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise PackValidationError(f"JSON read failed: {exc}") from exc
    return loads_strict_json_bytes(data)
