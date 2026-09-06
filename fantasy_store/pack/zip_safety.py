from __future__ import annotations

import os
import re
import shutil
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from fantasy_store.config import (
    VPACK_COMPRESSION_RATIO_MIN_SIZE,
    VPACK_MAX_COMPRESSION_RATIO,
    VPACK_MAX_EXPANDED_BYTES,
    VPACK_MAX_FILE_COUNT,
    VPACK_MAX_ITEMS_JSON_BYTES,
    VPACK_MAX_PACK_JSON_BYTES,
    VPACK_MAX_SINGLE_FILE_BYTES,
    VPACK_MAX_SOURCE_BYTES,
)
from fantasy_store.domain.errors import PackValidationError

_ALLOWED_ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ValidatedZipEntry:
    info: zipfile.ZipInfo
    normalized_path: str
    is_dir: bool


def _has_forbidden_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _validate_segment(segment: str) -> None:
    if segment in {"", ".", ".."}:
        raise PackValidationError("ZIP path contains an empty/dot traversal segment")
    if segment.endswith((" ", ".")):
        raise PackValidationError("ZIP path segment ends with a dot or space")
    base = segment.split(".", 1)[0].casefold()
    if base in _RESERVED:
        raise PackValidationError("ZIP path uses a reserved Windows basename")


def normalize_archive_path(name: str, *, directory_hint: bool = False) -> str:
    if not isinstance(name, str) or not name:
        raise PackValidationError("ZIP entry name must be non-empty text")
    if "\x00" in name or _has_forbidden_control(name):
        raise PackValidationError("ZIP entry name contains NUL/control characters")
    if name.startswith(("/", "\\")) or name.startswith("//") or name.startswith("\\\\"):
        raise PackValidationError("absolute/UNC ZIP path is forbidden")
    if "/" in name and "\\" in name:
        raise PackValidationError("mixed ZIP path separators are forbidden")
    if _DRIVE_RE.match(name):
        raise PackValidationError("drive-letter ZIP path is forbidden")

    normalized = unicodedata.normalize("NFC", name.replace("\\", "/"))
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise PackValidationError("normalized ZIP path is absolute")
    is_dir = directory_hint or normalized.endswith("/")
    core = normalized[:-1] if is_dir and normalized.endswith("/") else normalized
    segments = core.split("/")
    for segment in segments:
        _validate_segment(segment)
    return "/".join(segments) + ("/" if is_dir else "")


def _entry_kind(info: zipfile.ZipInfo) -> str:
    # DOS/Windows special attributes are rejected when present. In particular,
    # DEVICE and REPARSE_POINT cover device/junction-like entries represented
    # through Windows-origin ZIP metadata.
    if info.create_system == 0:
        dos_attrs = info.external_attr & 0xFFFF
        if dos_attrs & (0x0040 | 0x0400):
            raise PackValidationError("Windows device/reparse ZIP entry is forbidden")
    if info.is_dir() or info.filename.endswith("/"):
        expected = "dir"
    else:
        expected = "file"
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode:
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFLNK:
            raise PackValidationError("symlink ZIP entry is forbidden")
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise PackValidationError("special ZIP entry is forbidden")
        if kind == stat.S_IFDIR and expected != "dir":
            raise PackValidationError("ZIP metadata/file-name type mismatch")
        if kind == stat.S_IFREG and expected != "file":
            raise PackValidationError("ZIP metadata/file-name type mismatch")
    return expected


def validate_entry_sizes(info: zipfile.ZipInfo) -> None:
    if info.file_size < 0 or info.compress_size < 0:
        raise PackValidationError("negative ZIP size metadata")
    if info.file_size > VPACK_MAX_SINGLE_FILE_BYTES:
        raise PackValidationError("ZIP member exceeds 64 MiB single-file limit")
    normalized = normalize_archive_path(info.filename, directory_hint=info.is_dir())
    bare = normalized.rstrip("/")
    if bare == "pack.json" and info.file_size > VPACK_MAX_PACK_JSON_BYTES:
        raise PackValidationError("pack.json exceeds 256 KiB")
    if bare == "items.json" and info.file_size > VPACK_MAX_ITEMS_JSON_BYTES:
        raise PackValidationError("items.json exceeds 16 MiB")
    if info.file_size > 0 and info.compress_size == 0:
        raise PackValidationError("non-empty ZIP member has compressed_size=0")
    if info.file_size >= VPACK_COMPRESSION_RATIO_MIN_SIZE and info.compress_size > 0:
        if info.file_size / info.compress_size > VPACK_MAX_COMPRESSION_RATIO:
            raise PackValidationError("ZIP member compression ratio exceeds 100:1")


def validate_source_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackValidationError(f"cannot stat .vpack: {exc}") from exc
    if size > VPACK_MAX_SOURCE_BYTES:
        raise PackValidationError(".vpack exceeds 512 MiB source limit")


