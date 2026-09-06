from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fantasy_store.domain.errors import (
    PackBusyError,
    PackFileLockedError,
    PackOperationError,
    PackRecoveryRequiredError,
    PackValidationError,
    PackUninstallRequiresDisabledError,
)
from fantasy_store.domain.ids import new_uuid_v4
from fantasy_store.domain.pack_version import PackImportClassification, classify_pack_version
from fantasy_store.pack.access_coordinator import PackAccessCoordinator
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.file_ops import remove_tree_best_effort, replace_with_retry
from fantasy_store.pack.importer import ImportKind, PackImporter, PreparedPack
from fantasy_store.pack.journal import PackJournalStore, PackOperationJournal, PackOperationState, utc_text
from fantasy_store.pack.manifest_state import PackManifestStateStore
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.persistence.pack_repository import InstalledPack, PackRepository
from fantasy_store.runtime.paths import AppPaths


FailureHook = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class PackImportSkipped:
    classification: PackImportClassification
    pack_id: str
    pack_name: str
    incoming_version: str
    installed_version: str



class PackLifecycleManager:
    """Phase 4 pack import/update/enable orchestration.

    Phase 3 validation runs without a Pack write lock. The lock starts only at
    READY_TO_SWITCH immediately before physical generation switching.
    """

    def __init__(
        self,
        paths: AppPaths,
        *,
        repository: PackRepository | None = None,
        coordinator: PackAccessCoordinator | None = None,
        importer: PackImporter | None = None,
        journal_store: PackJournalStore | None = None,
        manifest_state: PackManifestStateStore | None = None,
        logger: logging.Logger | None = None,
        lock_timeout: float = 2.0,
        failure_hook: FailureHook | None = None,
        move_sleep=None,
    ) -> None:
        self.paths = paths
        self.repository = repository or PackRepository(paths.pack_db)
        self.coordinator = coordinator or PackAccessCoordinator()
        self.importer = importer or PackImporter(paths)
        self.journals = journal_store or PackJournalStore(paths)
        self.manifest_state = manifest_state or PackManifestStateStore(paths.pack_manifest_state_backup)
        self.resolver = PackPathResolver(paths)
        self.logger = logger or logging.getLogger(__name__)
        self.lock_timeout = lock_timeout
        self.failure_hook = failure_hook
        self._move_sleep = move_sleep

    def _checkpoint(self, name: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(name)

    def _retry_move(self, src: Path, dst: Path, *, pack_id: str) -> None:
        kwargs = {}
        if self._move_sleep is not None:
            kwargs["sleep"] = self._move_sleep
        replace_with_retry(
            src,
            dst,
            on_retry=lambda attempt, exc: self.logger.warning(
                "pack rename retry %s for %s: %s",
                attempt,
                pack_id,
                exc,
                extra={"event_code": "PACK_RENAME_RETRY", "pack_id": pack_id},
            ),
            **kwargs,
        )

    def import_vpack(self, source_path: Path) -> PreparedPack | PackImportSkipped:
        # Version policy preflight is deliberately read-only: a downgrade is
        # rejected before a journal or staging directory is created. Full Phase-3
        # validation still runs for NEW/UPGRADE/REINSTALL before switching.
        metadata = self.importer.inspect_metadata(source_path)
        pack_id = str(metadata["pack_id"])
        current = self.repository.get_installed_pack(pack_id)
        installed_version = current.pack_version if current is not None else None
        classification = classify_pack_version(str(metadata["version"]), installed_version)
        if classification == PackImportClassification.DOWNGRADE_SKIPPED:
            self.logger.info(
                "older pack version skipped by policy",
                extra={"event_code": "PACK_DOWNGRADE_SKIPPED", "pack_id": pack_id},
            )
            return PackImportSkipped(
                classification=classification,
                pack_id=pack_id,
                pack_name=str(metadata["name"]),
                incoming_version=str(metadata["version"]),
                installed_version=str(installed_version),
            )

        operation_id = new_uuid_v4()
        staging = self.resolver.staging_root(operation_id)
        journal = self.journals.create(operation_id, staging_path=staging)
        self.logger.info(
            "pack import/update start",
            extra={"event_code": "PACK_IMPORT_START", "operation_id": operation_id, "pack_id": pack_id},
        )
        self._checkpoint("STAGING")

        def phase_callback(state: str) -> None:
            nonlocal journal
            if state == PackOperationState.VALIDATING.value:
                journal = self.journals.transition(journal, PackOperationState.VALIDATING)
                self._checkpoint("VALIDATING")

        existing_ids = [p.pack_id for p in self.repository.list_installed_packs()]
        try:
            prepared = self.importer.prepare(
                source_path,
                existing_pack_ids=existing_ids,
                operation_id=operation_id,
                phase_callback=phase_callback,
            )
        except Exception:
            try:
                self.journals.remove(operation_id)
            except Exception as journal_exc:
                self.logger.error(
                    "failed to remove aborted staging journal: %s",
                    journal_exc,
                    extra={"event_code": "PACK_JOURNAL_CLEANUP_FAILED", "operation_id": operation_id},
                )
            raise

        prepared_pack_id = str(prepared.pack_metadata["pack_id"])
        if prepared_pack_id != pack_id:
            # The externally selected .vpack changed between read-only preflight
            # and full validation. Never switch using paths derived from stale
            # metadata; discard only operation-owned staging/journal and fail.
            remove_tree_best_effort(prepared.staging_path)
            try:
                self.journals.remove(operation_id)
            except Exception:
                pass
            raise PackValidationError("Pack metadata changed during validation")

        installed = self.resolver.installed_pack_root(pack_id)
        backup = self.resolver.backup_pack_root(operation_id, pack_id)
        # This first classification is advisory; the current installed version is
        # re-read under the Pack write lock immediately before switch.
        prepared = replace(prepared, version_classification=classification, installed_version_before=installed_version)
        op_type = "UPDATE" if prepared.import_kind == ImportKind.EXISTING_PACK else "IMPORT"
        journal = self.journals.transition(
            journal,
            PackOperationState.VALIDATED,
            operation_type=op_type,
            pack_id=pack_id,
            new_digest=prepared.content_digest,
            new_version=prepared.pack_metadata["version"],
            installed_path=self.resolver.relative_to_persistent(installed),
            backup_path=self.resolver.relative_to_persistent(backup),
        )
        self._checkpoint("VALIDATED")
        journal = self.journals.transition(journal, PackOperationState.READY_TO_SWITCH)
        self._checkpoint("READY_TO_SWITCH")

        try:
            with self.coordinator.write_lock(pack_id, timeout=self.lock_timeout):
                current = self.repository.get_installed_pack(pack_id)
                locked_classification = classify_pack_version(
                    str(prepared.pack_metadata["version"]),
                    current.pack_version if current is not None else None,
                )
                if locked_classification == PackImportClassification.DOWNGRADE_SKIPPED:
                    remove_tree_best_effort(prepared.staging_path)
                    self.journals.remove(operation_id)
                    return PackImportSkipped(
                        classification=locked_classification,
                        pack_id=pack_id,
                        pack_name=str(prepared.pack_metadata["name"]),
                        incoming_version=str(prepared.pack_metadata["version"]),
                        installed_version=str(current.pack_version),
                    )
                prepared = replace(
                    prepared,
                    version_classification=locked_classification,
                    import_kind=ImportKind.NEW if current is None else ImportKind.EXISTING_PACK,
                    installed_version_before=current.pack_version if current is not None else None,
                )
                try:
                    if current is None:
                        self._install_new(prepared, journal, installed)
                    else:
                        # The journal may have been initially classified as IMPORT
                        # before a concurrent install won the race. Persist the
                        # actual operation type/current old generation before switch.
                        if journal.operation_type != "UPDATE":
                            journal = self.journals.update(journal, operation_type="UPDATE")
                        self._replace_existing(prepared, journal, installed, backup)
                except Exception:
                    self._recover_after_operation_failure(operation_id, pack_id)
                    raise
        except PackBusyError:
            remove_tree_best_effort(prepared.staging_path)
            try:
                self.journals.remove(operation_id)
            except Exception:
                pass
            raise
        return prepared


    def _recover_after_operation_failure(self, operation_id: str, pack_id: str) -> None:
        path = self.journals.path_for(operation_id)
        if not path.exists():
            return
        try:
            durable = self.journals.load(operation_id)
            from fantasy_store.pack.recovery import PackRecoveryManager
            recovery = PackRecoveryManager(
                self.paths,
                repository=self.repository,
                importer=self.importer,
                journal_store=self.journals,
                manifest_state=self.manifest_state,
                logger=self.logger,
                retry_delay=0.1,
            )
            decision = recovery.recover_operation(durable)
            if decision.final_state == PackOperationState.RECOVERY_REQUIRED.value:
                self.coordinator.mark_recovery_required(pack_id)
                raise PackRecoveryRequiredError(pack_id, "operation failure could not be safely reconciled")
        except PackRecoveryRequiredError:
            raise
        except Exception as recovery_exc:
            self.coordinator.mark_recovery_required(pack_id)
            self.logger.critical(
                "pack operation failure recovery itself failed: %s",
                recovery_exc,
                extra={"event_code": "PACK_RECOVERY_REQUIRED", "operation_id": operation_id, "pack_id": pack_id},
            )
            raise PackRecoveryRequiredError(pack_id, "operation failure recovery failed") from recovery_exc

    def _install_new(
        self,
        prepared: PreparedPack,
        journal: PackOperationJournal,
        installed: Path,
    ) -> None:
        pack_id = prepared.pack_metadata["pack_id"]
        if self.repository.get_installed_pack(pack_id) is not None or installed.exists():
            raise PackOperationError("pack became installed during staging; retry operation")
        if compute_content_digest(prepared.staging_path) != prepared.content_digest:
            journal = self._mark_recovery_required(journal, pack_id, "staging digest changed before switch")
            raise PackRecoveryRequiredError(pack_id, "staging digest changed before switch")
        try:
            self._retry_move(prepared.staging_path, installed, pack_id=pack_id)
        except OSError as exc:
            raise PackFileLockedError(pack_id, str(exc)) from exc
        self._checkpoint("NEW_RENAME_DONE")
        journal = self.journals.transition(journal, PackOperationState.FILES_SWITCHED)
        self._checkpoint("FILES_SWITCHED")

        now = utc_text(datetime.now(timezone.utc))
        metadata = prepared.pack_metadata
        row = InstalledPack(
            pack_id=pack_id,
            pack_name=metadata["name"],
            pack_version=metadata["version"],
            author=metadata["author"],
            description=metadata["description"],
            schema_version=int(metadata["schema_version"]),
            is_enabled=True,
            content_digest=prepared.content_digest,
            install_dir=self.resolver.relative_to_persistent(installed),
            installed_at=now,
            updated_at=now,
        )
        try:
            self.repository.insert_pack_with_items(row, prepared.prepared_items_master)
            self._checkpoint("DB_COMMIT_DONE")
        except Exception:
            self._rollback_new_known_generation(journal, installed, prepared.content_digest)
            raise

        journal = self.journals.transition(journal, PackOperationState.DB_SWITCHED)
        self._checkpoint("DB_SWITCHED")
        self._verify_new_consistency(pack_id, prepared.content_digest, installed, journal)
        journal = self.journals.transition(journal, PackOperationState.COMPLETED)
        self._checkpoint("COMPLETED")
        self._write_manifest_state_nonfatal(pack_id)
        self.logger.info(
            "new pack import completed",
            extra={"event_code": "PACK_IMPORT_COMPLETE", "operation_id": journal.operation_id, "pack_id": pack_id},
        )

    def _replace_existing(
        self,
        prepared: PreparedPack,
        journal: PackOperationJournal,
        installed: Path,
        backup: Path,
    ) -> None:
        pack_id = prepared.pack_metadata["pack_id"]
        old = self.repository.get_installed_pack(pack_id)
        if old is None or not installed.is_dir():
            raise PackOperationError("existing pack disappeared before update switch")
        try:
            installed_digest = compute_content_digest(installed)
        except Exception as exc:
            journal = self._mark_recovery_required(journal, pack_id, f"cannot observe installed digest: {exc}")
            raise PackRecoveryRequiredError(pack_id, "cannot observe installed digest") from exc
        if installed_digest != old.content_digest:
            journal = self._mark_recovery_required(journal, pack_id, "DB and installed old digest disagree")
            raise PackRecoveryRequiredError(pack_id, "DB and installed old digest disagree")
        if compute_content_digest(prepared.staging_path) != prepared.content_digest:
            journal = self._mark_recovery_required(journal, pack_id, "staging digest changed before update switch")
            raise PackRecoveryRequiredError(pack_id, "staging digest changed before update switch")

        journal = self.journals.update(
            journal,
            old_digest=old.content_digest,
            old_version=old.pack_version,
        )
        try:
            self._retry_move(installed, backup, pack_id=pack_id)
        except OSError as exc:
            raise PackFileLockedError(pack_id, str(exc)) from exc
        self._checkpoint("OLD_RENAME_DONE")
        journal = self.journals.transition(journal, PackOperationState.OLD_BACKED_UP)
        self._checkpoint("OLD_BACKED_UP")

        try:
            self._retry_move(prepared.staging_path, installed, pack_id=pack_id)
        except OSError as exc:
            try:
                self._retry_move(backup, installed, pack_id=pack_id)
            except OSError as rollback_exc:
                journal = self._mark_recovery_required(
                    journal,
                    pack_id,
                    f"new rename failed and old restore failed: {rollback_exc}",
                )
                raise PackRecoveryRequiredError(pack_id, "new rename and rollback both failed") from rollback_exc
            raise PackFileLockedError(pack_id, str(exc)) from exc

        self._checkpoint("NEW_RENAME_DONE")
        journal = self.journals.transition(journal, PackOperationState.FILES_SWITCHED)
        self._checkpoint("FILES_SWITCHED")
        now = utc_text(datetime.now(timezone.utc))
        metadata = prepared.pack_metadata
        try:
            self.repository.replace_pack_with_items(
                pack_id,
                pack_name=metadata["name"],
                pack_version=metadata["version"],
                author=metadata["author"],
                description=metadata["description"],
                schema_version=int(metadata["schema_version"]),
                content_digest=prepared.content_digest,
                install_dir=self.resolver.relative_to_persistent(installed),
                updated_at=now,
                items=prepared.prepared_items_master,
            )
            self._checkpoint("DB_COMMIT_DONE")
        except Exception:
            self._rollback_update_known_old(journal, pack_id, installed, backup, prepared)
            raise

        journal = self.journals.transition(journal, PackOperationState.DB_SWITCHED)
        self._checkpoint("DB_SWITCHED")
        self._verify_new_consistency(pack_id, prepared.content_digest, installed, journal)
        journal = self.journals.transition(journal, PackOperationState.COMPLETED)
        self._checkpoint("COMPLETED")
        self._write_manifest_state_nonfatal(pack_id)
        remove_tree_best_effort(
            backup.parent,
            on_error=lambda exc: self.logger.warning(
                "old pack backup cleanup failed: %s",
                exc,
                extra={"event_code": "PACK_BACKUP_CLEANUP_FAILED", "operation_id": journal.operation_id, "pack_id": pack_id},
            ),
        )
        self.logger.info(
            "pack update completed",
            extra={"event_code": "PACK_UPDATE_COMPLETE", "operation_id": journal.operation_id, "pack_id": pack_id},
        )

    def _rollback_new_known_generation(self, journal: PackOperationJournal, installed: Path, new_digest: str) -> None:
        pack_id = journal.pack_id
        assert pack_id is not None
        journal = self.journals.transition(journal, PackOperationState.ROLLING_BACK)
        try:
            if installed.exists():
                if compute_content_digest(installed) != new_digest:
                    raise PackRecoveryRequiredError(pack_id, "new import rollback found unknown installed generation")
                if not remove_tree_best_effort(installed):
                    raise PackRecoveryRequiredError(pack_id, "new import rollback could not remove known new generation")
            self.journals.remove(journal.operation_id)
        except Exception as exc:
            self._mark_recovery_required(journal, pack_id, str(exc))
            if isinstance(exc, PackRecoveryRequiredError):
                raise
            raise PackRecoveryRequiredError(pack_id, "new import rollback failed") from exc

    def _rollback_update_known_old(
        self,
        journal: PackOperationJournal,
        pack_id: str,
        installed: Path,
        backup: Path,
        prepared: PreparedPack,
    ) -> None:
        journal = self.journals.transition(journal, PackOperationState.ROLLING_BACK)
        try:
            if not backup.is_dir() or compute_content_digest(backup) != journal.old_digest:
                raise PackRecoveryRequiredError(pack_id, "rollback old backup is unavailable or mismatched")
            if installed.exists():
                if compute_content_digest(installed) != prepared.content_digest:
                    raise PackRecoveryRequiredError(pack_id, "rollback found unknown installed generation")
                if prepared.staging_path.exists():
                    remove_tree_best_effort(prepared.staging_path)
                self._retry_move(installed, prepared.staging_path, pack_id=pack_id)
            self._checkpoint("ROLLBACK_NEW_MOVED")
            self._retry_move(backup, installed, pack_id=pack_id)
            self._checkpoint("ROLLBACK_OLD_RESTORED")
            remove_tree_best_effort(prepared.staging_path)
            remove_tree_best_effort(backup.parent)
            self.journals.remove(journal.operation_id)
            self.logger.info(
                "pack update rollback completed",
                extra={"event_code": "PACK_ROLLBACK_COMPLETE", "operation_id": journal.operation_id, "pack_id": pack_id},
            )
        except BaseException as exc:
            # BaseException is intentionally re-raised for crash-injection tests;
            # the ROLLING_BACK journal and physical evidence remain observable.
            if isinstance(exc, Exception):
                self._mark_recovery_required(journal, pack_id, f"rollback failed: {exc}")
            raise

    def _verify_new_consistency(
        self,
        pack_id: str,
        new_digest: str,
        installed: Path,
        journal: PackOperationJournal,
    ) -> None:
        try:
            db_digest = self.repository.get_digest(pack_id)
            fs_digest = compute_content_digest(installed)
        except Exception as exc:
            self._mark_recovery_required(journal, pack_id, f"post-COMMIT digest observation failed: {exc}")
            raise PackRecoveryRequiredError(pack_id, "post-COMMIT digest observation failed") from exc
        if db_digest != new_digest or fs_digest != new_digest:
            self._mark_recovery_required(journal, pack_id, "post-COMMIT DB/installed/new digest mismatch")
            raise PackRecoveryRequiredError(pack_id, "post-COMMIT digest mismatch")

    def _mark_recovery_required(
        self,
        journal: PackOperationJournal,
        pack_id: str,
        message: str,
    ) -> PackOperationJournal:
        try:
            journal = self.journals.transition(journal, PackOperationState.RECOVERY_REQUIRED)
        finally:
            self.coordinator.mark_recovery_required(pack_id)
            self.logger.critical(
                "pack operation requires recovery: %s",
                message,
                extra={"event_code": "PACK_RECOVERY_REQUIRED", "operation_id": journal.operation_id, "pack_id": pack_id},
            )
        return journal

    def _write_manifest_state_nonfatal(self, pack_id: str) -> None:
        try:
            self.manifest_state.write_repository_state(self.repository)
        except Exception as exc:
            self.logger.warning(
                "pack manifest state backup update failed: %s",
                exc,
                extra={"event_code": "BACKUP_WRITE_FAILED", "pack_id": pack_id},
            )

    def uninstall_pack(self, pack_id: str) -> InstalledPack:
        """Safely uninstall one currently disabled Pack as a journaled lifecycle.

        The installed generation is first moved to the operation-owned backup,
        then pack_manifest.db is atomically updated. Recovery can therefore
        restore the old Pack before DB commit or complete the uninstall after DB
        commit. user_data/history are deliberately untouched.
        """
        with self.coordinator.write_lock(pack_id, timeout=self.lock_timeout):
            row = self.repository.get_installed_pack(pack_id)
            if row is None:
                raise PackOperationError("pack is not installed", code="PACK_NOT_FOUND")
            if row.is_enabled:
                raise PackUninstallRequiresDisabledError(pack_id)
            installed = self.resolver.installed_pack_root(pack_id)
            if not installed.is_dir():
                self.coordinator.mark_recovery_required(pack_id)
                raise PackRecoveryRequiredError(pack_id, "installed pack directory is missing")
            try:
                installed_digest = compute_content_digest(installed)
            except Exception as exc:
                self.coordinator.mark_recovery_required(pack_id)
                raise PackRecoveryRequiredError(pack_id, "installed digest cannot be observed") from exc
            if installed_digest != row.content_digest:
                self.coordinator.mark_recovery_required(pack_id)
                raise PackRecoveryRequiredError(pack_id, "installed/DB digest mismatch")

            operation_id = new_uuid_v4()
            staging = self.resolver.staging_root(operation_id)
            backup = self.resolver.backup_pack_root(operation_id, pack_id)
            journal = self.journals.create(operation_id, staging_path=staging)
            journal = self.journals.transition(
                journal,
                PackOperationState.VALIDATED,
                operation_type="UNINSTALL",
                pack_id=pack_id,
                old_digest=row.content_digest,
                old_version=row.pack_version,
                new_digest=None,
                new_version=None,
                installed_path=self.resolver.relative_to_persistent(installed),
                backup_path=self.resolver.relative_to_persistent(backup),
            )
            journal = self.journals.transition(journal, PackOperationState.READY_TO_SWITCH)
            self.logger.info(
                "pack uninstall start",
                extra={"event_code": "PACK_UNINSTALL_START", "operation_id": operation_id, "pack_id": pack_id},
            )
            try:
                self._retry_move(installed, backup, pack_id=pack_id)
                self._checkpoint("UNINSTALL_OLD_RENAME_DONE")
                journal = self.journals.transition(journal, PackOperationState.OLD_BACKED_UP)
                journal = self.journals.transition(journal, PackOperationState.FILES_SWITCHED)
                self._checkpoint("UNINSTALL_FILES_SWITCHED")
                if not self.repository.uninstall_pack(pack_id):
                    raise PackOperationError("pack disappeared during uninstall", code="PACK_NOT_FOUND")
                self._checkpoint("UNINSTALL_DB_COMMIT_DONE")
                journal = self.journals.transition(journal, PackOperationState.DB_SWITCHED)
                if self.repository.get_installed_pack(pack_id) is not None or installed.exists():
                    journal = self._mark_recovery_required(journal, pack_id, "post-uninstall DB/filesystem state mismatch")
                    raise PackRecoveryRequiredError(pack_id, "post-uninstall DB/filesystem state mismatch")
                journal = self.journals.transition(journal, PackOperationState.COMPLETED)
                self._write_manifest_state_nonfatal(pack_id)
                remove_tree_best_effort(
                    backup.parent,
                    on_error=lambda exc: self.logger.warning(
                        "uninstall backup cleanup failed: %s",
                        exc,
                        extra={"event_code": "PACK_BACKUP_CLEANUP_FAILED", "operation_id": operation_id, "pack_id": pack_id},
                    ),
                )
                self.logger.info(
                    "pack uninstall completed",
                    extra={"event_code": "PACK_UNINSTALL_COMPLETE", "operation_id": operation_id, "pack_id": pack_id},
                )
                return row
            except Exception:
                self._recover_after_operation_failure(operation_id, pack_id)
                raise

    def set_pack_enabled(self, pack_id: str, enabled: bool) -> None:
        with self.coordinator.write_lock(pack_id, timeout=self.lock_timeout):
            row = self.repository.get_installed_pack(pack_id)
            if row is None:
                raise PackOperationError("pack is not installed", code="PACK_NOT_FOUND")
            installed = self.resolver.installed_pack_root(pack_id)
            if not installed.is_dir():
                raise PackRecoveryRequiredError(pack_id, "installed pack directory is missing")
            try:
                digest = compute_content_digest(installed)
            except Exception as exc:
                raise PackRecoveryRequiredError(pack_id, "installed digest cannot be observed") from exc
            if digest != row.content_digest:
                raise PackRecoveryRequiredError(pack_id, "installed/DB digest mismatch")
            if not self.repository.set_enabled(pack_id, enabled):
                raise PackOperationError("pack disappeared during enable/disable", code="PACK_NOT_FOUND")
            self._write_manifest_state_nonfatal(pack_id)
            self.logger.info(
                "pack enabled state changed",
                extra={"event_code": "PACK_ENABLE" if enabled else "PACK_DISABLE", "pack_id": pack_id},
            )
