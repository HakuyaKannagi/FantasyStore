from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

from fantasy_store.domain.errors import PackOperationError, PackValidationError
from fantasy_store.domain.ids import validate_uuid_v4
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.runtime.paths import AppPaths


class PackOperationState(StrEnum):
    STAGING = "STAGING"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    READY_TO_SWITCH = "READY_TO_SWITCH"
    OLD_BACKED_UP = "OLD_BACKED_UP"
    FILES_SWITCHED = "FILES_SWITCHED"
    DB_SWITCHED = "DB_SWITCHED"
    COMPLETED = "COMPLETED"
    ROLLING_BACK = "ROLLING_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class PackOperationJournal:
    journal_version: int
    operation_id: str
    operation_type: str
    pack_id: str | None
    state: str
    started_at: str
    updated_at: str
    old_digest: str | None
    new_digest: str | None
    old_version: str | None
    new_version: str | None
    installed_path: str | None
    staging_path: str
    backup_path: str | None


Clock = Callable[[], datetime]


def utc_text(dt: datetime) -> str:
    value = dt.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class PackJournalStore:
    def __init__(self, paths: AppPaths, *, now: Clock | None = None) -> None:
        self.paths = paths
        self.paths.pack_operations.mkdir(parents=True, exist_ok=True)
        self.resolver = PackPathResolver(paths)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def path_for(self, operation_id: str) -> Path:
        validate_uuid_v4(operation_id)
        path = (self.paths.pack_operations / f"{operation_id}.json").resolve()
        base = self.paths.pack_operations.resolve()
        if base not in path.parents:
            raise PackValidationError("journal path escapes pack operation directory")
        return path

    def create(self, operation_id: str, *, staging_path: Path) -> PackOperationJournal:
        validate_uuid_v4(operation_id)
        now = utc_text(self._now())
        journal = PackOperationJournal(
            journal_version=1,
            operation_id=operation_id,
            operation_type="IMPORT",
            pack_id=None,
            state=PackOperationState.STAGING.value,
            started_at=now,
            updated_at=now,
            old_digest=None,
            new_digest=None,
            old_version=None,
            new_version=None,
            installed_path=None,
            staging_path=self.resolver.relative_to_persistent(staging_path),
            backup_path=None,
        )
        self.write(journal)
        return journal

    def update(self, journal: PackOperationJournal, **changes) -> PackOperationJournal:
        changes["updated_at"] = utc_text(self._now())
        updated = replace(journal, **changes)
        self.write(updated)
        return updated

    def transition(self, journal: PackOperationJournal, state: PackOperationState, **changes) -> PackOperationJournal:
        return self.update(journal, state=state.value, **changes)

    def write(self, journal: PackOperationJournal) -> None:
        self._validate(journal)
        final = self.path_for(journal.operation_id)
        tmp = final.with_suffix(final.suffix + ".tmp")
        payload = json.dumps(asdict(journal), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, final)
        except OSError as exc:
            raise PackOperationError(f"journal atomic write failed: {exc}", code="PACK_JOURNAL_WRITE_FAILED") from exc

    def load(self, path_or_operation_id: Path | str) -> PackOperationJournal:
        path = self.path_for(path_or_operation_id) if isinstance(path_or_operation_id, str) else Path(path_or_operation_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackOperationError(f"journal cannot be read: {exc}", code="PACK_JOURNAL_INVALID") from exc
        try:
            journal = PackOperationJournal(**data)
        except TypeError as exc:
            raise PackOperationError("journal shape is invalid", code="PACK_JOURNAL_INVALID") from exc
        self._validate(journal)
        return journal

    def list_final_journals(self) -> list[Path]:
        return sorted(self.paths.pack_operations.glob("*.json"))

    def remove(self, operation_id: str) -> None:
        try:
            self.path_for(operation_id).unlink(missing_ok=True)
        except OSError as exc:
            raise PackOperationError(f"journal removal failed: {exc}", code="PACK_JOURNAL_WRITE_FAILED") from exc

    @staticmethod
    def _validate(journal: PackOperationJournal) -> None:
        if journal.journal_version != 1:
            raise PackOperationError("unsupported journal version", code="PACK_JOURNAL_INVALID")
        validate_uuid_v4(journal.operation_id)
        if journal.state not in {s.value for s in PackOperationState}:
            raise PackOperationError("unknown journal state", code="PACK_JOURNAL_INVALID")
        if journal.operation_type not in {"IMPORT", "UPDATE", "UNINSTALL"}:
            raise PackOperationError("unknown journal operation_type", code="PACK_JOURNAL_INVALID")
        if journal.pack_id is not None and not isinstance(journal.pack_id, str):
            raise PackOperationError("journal pack_id is invalid", code="PACK_JOURNAL_INVALID")
        if not isinstance(journal.staging_path, str) or not journal.staging_path:
            raise PackOperationError("journal staging_path is invalid", code="PACK_JOURNAL_INVALID")
        for field in (journal.old_digest, journal.new_digest):
            if field is not None and (len(field) != 64 or any(c not in "0123456789abcdef" for c in field)):
                raise PackOperationError("journal digest is invalid", code="PACK_JOURNAL_INVALID")
