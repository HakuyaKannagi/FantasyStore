from __future__ import annotations

import re
import uuid

from .errors import ValidationError

_PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$")
_ITEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED_WINDOWS_BASENAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def new_uuid_v4() -> str:
    return str(uuid.uuid4())


def validate_uuid_v4(value: str) -> str:
    if not isinstance(value, str) or not _UUID_V4_RE.fullmatch(value):
        raise ValidationError("UUID must be a lowercase hyphenated UUID v4")
    return value


def _validate_windows_segment_tail(value: str, label: str) -> None:
    if value.endswith((".", " ")):
        raise ValidationError(f"{label} must not end with a dot or space")


def validate_pack_id(value: str) -> str:
    if not isinstance(value, str) or not _PACK_ID_RE.fullmatch(value):
        raise ValidationError("pack_id does not match the frozen ASCII identifier format")
    _validate_windows_segment_tail(value, "pack_id")
    basename = value.split(".", 1)[0].casefold()
    if basename in _RESERVED_WINDOWS_BASENAMES:
        raise ValidationError("pack_id uses a reserved Windows basename")
    return value


def validate_item_id(value: str) -> str:
    if not isinstance(value, str) or not _ITEM_ID_RE.fullmatch(value):
        raise ValidationError("item_id does not match the frozen ASCII identifier format")
    _validate_windows_segment_tail(value, "item_id")
    return value
