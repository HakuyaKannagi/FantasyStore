from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Collection, Iterable

from fantasy_store.domain.errors import PackValidationError
from .path_resolver import ensure_contained, normalize_image_reference


class ImageReferenceValidator:
    def __init__(self, pack_root: Path, normalized_members: Collection[str], validated_images: Collection[str]) -> None:
        self.pack_root = Path(pack_root).resolve()
        self.assets_root = ensure_contained(self.pack_root, self.pack_root / "assets")
        self.member_map: dict[str, str] = {}
        self.case_map: dict[str, str] = {}
        for member in normalized_members:
            key = unicodedata.normalize("NFC", member)
            if key in self.member_map and self.member_map[key] != member:
                raise PackValidationError("ambiguous NFC member mapping")
            self.member_map[key] = member
            case_key = key.casefold()
            if case_key in self.case_map and self.case_map[case_key] != member:
                raise PackValidationError("ambiguous case-insensitive member mapping")
            self.case_map[case_key] = member
        self.validated_images = {unicodedata.normalize("NFC", p) for p in validated_images}

    def validate_one(self, reference: str) -> str:
        normalized = normalize_image_reference(reference)
        nfc_key = unicodedata.normalize("NFC", normalized)
        exact = self.member_map.get(nfc_key)
        case = self.case_map.get(nfc_key.casefold())
        if exact is None or case is None or exact != case:
            raise PackValidationError("image reference does not uniquely match a validated ZIP member")
        if nfc_key not in self.validated_images:
            raise PackValidationError("image reference target has not passed image validation")
        candidate = ensure_contained(self.assets_root, self.pack_root / Path(*nfc_key.split("/")))
        ensure_contained(self.pack_root, candidate)
        if not candidate.is_file() or candidate.is_symlink():
            raise PackValidationError("image reference target is not a regular file")
        return nfc_key

    def validate_many(self, references: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen_nfc: set[str] = set()
        for reference in references:
            value = self.validate_one(reference)
            key = unicodedata.normalize("NFC", value)
            if key in seen_nfc:
                raise PackValidationError("image reference NFC collision")
            seen_nfc.add(key)
            normalized.append(value)
        return tuple(normalized)
