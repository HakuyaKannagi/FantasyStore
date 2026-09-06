from __future__ import annotations

import logging
import shutil
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Collection, Mapping

from fantasy_store.config import VPACK_MAX_PACK_JSON_BYTES
from fantasy_store.domain.errors import PackValidationError
from fantasy_store.domain.ids import new_uuid_v4, validate_pack_id, validate_uuid_v4
from fantasy_store.domain.pack_version import PackImportClassification, PackVersion
from fantasy_store.runtime.paths import AppPaths
from .digest import compute_content_digest
from .image_reference_validator import ImageReferenceValidator
from .image_validator import ImageValidator
from .json_loader import load_strict_json, loads_strict_json_bytes
from .path_resolver import PackPathResolver, normalize_image_reference
from .schema_validator import LocalSchemaValidator
from .validator import PackSemanticValidator, PreparedItemRecord
from .zip_safety import prevalidate_archive, safe_extract


class ImportKind(StrEnum):
    NEW = "NEW"
    EXISTING_PACK = "EXISTING_PACK"


@dataclass(frozen=True, slots=True)
class PreparedPack:
    operation_id: str
    source_path: Path
    staging_path: Path
    pack_metadata: Mapping[str, Any]
    validated_items: Mapping[str, Any]
    prepared_items_master: tuple[PreparedItemRecord, ...]
    content_digest: str
    import_kind: ImportKind
    version_classification: PackImportClassification | None = None
    installed_version_before: str | None = None


PhaseCallback = Callable[[str], None]


