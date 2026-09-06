from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fantasy_store.config import SCHEMA_VERSION
from fantasy_store.domain.errors import (
    DatabaseVersionUnsupportedError,
    PackOperationError,
    PackRecoveryRequiredError,
    PackSafetyDiagnostic,
    PackStartupSafetyError,
    PackValidationError,
)
from fantasy_store.domain.pack_version import PackVersion
from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.file_ops import remove_tree_best_effort, replace_with_retry
from fantasy_store.pack.importer import PackImporter
from fantasy_store.pack.journal import PackJournalStore, PackOperationJournal, PackOperationState, utc_text
from fantasy_store.pack.manifest_state import PackManifestStateStore
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.persistence.migration import PACK_DB_NAME, initialize_database, validate_database_file
from fantasy_store.persistence.pack_repository import InstalledPack, PackRepository
from fantasy_store.runtime.paths import AppPaths


_UNKNOWN = object()


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    operation_id: str
    pack_id: str | None
    decision: str
    final_state: str


class PackRecoveryManager:
    def __init__(
        self,
        paths: AppPaths,
        *,
        repository: PackRepository | None = None,
        importer: PackImporter | None = None,
        journal_store: PackJournalStore | None = None,
        manifest_state: PackManifestStateStore | None = None,
        logger: logging.Logger | None = None,
        retry_attempts: int = 3,
        retry_delay: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.paths = paths
        self.repository = repository or PackRepository(paths.pack_db)
        self.importer = importer or PackImporter(paths)
        self.journals = journal_store or PackJournalStore(paths)
        self.manifest_state = manifest_state or PackManifestStateStore(paths.pack_manifest_state_backup)
        self.resolver = PackPathResolver(paths)
        self.logger = logger or logging.getLogger(__name__)
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.sleep = sleep

    # ------------------------------------------------------------------
    # pack_manifest.db health / rebuild
    def ensure_manifest_database(self) -> bool:
        """Return True when pack_manifest.db had to be rebuilt."""
        if not self.paths.pack_db.exists():
            evidence = (
                any(self.paths.installed_packs.iterdir())
                or self.paths.pack_manifest_state_backup.exists()
                or any(self.paths.pack_operations.glob("*.json"))
                or any(self.paths.pack_recovery_hold.iterdir())
            )
            if not evidence:
                initialize_database(self.paths.pack_db, PACK_DB_NAME)
                return False
            self.rebuild_manifest_database(hold_current=False)
            return True

        try:
            validate_database_file(
                self.paths.pack_db,
                PACK_DB_NAME,
                max_supported_version=SCHEMA_VERSION,
                integrity_check=True,
            )
            return False
        except DatabaseVersionUnsupportedError:
            # A future schema is not corruption and must never be silently
            # replaced by a reconstructed v1 database.
            raise
        except Exception as exc:
            self.logger.error(
                "pack_manifest.db health check failed; rebuilding: %s",
                exc,
                extra={"event_code": "PACK_DB_REBUILD_START"},
            )
            self.rebuild_manifest_database(hold_current=True)
            return True

    def rebuild_manifest_database(self, *, hold_current: bool) -> None:
        if hold_current and self.paths.pack_db.exists():
            hold = self.paths.pack_recovery_hold / f"{utc_text(datetime.now(timezone.utc)).replace(':','').replace('-','')}--{new_uuid_v4()}"
            hold.mkdir(parents=True, exist_ok=False)
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(self.paths.pack_db) + suffix)
                if src.exists():
                    try:
                        os.replace(src, hold / src.name)
                    except OSError as exc:
                        raise PackRecoveryRequiredError(None, f"cannot preserve corrupt pack manifest DB: {exc}") from exc

        if self.paths.pack_db.exists():
            # hold_current=False is only used when a newly initialized empty DB
            # was created for a missing prior DB. Rebuild it transactionally.
            try:
                self.paths.pack_db.unlink()
            except OSError as exc:
                raise PackRecoveryRequiredError(None, f"cannot reset manifest DB for rebuild: {exc}") from exc
        initialize_database(self.paths.pack_db, PACK_DB_NAME)
        self.repository = PackRepository(self.paths.pack_db)

        try:
            state = self.manifest_state.load()
        except Exception as exc:
            self.logger.warning(
                "manifest state backup unavailable/invalid; all recovered packs will be disabled: %s",
                exc,
                extra={"event_code": "PACK_MANIFEST_STATE_INVALID"},
            )
            state = {}

        rebuilt: list[tuple[InstalledPack, tuple]] = []
        now = utc_text(datetime.now(timezone.utc))
        if self.paths.installed_packs.exists():
            for child in sorted(self.paths.installed_packs.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or child.is_symlink():
                    self.logger.warning(
                        "invalid entry under installed packs left unregistered: %s",
                        child.name,
                        extra={"event_code": "PACK_REBUILD_INVALID_INSTALLED"},
                    )
                    continue
                try:
                    prepared = self.importer.validate_directory(child, expected_pack_id=child.name)
                except Exception as exc:
                    self.logger.error(
                        "installed pack failed revalidation and was not re-registered: %s: %s",
                        child.name,
                        exc,
                        extra={"event_code": "PACK_REBUILD_INVALID_INSTALLED", "pack_id": child.name},
                    )
                    continue
                pack_id = prepared.pack_metadata["pack_id"]
                backup = state.get(pack_id)
                same_generation = backup is not None and backup.content_digest == prepared.content_digest
                installed_at = backup.installed_at if same_generation else now
                updated_at = backup.updated_at if same_generation else now
                enabled = backup.is_enabled if same_generation else False
                md = prepared.pack_metadata
                rebuilt.append((
                    InstalledPack(
                        pack_id=pack_id,
                        pack_name=md["name"],
                        pack_version=md["version"],
                        author=md["author"],
                        description=md["description"],
                        schema_version=int(md["schema_version"]),
                        is_enabled=enabled,
                        content_digest=prepared.content_digest,
                        install_dir=self.resolver.relative_to_persistent(child),
                        installed_at=installed_at,
                        updated_at=updated_at,
                    ),
                    prepared.prepared_items_master,
                ))
        self.repository.rebuild_all(rebuilt)
        validate_database_file(
            self.paths.pack_db,
            PACK_DB_NAME,
            max_supported_version=SCHEMA_VERSION,
            integrity_check=True,
        )
        try:
            self.manifest_state.write_repository_state(self.repository)
        except Exception as exc:
            self.logger.warning(
                "manifest state backup could not be refreshed after DB rebuild: %s",
                exc,
                extra={"event_code": "BACKUP_WRITE_FAILED"},
            )
        self.logger.info("pack_manifest.db rebuild completed", extra={"event_code": "PACK_DB_REBUILD_COMPLETE"})

    # ------------------------------------------------------------------
    # operation recovery
    def recover_all(self) -> list[RecoveryDecision]:
        """Recover only the operation frontier for each Pack generation chain.

        Completed journals are durable history, not independent claims that their
        generation must still be current forever.  If a later operation for the
        same Pack consumes an earlier ``new_digest`` as its ``old_digest``, the
        earlier operation is historical/superseded and must not be compared with
        the current DB/filesystem generation.  The later frontier operation still
        undergoes the full observation-based recovery logic, so genuine external
        corruption remains fail-closed.

        The relevance decision is derived at recovery time; no new persistent
        journal state is introduced.  This also tolerates historical journals that
        an older buggy build already rewrote to RECOVERY_REQUIRED, provided a later
        COMPLETED successor proves the generation chain continued normally.
        """
        loaded = [self.journals.load(path) for path in self.journals.list_final_journals()]
        decisions: list[RecoveryDecision] = []

        # Pre-validation staging journals have no Pack identity and therefore no
        # generation chain. Recover them independently as before.
        by_pack: dict[str, list[PackOperationJournal]] = {}
        for journal in loaded:
            if journal.pack_id is None:
                decisions.append(self.recover_operation(journal))
            else:
                by_pack.setdefault(journal.pack_id, []).append(journal)

        for pack_id in sorted(by_pack):
            journals = sorted(by_pack[pack_id], key=self._journal_generation_order)
            historical = self._superseded_operation_ids(journals)
            for journal in journals:
                if journal.operation_id in historical:
                    self.logger.info(
                        "historical pack operation superseded by a later generation",
                        extra={
                            "event_code": "PACK_RECOVERY_HISTORICAL_SUPERSEDED",
                            "operation_id": journal.operation_id,
                            "pack_id": journal.pack_id,
                        },
                    )
                    decisions.append(
                        RecoveryDecision(
                            journal.operation_id,
                            journal.pack_id,
                            "historical_superseded",
                            PackOperationState.COMPLETED.value,
                        )
                    )
                    continue
                decisions.append(self.recover_operation(journal))

        self.cleanup_orphans()
        return decisions

    @staticmethod
    def _journal_generation_order(journal: PackOperationJournal) -> tuple[str, str]:
        # started_at is immutable across state transitions; updated_at is not (and
        # an older buggy recovery may have rewritten it long after a successor).
        return journal.started_at, journal.operation_id

    @classmethod
    def _superseded_operation_ids(cls, journals: list[PackOperationJournal]) -> set[str]:
        """Return operations whose generation is consumed by a later success.

        Only a later COMPLETED operation is authoritative evidence that the former
        generation became historical.  This keeps interrupted/latest operations on
        the recovery frontier while preventing old successful generations from
        being reclassified merely because the Pack has since advanced.
        """
        ordered = sorted(journals, key=cls._journal_generation_order)
        superseded: set[str] = set()
        for index, journal in enumerate(ordered):
            for successor in ordered[index + 1 :]:
                if successor.state != PackOperationState.COMPLETED.value:
                    continue
                # A normal update or uninstall consumes the current generation.
                if journal.new_digest is not None and successor.old_digest == journal.new_digest:
                    superseded.add(journal.operation_id)
                    break
                # A completed uninstall represents the absence of an installed
                # generation. A later completed IMPORT starts a new lineage for
                # the same Pack ID, so the old uninstall is historical too.
                if (
                    journal.operation_type == "UNINSTALL"
                    and successor.operation_type == "IMPORT"
                    and successor.old_digest is None
                    and successor.new_digest is not None
                ):
                    superseded.add(journal.operation_id)
                    break
        return superseded

    def recover_operation(self, journal: PackOperationJournal) -> RecoveryDecision:
        self.logger.info(
            "pack recovery observe",
            extra={"event_code": "PACK_RECOVERY_OBSERVE", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
        )
        if journal.pack_id is None:
            if journal.state not in {PackOperationState.STAGING.value, PackOperationState.VALIDATING.value}:
                return self._require_recovery(journal, "journal has no pack_id after validation boundary")
            expected_staging = self.resolver.staging_root(journal.operation_id)
            if journal.staging_path != self.resolver.relative_to_persistent(expected_staging):
                return self._require_recovery(journal, "journal staging path is not the operation-owned staging path")
            self._cleanup_tree(expected_staging, "PACK_STAGING_CLEANUP_FAILED")
            self.journals.remove(journal.operation_id)
            return RecoveryDecision(journal.operation_id, None, "discard_partial_staging", "ROLLED_BACK")

        try:
            installed, staging, backup = self._validated_journal_paths(journal)
        except Exception as exc:
            return self._require_recovery(journal, f"journal path validation failed: {exc}")

        db = self._observe_db_digest(journal.pack_id)
        installed_digest = self._observe_digest(installed)
        backup_digest = self._observe_digest(backup)
        staging_digest = self._observe_digest(staging)
        old = journal.old_digest
        new = journal.new_digest
        if journal.operation_type == "UNINSTALL":
            return self._recover_uninstall(
                journal,
                installed,
                staging,
                backup,
                db,
                installed_digest,
                staging_digest,
                backup_digest,
                old,
            )
        if new is None:
            return self._require_recovery(journal, "journal has no new_digest")

        # COMPLETED is still observed because cleanup may have been interrupted.
        if db == new and installed_digest == new:
            if backup_digest not in (None, old) and backup.exists():
                return self._require_recovery(journal, "completed/new state has unknown backup generation")
            if staging_digest not in (None, new) and staging.exists():
                return self._require_recovery(journal, "completed/new state has unknown staging generation")
            journal = self._complete_success(journal, staging, backup)
            return RecoveryDecision(journal.operation_id, journal.pack_id, "complete_new_generation", PackOperationState.COMPLETED.value)

        if journal.operation_type == "IMPORT" and old is None:
            return self._recover_new_import(journal, installed, staging, backup, db, installed_digest, staging_digest, new)

        if old is None:
            return self._require_recovery(journal, "update journal has no old_digest")
        return self._recover_update(
            journal,
            installed,
            staging,
            backup,
            db,
            installed_digest,
            staging_digest,
            backup_digest,
            old,
            new,
        )

    def _recover_uninstall(
        self,
        journal,
        installed,
        staging,
        backup,
        db,
        installed_digest,
        staging_digest,
        backup_digest,
        old,
    ):
        if old is None:
            return self._require_recovery(journal, "uninstall journal has no old_digest")
        if db is _UNKNOWN or installed_digest is _UNKNOWN or backup_digest is _UNKNOWN or staging_digest is _UNKNOWN:
            return self._require_recovery(journal, "uninstall DB/filesystem observation is unavailable")
        if staging_digest is not None:
            # Uninstall never owns a staged Pack generation. Unexpected content at
            # that path is therefore not safe to infer or delete.
            return self._require_recovery(journal, "uninstall has unexpected staging content")

        if db == old:
            # Pre-COMMIT outcome must converge back to the installed old Pack.
            if installed_digest == old and backup_digest in (None, old):
                if backup_digest == old:
                    self._cleanup_tree(backup, "PACK_BACKUP_CLEANUP_FAILED")
                    self._cleanup_empty_parent(backup.parent)
                self.journals.remove(journal.operation_id)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "uninstall_rolled_back_old_present", "ROLLED_BACK")
            if installed_digest is None and backup_digest == old:
                journal = self.journals.transition(journal, PackOperationState.ROLLING_BACK)
                try:
                    self._move(backup, installed, journal.pack_id)
                    if self._observe_digest(installed) != old:
                        return self._require_recovery(journal, "uninstall rollback restored unknown generation")
                    self._cleanup_empty_parent(backup.parent)
                    self.journals.remove(journal.operation_id)
                    return RecoveryDecision(journal.operation_id, journal.pack_id, "uninstall_restore_old", "ROLLED_BACK")
                except Exception as exc:
                    return self._require_recovery(journal, f"uninstall rollback failed: {exc}")
            return self._require_recovery(journal, "uninstall pre-COMMIT state cannot safely restore old generation")

        if db is None:
            # DB absence means the uninstall transaction committed. Complete the
            # filesystem side only when every remaining generation is the known old
            # digest from this journal.
            if installed_digest is None and backup_digest in (None, old):
                journal = self._complete_uninstall_success(journal, staging, backup)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "complete_uninstall", PackOperationState.COMPLETED.value)
            if installed_digest == old and backup_digest is None:
                if not self._cleanup_tree(installed, "PACK_UNINSTALL_CLEANUP_FAILED"):
                    return self._require_recovery(journal, "committed uninstall could not remove known old generation")
                journal = self._complete_uninstall_success(journal, staging, backup)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "complete_uninstall_known_old", PackOperationState.COMPLETED.value)
            return self._require_recovery(journal, "uninstall committed but filesystem is not a known removable old generation")

        return self._require_recovery(journal, "uninstall DB digest is neither old nor absent")

    def _complete_uninstall_success(self, journal, staging, backup) -> PackOperationJournal:
        if journal.state not in {PackOperationState.DB_SWITCHED.value, PackOperationState.COMPLETED.value}:
            journal = self.journals.transition(journal, PackOperationState.DB_SWITCHED)
        if journal.state != PackOperationState.COMPLETED.value:
            journal = self.journals.transition(journal, PackOperationState.COMPLETED)
        self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
        self._cleanup_tree(backup, "PACK_BACKUP_CLEANUP_FAILED")
        self._cleanup_empty_parent(backup.parent)
        try:
            self.manifest_state.write_repository_state(self.repository)
        except Exception as exc:
            self.logger.warning(
                "manifest state backup failed after uninstall recovery: %s",
                exc,
                extra={"event_code": "BACKUP_WRITE_FAILED", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
            )
        self.logger.info(
            "pack uninstall recovery completed",
            extra={"event_code": "PACK_UNINSTALL_RECOVERY_COMPLETE", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
        )
        return journal

    def _recover_new_import(self, journal, installed, staging, backup, db, installed_digest, staging_digest, new):
        # DB row absent means the import was not committed. A known-new file can
        # be removed because the pre-operation state had no installed Pack.
        if db is None:
            if installed_digest is None:
                self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
                self.journals.remove(journal.operation_id)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "rollback_uncommitted_new", "ROLLED_BACK")
            if installed_digest == new:
                if not self._cleanup_tree(installed, "PACK_ROLLBACK_CLEANUP_FAILED"):
                    return self._require_recovery(journal, "known new generation could not be removed")
                self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
                self.journals.remove(journal.operation_id)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "rollback_uncommitted_new", "ROLLED_BACK")
            return self._require_recovery(journal, "uncommitted new import has unknown installed digest")
        if db is _UNKNOWN or installed_digest is _UNKNOWN or staging_digest is _UNKNOWN:
            return self._require_recovery(journal, "new import digest/DB observation is unavailable")
        return self._require_recovery(journal, "new import DB/installed state is not a known generation")

    def _recover_update(
        self,
        journal,
        installed,
        staging,
        backup,
        db,
        installed_digest,
        staging_digest,
        backup_digest,
        old,
        new,
    ):
        if db is _UNKNOWN or installed_digest is _UNKNOWN or backup_digest is _UNKNOWN:
            return self._require_recovery(journal, "update digest/DB observation is unavailable")
        if db not in {old, new}:
            return self._require_recovery(journal, "DB digest matches neither old nor new generation")

        if db == old:
            if installed_digest == old:
                # Rollback already complete (possibly recovery crashed after old
                # restore). Only operation-owned known-new/known-old leftovers
                # are cleaned.
                if staging_digest not in (None, new, _UNKNOWN):
                    return self._require_recovery(journal, "rollback-complete state has unknown staging digest")
                if backup_digest not in (None, old):
                    return self._require_recovery(journal, "rollback-complete state has unknown backup digest")
                if staging_digest is _UNKNOWN:
                    # In STAGING/VALIDATING partial staging is expected and can
                    # be discarded by operation ownership; after switching an
                    # unreadable staging generation is not safe to guess.
                    if journal.state not in {PackOperationState.STAGING.value, PackOperationState.VALIDATING.value}:
                        return self._require_recovery(journal, "staging digest is unobservable after switch boundary")
                self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
                self._cleanup_tree(backup, "PACK_BACKUP_CLEANUP_FAILED")
                self._cleanup_empty_parent(backup.parent)
                self.journals.remove(journal.operation_id)
                return RecoveryDecision(journal.operation_id, journal.pack_id, "old_generation_already_restored", "ROLLED_BACK")

            if installed_digest is None and backup_digest == old:
                return self._restore_old(journal, installed, staging, backup, staging_digest, new)

            if installed_digest == new and backup_digest == old:
                return self._restore_old(journal, installed, staging, backup, staging_digest, new, move_new=True)

            return self._require_recovery(journal, "DB=old cannot safely reconstruct the old installed generation")

        # db == new: success is only automatic when installed=new (handled by
        # the top-level success check). Do not infer missing/old files are new.
        return self._require_recovery(journal, "DB=new but installed is not the known new generation")

    def _restore_old(self, journal, installed, staging, backup, staging_digest, new, *, move_new=False):
        journal = self.journals.transition(journal, PackOperationState.ROLLING_BACK)
        try:
            if move_new:
                if staging.exists():
                    if staging_digest not in (new, None):
                        return self._require_recovery(journal, "cannot clear unknown staging before rollback")
                    self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
                self._move(installed, staging, journal.pack_id)
            self._move(backup, installed, journal.pack_id)
            # Re-observe final old generation; no name-based inference.
            if self._observe_digest(installed) != journal.old_digest:
                return self._require_recovery(journal, "old restore completed but installed digest is not old")
            self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
            self._cleanup_empty_parent(backup.parent)
            self.journals.remove(journal.operation_id)
            self.logger.info(
                "pack rollback completed during recovery",
                extra={"event_code": "PACK_ROLLBACK_COMPLETE", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
            )
            return RecoveryDecision(journal.operation_id, journal.pack_id, "restore_old_generation", "ROLLED_BACK")
        except PackRecoveryRequiredError as exc:
            return self._require_recovery(journal, str(exc))
        except BaseException as exc:
            if isinstance(exc, Exception):
                return self._require_recovery(journal, f"rollback recovery failed: {exc}")
            raise

    def _complete_success(self, journal, staging, backup) -> PackOperationJournal:
        if journal.state not in {PackOperationState.DB_SWITCHED.value, PackOperationState.COMPLETED.value}:
            journal = self.journals.transition(journal, PackOperationState.DB_SWITCHED)
        if journal.state != PackOperationState.COMPLETED.value:
            journal = self.journals.transition(journal, PackOperationState.COMPLETED)
        self._cleanup_tree(staging, "PACK_STAGING_CLEANUP_FAILED")
        self._cleanup_tree(backup, "PACK_BACKUP_CLEANUP_FAILED")
        self._cleanup_empty_parent(backup.parent)
        try:
            self.manifest_state.write_repository_state(self.repository)
        except Exception as exc:
            self.logger.warning(
                "manifest state backup failed after pack recovery: %s",
                exc,
                extra={"event_code": "BACKUP_WRITE_FAILED", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
            )
        self.logger.info(
            "pack recovery completed on new generation",
            extra={"event_code": "PACK_RECOVERY_COMPLETE", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
        )
        return journal

    def _validated_journal_paths(self, journal: PackOperationJournal) -> tuple[Path, Path, Path]:
        assert journal.pack_id is not None
        installed = self.resolver.installed_pack_root(journal.pack_id)
        staging = self.resolver.staging_root(journal.operation_id)
        backup = self.resolver.backup_pack_root(journal.operation_id, journal.pack_id)
        expected = {
            "installed_path": self.resolver.relative_to_persistent(installed),
            "staging_path": self.resolver.relative_to_persistent(staging),
            "backup_path": self.resolver.relative_to_persistent(backup),
        }
        for field, value in expected.items():
            if getattr(journal, field) != value:
                raise PackOperationError(f"journal {field} does not match safe derived path", code="PACK_JOURNAL_INVALID")
            self.resolver.resolve_recorded_path(value)
        return installed, staging, backup

    def _observe_db_digest(self, pack_id: str):
        try:
            return self.repository.get_digest(pack_id)
        except Exception as exc:
            self.logger.error("DB digest observation failed: %s", exc, extra={"event_code": "PACK_RECOVERY_OBSERVE_FAILED", "pack_id": pack_id})
            return _UNKNOWN

    def _observe_digest(self, path: Path):
        if not path.exists():
            return None
        last = None
        for attempt in range(self.retry_attempts):
            try:
                return compute_content_digest(path)
            except Exception as exc:
                last = exc
                if attempt + 1 < self.retry_attempts:
                    self.sleep(self.retry_delay * (2 ** attempt))
        self.logger.error(
            "pack digest observation failed after retries: %s: %s",
            path,
            last,
            extra={"event_code": "PACK_RECOVERY_OBSERVE_FAILED"},
        )
        return _UNKNOWN

    def _move(self, src: Path, dst: Path, pack_id: str | None) -> None:
        try:
            replace_with_retry(
                src,
                dst,
                attempts=self.retry_attempts,
                base_delay=self.retry_delay,
                sleep=self.sleep,
                on_retry=lambda attempt, exc: self.logger.warning(
                    "recovery rename retry %s: %s",
                    attempt,
                    exc,
                    extra={"event_code": "PACK_RENAME_RETRY", "pack_id": pack_id},
                ),
            )
        except OSError as exc:
            raise PackRecoveryRequiredError(pack_id, f"recovery rename failed: {exc}") from exc

    def _cleanup_tree(self, path: Path, event_code: str) -> bool:
        return remove_tree_best_effort(
            path,
            on_error=lambda exc: self.logger.warning(
                "pack recovery cleanup failed: %s: %s",
                path,
                exc,
                extra={"event_code": event_code},
            ),
        )

    @staticmethod
    def _cleanup_empty_parent(path: Path) -> None:
        try:
            path.rmdir()
        except OSError:
            pass

    def _require_recovery(self, journal: PackOperationJournal, reason: str) -> RecoveryDecision:
        if journal.state != PackOperationState.RECOVERY_REQUIRED.value:
            try:
                journal = self.journals.transition(journal, PackOperationState.RECOVERY_REQUIRED)
            except Exception:
                # If even the journal cannot be updated, preserve all evidence
                # and still fail closed.
                pass
        self.logger.critical(
            "pack recovery requires human intervention: %s",
            reason,
            extra={"event_code": "PACK_RECOVERY_REQUIRED", "operation_id": journal.operation_id, "pack_id": journal.pack_id},
        )
        return RecoveryDecision(journal.operation_id, journal.pack_id, "RECOVERY_REQUIRED", PackOperationState.RECOVERY_REQUIRED.value)

    def verify_manifest_consistency(self) -> None:
        """Verify DB/files after journal recovery and rebuild derived items if needed.

        Failures in this boundary are Pack-subsystem safety failures.  Normal
        storefront startup still fails closed, while Phase 7 store-manager
        startup may surface the structured diagnostic in restricted mode.
        """
        rows = self.repository.list_installed_packs()
        db_ids = {row.pack_id for row in rows}
        for row in rows:
            # Establish Pack identity/current-generation safety *before* parsing
            # the Human-approved MAJOR.MINOR Version.  A legacy Version may be
            # eligible for restricted disable/uninstall only when DB and
            # installed filesystem already prove the same current generation.
            installed = self.resolver.installed_pack_root(row.pack_id)
            if row.install_dir != self.resolver.relative_to_persistent(installed):
                raise PackStartupSafetyError(
                    PackSafetyDiagnostic(
                        category="PACK_INSTALLED_DB_MISMATCH",
                        pack_id=row.pack_id,
                        pack_version=row.pack_version,
                        operation_id=None,
                        operation_type=None,
                        state="MANIFEST_CONSISTENCY_FAILED",
                        message="Pack管理情報と安全な導入先が一致しません。",
                    )
                )
            if not installed.is_dir():
                raise PackStartupSafetyError(
                    PackSafetyDiagnostic(
                        category="PACK_INSTALLED_DB_MISMATCH",
                        pack_id=row.pack_id,
                        pack_version=row.pack_version,
                        operation_id=None,
                        operation_type=None,
                        state="MANIFEST_CONSISTENCY_FAILED",
                        message="Pack管理情報に対応する導入済みPackを確認できません。",
                    )
                )
            digest = self._observe_digest(installed)
            if digest is _UNKNOWN or digest != row.content_digest:
                raise PackStartupSafetyError(
                    PackSafetyDiagnostic(
                        category="PACK_INSTALLED_DB_MISMATCH",
                        pack_id=row.pack_id,
                        pack_version=row.pack_version,
                        operation_id=None,
                        operation_type=None,
                        state="MANIFEST_CONSISTENCY_FAILED",
                        message="Pack管理情報と導入済みPackの内容世代が一致しません。",
                    )
                )

            # Human-approved Pack Version policy deliberately does not migrate
            # legacy values such as ``1``/``2``.  At this point the current
            # generation, Pack ID, DB row, installed path and digest are known
            # to agree.  Record that evidence for restricted maintenance;
            # startup remains fail-closed until the legacy Pack is retired.
            try:
                PackVersion.parse(row.pack_version)
            except PackValidationError as exc:
                raise PackStartupSafetyError(
                    PackSafetyDiagnostic(
                        category="PACK_VERSION_SCHEMA_INCOMPATIBLE",
                        pack_id=row.pack_id,
                        pack_version=row.pack_version,
                        operation_id=None,
                        operation_type=None,
                        state="MANIFEST_CONSISTENCY_FAILED",
                        message="導入済みPackのVersionが現行MAJOR.MINOR形式に適合しません。",
                        maintenance_level="SAFE_MAINTENANCE",
                        current_generation_safe=True,
                        db_filesystem_match=True,
                        enable_state_known=True,
                        is_enabled=row.is_enabled,
                        pack_name=row.pack_name,
                    )
                ) from exc
            try:
                prepared = self.importer.validate_directory(installed, expected_pack_id=row.pack_id)
            except Exception as exc:
                raise PackStartupSafetyError(
                    PackSafetyDiagnostic(
                        category="PACK_MANIFEST_INCONSISTENT",
                        pack_id=row.pack_id,
                        pack_version=row.pack_version,
                        operation_id=None,
                        operation_type=None,
                        state="MANIFEST_CONSISTENCY_FAILED",
                        message="導入済みPackを現行仕様で安全に再検証できません。",
                    )
                ) from exc
            db_items = self.repository.list_items_for_pack(row.pack_id)
            expected = {item.item_id: item for item in prepared.prepared_items_master}
            actual_ids = {r["item_id"] for r in db_items}
            if actual_ids != set(expected) or len(db_items) != len(expected):
                self.repository.replace_items_only(row.pack_id, prepared.prepared_items_master)
                self.logger.warning(
                    "items_master rebuilt from installed pack",
                    extra={"event_code": "PACK_ITEMS_REBUILT", "pack_id": row.pack_id},
                )
            else:
                # Detect stale derived content even when the item-id set matches.
                stale = False
                for db_row in db_items:
                    item = expected[db_row["item_id"]]
                    fields = (
                        "item_name", "price_significand", "price_exponent", "price_magnitude",
                        "price_sort_digits", "category", "description", "attributes_json",
                        "images_json", "search_text",
                    )
                    values = (
                        item.item_name, item.price_significand, item.price_exponent, item.price_magnitude,
                        item.price_sort_digits, item.category, item.description, item.attributes_json,
                        item.images_json, item.search_text,
                    )
                    if any(db_row[field] != value for field, value in zip(fields, values)):
                        stale = True
                        break
                if stale:
                    self.repository.replace_items_only(row.pack_id, prepared.prepared_items_master)
                    self.logger.warning(
                        "stale items_master rebuilt from installed pack",
                        extra={"event_code": "PACK_ITEMS_REBUILT", "pack_id": row.pack_id},
                    )

        if self.paths.installed_packs.exists():
            for child in self.paths.installed_packs.iterdir():
                if child.is_dir() and child.name not in db_ids:
                    self.logger.warning(
                        "installed directory has no manifest row and was not auto-registered: %s",
                        child.name,
                        extra={"event_code": "PACK_ORPHAN_INSTALLED"},
                    )

    # ------------------------------------------------------------------
    # orphan cleanup
    def cleanup_orphans(self) -> None:
        finals = {p.name for p in self.journals.list_final_journals()}
        for tmp in self.paths.pack_operations.glob("*.json.tmp"):
            final_name = tmp.name[:-4]
            if final_name in finals:
                try:
                    tmp.unlink()
                except OSError as exc:
                    self.logger.warning("journal temp cleanup failed: %s", exc, extra={"event_code": "PACK_ORPHAN_CLEANUP_FAILED"})
            else:
                self.logger.warning(
                    "orphan journal temp left untouched because ownership is uncertain: %s",
                    tmp.name,
                    extra={"event_code": "PACK_ORPHAN_UNCERTAIN"},
                )
        known_ops = {p.stem for p in self.journals.list_final_journals()}
        for base in (self.paths.staging_packs, self.paths.pack_backups):
            if not base.exists():
                continue
            for child in base.iterdir():
                if child.is_dir() and child.name not in known_ops:
                    self.logger.warning(
                        "orphan pack directory left untouched because ownership is uncertain: %s",
                        child,
                        extra={"event_code": "PACK_ORPHAN_UNCERTAIN"},
                    )
