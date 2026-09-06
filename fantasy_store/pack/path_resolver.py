from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.domain.ids import validate_pack_id, validate_uuid_v4
from fantasy_store.runtime.paths import AppPaths

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def _contains_forbidden_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def normalize_image_reference(reference: str) -> str:
    if not isinstance(reference, str) or not reference:
        raise PackValidationError("image reference must be non-empty text")
    if "\x00" in reference or _contains_forbidden_control(reference):
        raise PackValidationError("image reference contains NUL/control characters")
    if reference.startswith(("/", "\\")) or reference.startswith("//") or reference.startswith("\\\\"):
        raise PackValidationError("absolute/UNC image reference is forbidden")
    if _DRIVE_RE.match(reference):
        raise PackValidationError("drive-letter image reference is forbidden")
    normalized = unicodedata.normalize("NFC", reference.replace("\\", "/"))
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise PackValidationError("normalized image reference is absolute")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise PackValidationError("image reference contains empty/dot traversal segment")
    for segment in segments:
        if segment.endswith((" ", ".")):
            raise PackValidationError("image reference segment ends with dot/space")
        if segment.split(".", 1)[0].casefold() in _RESERVED:
            raise PackValidationError("image reference uses reserved Windows basename")
    if segments[0] != "assets" or len(segments) < 2:
        raise PackValidationError("image reference must be under assets/")
    if Path(segments[-1]).suffix.casefold() not in _ALLOWED_EXT:
        raise PackValidationError("image reference extension is not allowed")
    return "/".join(segments)


def ensure_contained(root: Path, candidate: Path) -> Path:
    root_resolved = Path(root).resolve()
    candidate_resolved = Path(candidate).resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise PackValidationError("resolved path escapes required root")
    return candidate_resolved


class PackPathResolver:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.persistent_root = paths.root.resolve()
        self.packs_root = ensure_contained(self.persistent_root, paths.packs)
        self.staging_base = ensure_contained(self.packs_root, paths.staging_packs)
        self.installed_base = ensure_contained(self.packs_root, paths.installed_packs)
        self.backup_base = ensure_contained(self.packs_root, paths.pack_backups)

    def staging_root(self, operation_id: str) -> Path:
        validate_uuid_v4(operation_id)
        return ensure_contained(self.staging_base, self.staging_base / operation_id)

    def installed_pack_root(self, pack_id: str) -> Path:
        validate_pack_id(pack_id)
        return ensure_contained(self.installed_base, self.installed_base / pack_id)

    def backup_pack_root(self, operation_id: str, pack_id: str) -> Path:
        validate_uuid_v4(operation_id)
        validate_pack_id(pack_id)
        operation_root = ensure_contained(self.backup_base, self.backup_base / operation_id)
        return ensure_contained(operation_root, operation_root / pack_id)

    def relative_to_persistent(self, path: Path) -> str:
        resolved = ensure_contained(self.persistent_root, path)
        return resolved.relative_to(self.persistent_root).as_posix()

    def resolve_recorded_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or _DRIVE_RE.match(relative):
            raise PackValidationError("recorded pack path must be persistent-root relative")
        normalized = unicodedata.normalize("NFC", relative.replace("\\", "/"))
        segments = normalized.split("/")
        if any(seg in {"", ".", ".."} for seg in segments):
            raise PackValidationError("recorded pack path contains unsafe segment")
        return ensure_contained(self.persistent_root, self.persistent_root / Path(*segments))

    def asset_path(self, pack_root: Path, reference: str) -> Path:
        normalized = normalize_image_reference(reference)
        root = ensure_contained(self.packs_root, Path(pack_root))
        assets_root = ensure_contained(root, root / "assets")
        candidate = ensure_contained(assets_root, root / Path(*normalized.split("/")))
        ensure_contained(root, candidate)
        return candidate