class PackImporter:
    def __init__(self, paths: AppPaths, *, logger: logging.Logger | None = None) -> None:
        self.paths = paths
        self.path_resolver = PackPathResolver(paths)
        self.schema = LocalSchemaValidator()
        self.semantic = PackSemanticValidator()
        self.images = ImageValidator()
        self.logger = logger or logging.getLogger(__name__)


    def inspect_metadata(self, source_path: Path) -> dict[str, Any]:
        """Read and validate only pack.json without mutating persistent state.

        This preflight exists for the Human-approved version policy. It performs
        the same ZIP metadata safety checks and local pack.json schema validation
        before any version comparison, but it creates no journal/staging tree.
        Full Phase-3 validation still runs before any installation switch.
        """
        source = Path(source_path).resolve()
        archive = None
        try:
            archive, entries = prevalidate_archive(source)
            entry = next((e for e in entries if not e.is_dir and e.normalized_path == "pack.json"), None)
            if entry is None:
                raise PackValidationError("pack.json is missing")
            with archive.open(entry.info, "r") as fp:
                raw = fp.read(VPACK_MAX_PACK_JSON_BYTES + 1)
            if len(raw) > VPACK_MAX_PACK_JSON_BYTES:
                raise PackValidationError("actual pack.json exceeds 256 KiB")
            pack = loads_strict_json_bytes(raw)
            self.schema.validate_pack(pack)
            validate_pack_id(pack["pack_id"])
            PackVersion.parse(pack["version"])
            return dict(pack)
        finally:
            if archive is not None:
                archive.close()

    def prepare(
        self,
        source_path: Path,
        *,
        existing_pack_ids: Collection[str] = (),
        operation_id: str | None = None,
        phase_callback: PhaseCallback | None = None,
    ) -> PreparedPack:
        """Safely stage and validate one external .vpack.

        Phase 4 may supply the operation id so the Phase 3 staging directory and
        operation journal use the same identifier. The validation behavior is
        otherwise unchanged from Phase 3.
        """
        source = Path(source_path).resolve()
        op_id = operation_id or new_uuid_v4()
        validate_uuid_v4(op_id)
        staging = self.path_resolver.staging_root(op_id)
        archive = None
        created_staging = False
        try:
            archive, entries = prevalidate_archive(source)
            safe_extract(archive, entries, staging)
            created_staging = True
            archive.close()
            archive = None
            if phase_callback is not None:
                phase_callback("VALIDATING")
            members = {e.normalized_path.rstrip("/") for e in entries if not e.is_dir}
            return self._validate_extracted(
                staging,
                operation_id=op_id,
                source_path=source,
                normalized_members=members,
                existing_pack_ids=existing_pack_ids,
            )
        except Exception:
            if archive is not None:
                archive.close()
            if staging.exists() or created_staging:
                try:
                    shutil.rmtree(staging)
                except OSError as cleanup_exc:
                    self.logger.error(
                        "failed to clean Phase 3 staging after validation failure: %s",
                        cleanup_exc,
                        extra={"event_code": "PACK_STAGING_CLEANUP_FAILED"},
                    )
            raise

    def validate_directory(
        self,
        pack_root: Path,
        *,
        operation_id: str | None = None,
        source_path: Path | None = None,
        existing_pack_ids: Collection[str] = (),
        expected_pack_id: str | None = None,
    ) -> PreparedPack:
        """Revalidate an already extracted pack directory using Phase 3 rules.

        This is used by Phase 4 manifest recovery. It does not move or mutate the
        directory and therefore is safe to use for installed/backup observation.
        """
        root = Path(pack_root).resolve()
        if not root.is_dir() or root.is_symlink():
            raise PackValidationError("pack directory is missing or not a regular directory")
        members = self._scan_directory_members(root)
        op_id = operation_id or new_uuid_v4()
        validate_uuid_v4(op_id)
        prepared = self._validate_extracted(
            root,
            operation_id=op_id,
            source_path=Path(source_path).resolve() if source_path is not None else root,
            normalized_members=members,
            existing_pack_ids=existing_pack_ids,
        )
        if expected_pack_id is not None:
            validate_pack_id(expected_pack_id)
            if prepared.pack_metadata["pack_id"] != expected_pack_id:
                raise PackValidationError("installed directory name and pack.json pack_id disagree")
        return prepared

    def _validate_extracted(
        self,
        root: Path,
        *,
        operation_id: str,
        source_path: Path,
        normalized_members: Collection[str],
        existing_pack_ids: Collection[str],
    ) -> PreparedPack:
        pack = load_strict_json(root / "pack.json")
        items = load_strict_json(root / "items.json")
        self.schema.validate_pack(pack)
        self.schema.validate_items(items)
        PackVersion.parse(pack["version"])

        validated_image_paths: set[str] = set()
        for member in sorted(p for p in normalized_members if p.startswith("assets/")):
            self.images.validate(root / Path(*member.split("/")))
            validated_image_paths.add(member)
        refs = ImageReferenceValidator(root, normalized_members, validated_image_paths)
        validated_items, prepared_records = self.semantic.validate_and_prepare(pack, items, refs)
        digest = compute_content_digest(root)
        kind = ImportKind.EXISTING_PACK if pack["pack_id"] in set(existing_pack_ids) else ImportKind.NEW
        return PreparedPack(
            operation_id=operation_id,
            source_path=source_path,
            staging_path=root,
            pack_metadata=dict(pack),
            validated_items=validated_items,
            prepared_items_master=prepared_records,
            content_digest=digest,
            import_kind=kind,
        )

    @staticmethod
    def _scan_directory_members(root: Path) -> set[str]:
        root_entries = {p.name: p for p in root.iterdir()}
        if set(root_entries) != {"pack.json", "items.json", "assets"}:
            raise PackValidationError("installed pack root must contain only pack.json, items.json, and assets/")
        if not root_entries["pack.json"].is_file() or root_entries["pack.json"].is_symlink():
            raise PackValidationError("pack.json is not a regular file")
        if not root_entries["items.json"].is_file() or root_entries["items.json"].is_symlink():
            raise PackValidationError("items.json is not a regular file")
        assets = root_entries["assets"]
        if not assets.is_dir() or assets.is_symlink():
            raise PackValidationError("assets is not a regular directory")

        members: set[str] = {"pack.json", "items.json"}
        nfc_seen: set[str] = set(members)
        case_seen: set[str] = {m.casefold() for m in members}
        asset_count = 0
        for path in assets.rglob("*"):
            if path.is_symlink():
                raise PackValidationError("symlink in installed pack is forbidden")
            if path.is_dir():
                continue
            if not path.is_file():
                raise PackValidationError("special file in installed pack is forbidden")
            rel = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
            # Reuse the independent runtime image-reference boundary so reserved
            # names, extensions, dot segments, and unsafe Windows names are
            # checked again even for previously validated installed content.
            rel = normalize_image_reference(rel)
            if rel in nfc_seen or rel.casefold() in case_seen:
                raise PackValidationError("installed pack path collision")
            nfc_seen.add(rel)
            case_seen.add(rel.casefold())
            members.add(rel)
            asset_count += 1
        if asset_count == 0:
            raise PackValidationError("assets/ must contain at least one image file")
        return members