def _validate_allowed_layout(path: str, is_dir: bool) -> None:
    bare = path.rstrip("/")
    if is_dir:
        if bare == "assets" or bare.startswith("assets/"):
            return
        raise PackValidationError("only assets/ directories are allowed")
    if bare in {"pack.json", "items.json"}:
        return
    if bare.startswith("assets/"):
        ext = PurePosixPath(bare).suffix.casefold()
        if ext not in _ALLOWED_ASSET_EXTENSIONS:
            raise PackValidationError("asset extension is not allowed")
        return
    raise PackValidationError("root content outside pack.json/items.json/assets is forbidden")


def validate_zip_metadata(infos: Iterable[zipfile.ZipInfo]) -> list[ValidatedZipEntry]:
    validated: list[ValidatedZipEntry] = []
    raw_seen: set[str] = set()
    nfc_seen: set[str] = set()
    case_seen: set[str] = set()
    total = 0
    file_count = 0
    root_files: dict[str, int] = {"pack.json": 0, "items.json": 0}
    assets_present = False

    for info in infos:
        if info.filename in raw_seen:
            raise PackValidationError("duplicate ZIP member")
        raw_seen.add(info.filename)
        kind = _entry_kind(info)
        normalized = normalize_archive_path(info.filename, directory_hint=(kind == "dir"))
        collision_key = normalized.rstrip("/")
        if collision_key in nfc_seen:
            raise PackValidationError("Unicode NFC ZIP path collision")
        nfc_seen.add(collision_key)
        case_key = collision_key.casefold()
        if case_key in case_seen:
            raise PackValidationError("Windows case-insensitive ZIP path collision")
        case_seen.add(case_key)
        _validate_allowed_layout(normalized, kind == "dir")

        if kind == "file":
            validate_entry_sizes(info)
            file_count += 1
            total += info.file_size
            if file_count > VPACK_MAX_FILE_COUNT:
                raise PackValidationError("ZIP file count exceeds 5,000")
            if total > VPACK_MAX_EXPANDED_BYTES:
                raise PackValidationError("ZIP expanded total exceeds 1 GiB")
            if collision_key in root_files:
                root_files[collision_key] += 1
            if collision_key.startswith("assets/"):
                assets_present = True
        elif collision_key == "assets" or collision_key.startswith("assets/"):
            assets_present = True
        validated.append(ValidatedZipEntry(info, normalized, kind == "dir"))

    if root_files != {"pack.json": 1, "items.json": 1}:
        raise PackValidationError("pack.json and items.json must each exist exactly once at ZIP root")
    if not assets_present:
        raise PackValidationError("assets/ must exist")
    return validated


def prevalidate_archive(path: Path) -> tuple[zipfile.ZipFile, list[ValidatedZipEntry]]:
    path = Path(path)
    if path.suffix.casefold() != ".vpack":
        raise PackValidationError("input file must have .vpack extension")
    if not path.is_file():
        raise PackValidationError(".vpack input is not a regular file")
    validate_source_size(path)
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(path, "r")
        entries = validate_zip_metadata(archive.infolist())
        return archive, entries
    except PackValidationError:
        if archive is not None:
            archive.close()
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        if archive is not None:
            archive.close()
        raise PackValidationError(f"invalid ZIP archive: {exc}") from exc


def _ensure_contained(root: Path, destination: Path) -> Path:
    root_resolved = root.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved != root_resolved and root_resolved not in destination_resolved.parents:
        raise PackValidationError("extraction destination escapes staging root")
    return destination_resolved


def _copy_stream(src, dst, *, max_file: int, remaining_total: int) -> int:
    written = 0
    while True:
        chunk = src.read(min(_CHUNK, max_file - written + 1))
        if not chunk:
            break
        written += len(chunk)
        if written > max_file:
            raise PackValidationError("actual extracted member exceeds single-file limit")
        if written > remaining_total:
            raise PackValidationError("actual extracted total exceeds expanded-size limit")
        dst.write(chunk)
    return written


def safe_extract(archive: zipfile.ZipFile, entries: Iterable[ValidatedZipEntry], staging_root: Path) -> None:
    root = Path(staging_root)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise PackValidationError(f"staging creation failed: {exc}") from exc
    total_written = 0
    for entry in entries:
        rel = entry.normalized_path.rstrip("/")
        destination = _ensure_contained(root, root / Path(*rel.split("/")))
        if entry.is_dir:
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(entry.info, "r") as src, open(destination, "xb") as dst:
                actual = _copy_stream(
                    src,
                    dst,
                    max_file=VPACK_MAX_SINGLE_FILE_BYTES,
                    remaining_total=VPACK_MAX_EXPANDED_BYTES - total_written,
                )
        except PackValidationError:
            raise
        except Exception as exc:
            raise PackValidationError(f"safe extraction failed: {exc}") from exc
        total_written += actual
        if entry.normalized_path == "pack.json" and actual > VPACK_MAX_PACK_JSON_BYTES:
            raise PackValidationError("actual pack.json exceeds 256 KiB")
        if entry.normalized_path == "items.json" and actual > VPACK_MAX_ITEMS_JSON_BYTES:
            raise PackValidationError("actual items.json exceeds 16 MiB")
