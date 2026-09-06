from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fantasy_store.config import SCHEMA_VERSION
from fantasy_store.domain.errors import (
    DatabaseBackupError,
    DatabaseRecoveryError,
    DatabaseVersionUnsupportedError,
)
from .connection import connect, connect_readonly_immutable
from .migration import USER_DB_NAME, validate_database_file


@dataclass(frozen=True, slots=True)
class BackupValidation:
    path: Path
    schema_version: int


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    recovered: bool
    active_path: Path
    backup_path: Path | None = None
    recovery_hold_path: Path | None = None


class UserDataBackupManager:
    def __init__(
        self,
        db_path: Path,
        backup_dir: Path,
        recovery_hold_dir: Path,
        *,
        max_generations: int = 3,
        max_supported_version: int = SCHEMA_VERSION,
        now: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], uuid.UUID] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.recovery_hold_dir = Path(recovery_hold_dir)
        self.max_generations = max_generations
        self.max_supported_version = max_supported_version
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or uuid.uuid4

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    def _new_backup_paths(self) -> tuple[Path, Path]:
        token = str(self._uuid_factory())
        stem = f"user_data.{self._timestamp()}.{token}"
        return self.backup_dir / f"{stem}.tmp.db", self.backup_dir / f"{stem}.db"

    @staticmethod
    def _protection_marker(path: Path) -> Path:
        return path.with_name(path.name + ".migration-protected")

    def validate(self, path: Path, *, max_supported_version: int | None = None) -> BackupValidation:
        version = validate_database_file(
            Path(path),
            USER_DB_NAME,
            max_supported_version=self.max_supported_version if max_supported_version is None else max_supported_version,
            integrity_check=True,
            immutable=True,
        )
        return BackupValidation(Path(path), version)

    def validate_current(self) -> BackupValidation:
        version = validate_database_file(
            self.db_path,
            USER_DB_NAME,
            max_supported_version=self.max_supported_version,
            integrity_check=True,
            immutable=False,
        )
        return BackupValidation(self.db_path, version)

    def create_backup(self, *, protect_for_migration: bool = False) -> Path:
        if not self.db_path.exists():
            raise DatabaseBackupError("user_data.db does not exist")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        tmp_path, final_path = self._new_backup_paths()

        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        try:
            source = connect(self.db_path)
            destination = sqlite3.connect(tmp_path)
            source.backup(destination)
            destination.commit()
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DatabaseBackupError(f"SQLite Backup API failed: {exc}") from exc
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()

        try:
            self.validate(tmp_path)
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                raise DatabaseBackupError(f"backup validation failed: {exc}") from exc

        try:
            os.replace(tmp_path, final_path)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DatabaseBackupError(f"backup finalization failed: {exc}") from exc

        if protect_for_migration:
            try:
                marker = self._protection_marker(final_path)
                marker.write_text("migration-protected\n", encoding="utf-8")
            except OSError as exc:
                # If protection cannot be persisted, this backup cannot safely
                # authorize starting migration.
                try:
                    final_path.unlink(missing_ok=True)
                finally:
                    raise DatabaseBackupError(f"migration backup protection failed: {exc}") from exc

        # Retention is best-effort after the new generation is already valid.
        self.cleanup_old_backups()
        return final_path

    def backup_before_migration(self) -> Path:
        return self.create_backup(protect_for_migration=True)

    def backup_after_checkout(self) -> Path:
        return self.create_backup()

    def backup_after_migration(self) -> Path:
        return self.create_backup()

    def backup_on_shutdown(self, *, dirty_since_last_backup: bool) -> Path | None:
        return self.create_backup() if dirty_since_last_backup else None

    def ensure_initial_backup(self) -> Path | None:
        if self.valid_backups():
            return None
        self.validate_current()
        return self.create_backup()

    def release_migration_protection(self, backup_path: Path) -> None:
        marker = self._protection_marker(Path(backup_path))
        try:
            marker.unlink(missing_ok=True)
        except OSError as exc:
            raise DatabaseBackupError(f"cannot release migration backup protection: {exc}") from exc
        self.cleanup_old_backups()

    def list_backup_files(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        # Never treat staging/temp artifacts as recovery generations. The glob
        # is intentionally filtered because '*' can also span '.tmp'.
        candidates = [
            path
            for path in self.backup_dir.glob("user_data.*.*.db")
            if not path.name.endswith(".tmp.db")
        ]
        # UTC timestamp is embedded in a fixed-width lexicographically sortable
        # position in names created by this manager.
        return sorted(candidates, key=lambda p: p.name, reverse=True)

    def valid_backups(self) -> list[Path]:
        valid: list[Path] = []
        for path in self.list_backup_files():
            try:
                self.validate(path)
            except Exception:
                continue
            valid.append(path)
        return valid

    def cleanup_old_backups(self) -> None:
        valid_unprotected: list[Path] = []
        for path in self.list_backup_files():
            if self._protection_marker(path).exists():
                continue
            try:
                self.validate(path)
            except Exception:
                continue
            valid_unprotected.append(path)

        for old in valid_unprotected[self.max_generations :]:
            try:
                old.unlink()
            except OSError:
                # Frozen design: cleanup failure must not invalidate the newly
                # created, already validated backup generation.
                continue

    def select_latest_valid_backup(self) -> Path | None:
        for candidate in self.list_backup_files():
            try:
                self.validate(candidate)
                return candidate
            except Exception:
                continue
        return None

    def has_recovery_evidence(self) -> bool:
        """Return true when a missing current DB must not be treated as first-run.

        A prior failed recovery may have moved the only current DB into
        recovery_hold. Existing backup artifacts likewise prove this data area
        has already contained user data.
        """
        if self.backup_dir.exists() and any(path.is_file() for path in self.backup_dir.glob("user_data.*")):
            return True
        if self.recovery_hold_dir.exists():
            for path in self.recovery_hold_dir.glob("*/user_data.db*"):
                if path.is_file():
                    return True
        return False

    def recover_missing_current(self) -> RecoveryResult:
        if self.db_path.exists():
            raise DatabaseRecoveryError("recover_missing_current requires a missing current DB")
        candidate = self.select_latest_valid_backup()
        if candidate is None:
            raise DatabaseRecoveryError(
                "current user_data.db is missing in an existing data area and no valid backup candidate exists"
            )
        return self.restore_specific_backup(candidate, hold_current=False)

    def hold_current_database(self) -> Path:
        hold = self.recovery_hold_dir / self._timestamp()
        try:
            hold.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise DatabaseRecoveryError(f"cannot create recovery_hold: {exc}") from exc

        for source in (
            self.db_path,
            Path(str(self.db_path) + "-wal"),
            Path(str(self.db_path) + "-shm"),
        ):
            if not source.exists():
                continue
            try:
                os.replace(source, hold / source.name)
            except OSError as exc:
                raise DatabaseRecoveryError(f"cannot preserve current database file {source.name}: {exc}") from exc
        return hold

    def restore_specific_backup(self, backup_path: Path, *, hold_current: bool) -> RecoveryResult:
        backup_path = Path(backup_path)
        self.validate(backup_path)
        hold_path: Path | None = None
        if hold_current and self.db_path.exists():
            hold_path = self.hold_current_database()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.db_path.with_name(f"user_data.restore.{self._uuid_factory()}.tmp.db")
        source: sqlite3.Connection | None = None
        destination: sqlite3.Connection | None = None
        try:
            source = connect_readonly_immutable(backup_path)
            destination = sqlite3.connect(tmp_path)
            source.backup(destination)
            destination.commit()
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DatabaseRecoveryError(f"restore temporary database creation failed: {exc}") from exc
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()

        try:
            self.validate(tmp_path)
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            finally:
                raise DatabaseRecoveryError(f"restore temporary database validation failed: {exc}") from exc

        try:
            os.replace(tmp_path, self.db_path)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise DatabaseRecoveryError(f"restore switch failed: {exc}") from exc

        try:
            self.validate_current()
        except Exception as exc:
            raise DatabaseRecoveryError(f"restored database failed final validation: {exc}") from exc

        return RecoveryResult(True, self.db_path, backup_path, hold_path)

    def recover_if_current_invalid(self) -> RecoveryResult:
        if not self.db_path.exists():
            raise DatabaseRecoveryError("current user_data.db is missing; initialization decision belongs to bootstrap")

        try:
            self.validate_current()
            return RecoveryResult(False, self.db_path)
        except DatabaseVersionUnsupportedError:
            # A newer DB is not corruption and must never be silently rolled
            # back to an older backup.
            raise
        except Exception:
            pass

        hold = self.hold_current_database()
        candidate = self.select_latest_valid_backup()
        if candidate is None:
            raise DatabaseRecoveryError("no valid user_data.db backup candidate exists")
        result = self.restore_specific_backup(candidate, hold_current=False)
        return RecoveryResult(True, result.active_path, candidate, hold)
