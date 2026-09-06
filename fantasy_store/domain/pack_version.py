from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from fantasy_store.domain.errors import PackValidationError

PACK_VERSION_PATTERN = r"^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$"
_PACK_VERSION_RE = re.compile(PACK_VERSION_PATTERN)


@dataclass(frozen=True, order=True, slots=True)
class PackVersion:
    major: int
    minor: int

    @classmethod
    def parse(cls, value: object) -> "PackVersion":
        if not isinstance(value, str):
            raise PackValidationError("Pack version must be MAJOR.MINOR text")
        match = _PACK_VERSION_RE.fullmatch(value)
        if match is None:
            raise PackValidationError("Pack version must match MAJOR.MINOR with each component 0..999 and no leading zeros")
        return cls(int(match.group(1)), int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


class PackImportClassification(StrEnum):
    NEW = "NEW"
    UPGRADE = "UPGRADE"
    REINSTALL = "REINSTALL"
    DOWNGRADE_SKIPPED = "DOWNGRADE_SKIPPED"


def classify_pack_version(incoming: str, installed: str | None) -> PackImportClassification:
    incoming_value = PackVersion.parse(incoming)
    if installed is None:
        return PackImportClassification.NEW
    installed_value = PackVersion.parse(installed)
    if incoming_value > installed_value:
        return PackImportClassification.UPGRADE
    if incoming_value == installed_value:
        return PackImportClassification.REINSTALL
    return PackImportClassification.DOWNGRADE_SKIPPED
