from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import logging

from fantasy_store.domain.errors import AlreadyRunningError
from fantasy_store.logging_setup import setup_logging
from fantasy_store.persistence.migration import (
    PACK_DB_NAME,
    USER_DB_NAME,
    MigrationStep,
    initialize_database,
    migrate_database,
    validate_database_file,
)
from fantasy_store.config import SCHEMA_VERSION
from fantasy_store.persistence.user_backup import RecoveryResult, UserDataBackupManager
from fantasy_store.runtime.app_lock import AppInstanceLock
from fantasy_store.runtime.paths import AppPaths, resolve_persistent_root
from fantasy_store.runtime.resource_locator import ResourceLocator
from fantasy_store.runtime.webview2_probe import WebView2ProbeResult, probe_webview2_phase1


@dataclass(slots=True)
class Phase1Runtime:
    paths: AppPaths
    lock: AppInstanceLock
    logger: logging.Logger
    user_schema_version: int
    pack_schema_version: int
    webview2_probe: WebView2ProbeResult

    def close(self) -> None:
        self.logger.info("phase 1 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.lock.release()

    def __enter__(self) -> "Phase1Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bootstrap_phase1(data_root: Path | str | None = None) -> Phase1Runtime:
    """Execute only the frozen Phase 1 bootstrap responsibilities.

    This is intentionally not the complete MVP startup: backup/recovery, pack
    journal recovery, pywebview startup, and later-phase services are not
    reported as implemented.
    """
    paths = AppPaths(resolve_persistent_root(data_root))
    paths.create_bootstrap_minimum()
    logger = setup_logging(paths.log_file)
    logger.info("phase 1 bootstrap start", extra={"event_code": "RUNTIME_START"})

    lock = AppInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        logger.error("second instance rejected", extra={"event_code": "RUNTIME_ALREADY_RUNNING"})
        raise

    try:
        paths.create_application_dirs()
        probe = probe_webview2_phase1()
        logger.info(
            "WebView2 phase-1 probe boundary reached: %s",
            probe.status,
            extra={"event_code": "RUNTIME_WEBVIEW2_PHASE1_PROBE"},
        )
        user_version = initialize_database(paths.user_db, USER_DB_NAME)
        pack_version = initialize_database(paths.pack_db, PACK_DB_NAME)
        logger.info(
            "database foundation initialized",
            extra={"event_code": "DB_INIT"},
        )
        return Phase1Runtime(paths, lock, logger, user_version, pack_version, probe)
    except Exception:
        lock.release()
        raise

# No product migration exists while the frozen schema version is 1. Future
# approved migrations must be registered explicitly; never synthesize a step.
USER_MIGRATIONS: tuple[MigrationStep, ...] = ()


@dataclass(slots=True)
class Phase2Runtime:
    paths: AppPaths
    lock: AppInstanceLock
    logger: logging.Logger
    user_schema_version: int
    pack_schema_version: int
    webview2_probe: WebView2ProbeResult
    backup_manager: UserDataBackupManager
    recovery_result: RecoveryResult | None

    def close(self) -> None:
        self.logger.info("phase 2 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.lock.release()

    def __enter__(self) -> "Phase2Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bootstrap_phase2(data_root: Path | str | None = None) -> Phase2Runtime:
    """Phase-2 bootstrap through user_data backup/recovery/migration only.

    Pack journal recovery, snapshot orphan cleanup, Bridge/UI, and other later
    phases remain deliberately outside this function.
    """
    paths = AppPaths(resolve_persistent_root(data_root))
    paths.create_bootstrap_minimum()
    logger = setup_logging(paths.log_file)
    logger.info("phase 2 bootstrap start", extra={"event_code": "RUNTIME_START"})

    lock = AppInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        logger.error("second instance rejected", extra={"event_code": "RUNTIME_ALREADY_RUNNING"})
        raise

    try:
        # Freeze ordering is unchanged: Application directories are still
        # created only after the instance lock is acquired.
        paths.create_application_dirs()
        probe = probe_webview2_phase1()
        logger.info(
            "WebView2 phase-1 probe boundary reached: %s",
            probe.status,
            extra={"event_code": "RUNTIME_WEBVIEW2_PHASE1_PROBE"},
        )

        backup_manager = UserDataBackupManager(
            paths.user_db,
            paths.user_backups,
            paths.recovery_hold,
            max_supported_version=SCHEMA_VERSION,
        )
        recovery_result: RecoveryResult | None = None
        if paths.user_db.exists():
            recovery_result = backup_manager.recover_if_current_invalid()
            user_version = validate_database_file(
                paths.user_db,
                USER_DB_NAME,
                max_supported_version=SCHEMA_VERSION,
                integrity_check=True,
            )
            if user_version < SCHEMA_VERSION:
                user_version = migrate_database(
                    paths.user_db,
                    USER_DB_NAME,
                    target_version=SCHEMA_VERSION,
                    steps=USER_MIGRATIONS,
                    backup_manager=backup_manager,
                )
        else:
            # A missing DB is first-run only when this data area contains no
            # evidence of prior user data. After a failed recovery the corrupt
            # DB lives in recovery_hold, so a later launch must never escape by
            # creating an empty DB.
            if backup_manager.has_recovery_evidence():
                recovery_result = backup_manager.recover_missing_current()
                user_version = validate_database_file(
                    paths.user_db,
                    USER_DB_NAME,
                    max_supported_version=SCHEMA_VERSION,
                    integrity_check=True,
                )
            else:
                user_version = initialize_database(paths.user_db, USER_DB_NAME)

        if recovery_result is not None and recovery_result.recovered:
            logger.warning(
                "user_data.db restored from validated internal backup: %s",
                recovery_result.backup_path,
                extra={"event_code": "DB_RECOVERY_SUCCESS"},
            )

        backup_manager.ensure_initial_backup()
        pack_version = initialize_database(paths.pack_db, PACK_DB_NAME)
        logger.info(
            "phase 2 user persistence ready",
            extra={"event_code": "DB_PHASE2_READY"},
        )
        return Phase2Runtime(
            paths,
            lock,
            logger,
            user_version,
            pack_version,
            probe,
            backup_manager,
            recovery_result,
        )
    except Exception:
        lock.release()
        raise


@dataclass(slots=True)
class Phase4Runtime:
    paths: AppPaths
    lock: AppInstanceLock
    logger: logging.Logger
    user_schema_version: int
    pack_schema_version: int
    webview2_probe: WebView2ProbeResult
    backup_manager: UserDataBackupManager
    recovery_result: RecoveryResult | None
    pack_recovery: "PackRecoveryManager"
    pack_access: "PackAccessCoordinator"
    pack_lifecycle: "PackLifecycleManager"
    pack_recovery_decisions: tuple["RecoveryDecision", ...]
    pack_safety_failures: tuple["PackSafetyDiagnostic", ...]
    pack_manifest_rebuilt: bool

    def close(self) -> None:
        self.logger.info("phase 4 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.lock.release()

    def __enter__(self) -> "Phase4Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bootstrap_phase4(
    data_root: Path | str | None = None,
    *,
    allow_recovery_required: bool = False,
) -> Phase4Runtime:
    """Bootstrap through Phase 4 pack recovery/lifecycle readiness.

    Application services, CartCheckoutCoordinator, Bridge/UI, and pywebview
    startup intentionally remain outside this Phase 4 boundary.
    """
    from fantasy_store.domain.errors import (
        PackRecoveryRequiredError,
        PackSafetyDiagnostic,
        PackStartupSafetyError,
    )
    from fantasy_store.pack.access_coordinator import PackAccessCoordinator
    from fantasy_store.pack.recovery import PackRecoveryManager
    from fantasy_store.pack.updater import PackLifecycleManager
    from fantasy_store.persistence.pack_repository import PackRepository

    paths = AppPaths(resolve_persistent_root(data_root))
    paths.create_bootstrap_minimum()
    logger = setup_logging(paths.log_file)
    logger.info("phase 4 bootstrap start", extra={"event_code": "RUNTIME_START"})

    lock = AppInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError:
        logger.error("second instance rejected", extra={"event_code": "RUNTIME_ALREADY_RUNNING"})
        raise

    try:
        # Frozen order: only application directories after instance lock.
        paths.create_application_dirs()
        probe = probe_webview2_phase1()
        logger.info(
            "WebView2 phase-1 probe boundary reached: %s",
            probe.status,
            extra={"event_code": "RUNTIME_WEBVIEW2_PHASE1_PROBE"},
        )

        # Phase 2 user_data recovery/migration boundary is preserved verbatim.
        backup_manager = UserDataBackupManager(
            paths.user_db,
            paths.user_backups,
            paths.recovery_hold,
            max_supported_version=SCHEMA_VERSION,
        )
        recovery_result: RecoveryResult | None = None
        if paths.user_db.exists():
            recovery_result = backup_manager.recover_if_current_invalid()
            user_version = validate_database_file(
                paths.user_db,
                USER_DB_NAME,
                max_supported_version=SCHEMA_VERSION,
                integrity_check=True,
            )
            if user_version < SCHEMA_VERSION:
                user_version = migrate_database(
                    paths.user_db,
                    USER_DB_NAME,
                    target_version=SCHEMA_VERSION,
                    steps=USER_MIGRATIONS,
                    backup_manager=backup_manager,
                )
        else:
            if backup_manager.has_recovery_evidence():
                recovery_result = backup_manager.recover_missing_current()
                user_version = validate_database_file(
                    paths.user_db,
                    USER_DB_NAME,
                    max_supported_version=SCHEMA_VERSION,
                    integrity_check=True,
                )
            else:
                user_version = initialize_database(paths.user_db, USER_DB_NAME)
        backup_manager.ensure_initial_backup()

        pack_repository = PackRepository(paths.pack_db)
        pack_recovery = PackRecoveryManager(paths, repository=pack_repository, logger=logger)
        rebuilt = pack_recovery.ensure_manifest_database()
        # ensure_manifest_database may replace the DB and repository instance.
        pack_repository = pack_recovery.repository
        decisions = tuple(pack_recovery.recover_all())
        failed = [d for d in decisions if d.final_state == "RECOVERY_REQUIRED"]
        safety_failures: list[PackSafetyDiagnostic] = []
        for decision in failed:
            try:
                journal = pack_recovery.journals.load(decision.operation_id)
                pack_version_text = journal.new_version or journal.old_version
                operation_type = journal.operation_type
                state = journal.state
            except Exception:
                # The recovery decision itself remains authoritative enough to
                # fail closed.  Do not leak journal read details to the UI.
                pack_version_text = None
                operation_type = None
                state = "RECOVERY_REQUIRED"
            safety_failures.append(
                PackSafetyDiagnostic(
                    category="PACK_RECOVERY_REQUIRED",
                    pack_id=decision.pack_id,
                    pack_version=pack_version_text,
                    operation_id=decision.operation_id,
                    operation_type=operation_type,
                    state=state,
                    message="Pack操作の復旧状態を安全に確定できません。",
                )
            )

        if failed and not allow_recovery_required:
            raise PackRecoveryRequiredError(
                failed[0].pack_id,
                f"unfinished pack operation cannot be safely recovered: {failed[0].operation_id}",
            )

        if not failed:
            try:
                pack_recovery.verify_manifest_consistency()
            except PackStartupSafetyError as exc:
                if not allow_recovery_required:
                    raise
                diagnostic = exc.diagnostic
                # Reaching manifest consistency with no unresolved RecoveryDecision
                # proves that journal recovery/lineage is healthy for this startup.
                # Preserve that observation separately from category text; restricted
                # maintenance later re-observes DB/files immediately before writes.
                if not failed and diagnostic.maintenance_level == "SAFE_MAINTENANCE":
                    diagnostic = replace(diagnostic, recovery_lineage_healthy=True)
                safety_failures.append(diagnostic)
                logger.critical(
                    "pack manifest consistency prevents normal storefront startup category=%s pack_id=%s",
                    diagnostic.category,
                    diagnostic.pack_id or "-",
                    extra={"event_code": "PACK_MANIFEST_RESTRICTED_STARTUP"},
                )
        else:
            logger.critical(
                "pack recovery remains unresolved; restricted store-manager startup only",
                extra={"event_code": "PACK_RECOVERY_RESTRICTED_STARTUP"},
            )
        pack_version = validate_database_file(
            paths.pack_db,
            PACK_DB_NAME,
            max_supported_version=SCHEMA_VERSION,
            integrity_check=True,
        )

        pack_access = PackAccessCoordinator()
        for diagnostic in safety_failures:
            if diagnostic.pack_id is not None and not diagnostic.safe_maintenance_allowed:
                pack_access.mark_recovery_required(diagnostic.pack_id)
            elif diagnostic.pack_id is not None:
                logger.warning(
                    "restricted safe maintenance available pack_id=%s category=%s",
                    diagnostic.pack_id,
                    diagnostic.category,
                    extra={"event_code": "PACK_SAFE_MAINTENANCE_AVAILABLE"},
                )
        pack_lifecycle = PackLifecycleManager(
            paths,
            repository=pack_repository,
            coordinator=pack_access,
            logger=logger,
        )
        if safety_failures:
            logger.critical(
                "phase 4 pack subsystem restricted; normal storefront is not ready",
                extra={"event_code": "DB_PHASE4_RESTRICTED"},
            )
        else:
            logger.info("phase 4 pack lifecycle ready", extra={"event_code": "DB_PHASE4_READY"})
        return Phase4Runtime(
            paths=paths,
            lock=lock,
            logger=logger,
            user_schema_version=user_version,
            pack_schema_version=pack_version,
            webview2_probe=probe,
            backup_manager=backup_manager,
            recovery_result=recovery_result,
            pack_recovery=pack_recovery,
            pack_access=pack_access,
            pack_lifecycle=pack_lifecycle,
            pack_recovery_decisions=decisions,
            pack_safety_failures=tuple(safety_failures),
            pack_manifest_rebuilt=rebuilt,
        )
    except Exception:
        lock.release()
        raise

@dataclass(slots=True)
class Phase5Runtime:
    phase4: Phase4Runtime
    user_repository: "UserRepository"
    catalog_service: "CatalogService"
    cart_checkout: "CartCheckoutCoordinator"
    cart_service: "CartService"
    snapshot_manager: "SnapshotManager"
    snapshot_cleaner: "SnapshotOrphanCleaner"
    snapshot_retention: "SnapshotRetentionManager"
    history_service: "HistoryService"
    history_maintenance_service: "HistoryMaintenanceService"
    stats_service: "StatsService"
    pack_service: "PackService"
    checkout_service: "CheckoutService"
    snapshot_cleanup_removed: tuple[Path, ...]

    @property
    def paths(self) -> AppPaths:
        return self.phase4.paths

    @property
    def lock(self) -> AppInstanceLock:
        return self.phase4.lock

    @property
    def logger(self) -> logging.Logger:
        return self.phase4.logger

    def close(self) -> None:
        self.logger.info("phase 5 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.phase4.close()

    def __enter__(self) -> "Phase5Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bootstrap_phase5(
    data_root: Path | str | None = None,
    *,
    phase4_runtime: Phase4Runtime | None = None,
) -> Phase5Runtime:
    """Bootstrap through Phase 5 application-service readiness.

    Phase 4 already guarantees AppInstanceLock -> user-data recovery/migration ->
    pack manifest health/recovery/consistency. Snapshot orphan cleanup is added
    only after that boundary, before services are exposed as ready.
    """
    from fantasy_store.application.cart_checkout_coordinator import CartCheckoutCoordinator
    from fantasy_store.application.cart_service import CartService
    from fantasy_store.application.catalog_service import CatalogService
    from fantasy_store.application.checkout_service import CheckoutService
    from fantasy_store.application.history_service import HistoryService
    from fantasy_store.application.history_maintenance_service import HistoryMaintenanceService
    from fantasy_store.application.pack_service import PackService
    from fantasy_store.application.stats_service import StatsService
    from fantasy_store.pack.asset_resolver import AssetResolver
    from fantasy_store.pack.path_resolver import PackPathResolver
    from fantasy_store.persistence.user_repository import UserRepository
    from fantasy_store.snapshot.cleanup import SnapshotOrphanCleaner
    from fantasy_store.snapshot.manager import SnapshotManager
    from fantasy_store.snapshot.retention import SnapshotRetentionManager

    phase4 = phase4_runtime or bootstrap_phase4(data_root)
    try:
        paths = phase4.paths
        users = UserRepository(paths.user_db)
        pack_repo = phase4.pack_lifecycle.repository
        assets = AssetResolver(PackPathResolver(paths))
        cart_checkout = CartCheckoutCoordinator()
        snapshot_manager = SnapshotManager(paths)
        snapshot_cleaner = SnapshotOrphanCleaner(paths, users, logger=phase4.logger)
        snapshot_retention = SnapshotRetentionManager(users, snapshot_manager, logger=phase4.logger)
        try:
            removed = snapshot_cleaner.cleanup()
        except Exception as exc:
            # Per frozen Phase 5 policy cleanup is maintenance, not authority to
            # invalidate healthy orders or application startup.
            phase4.logger.warning(
                "snapshot orphan cleanup pass failed: %s",
                exc,
                extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED"},
            )
            removed = ()
        try:
            snapshot_retention.prune()
        except Exception as exc:
            phase4.logger.warning(
                "snapshot retention startup pass failed: %s",
                exc,
                extra={"event_code": "SNAPSHOT_RETENTION_FAILED"},
            )
        history = HistoryService(users, snapshot_manager)
        history_maintenance = HistoryMaintenanceService(users, snapshot_manager, cart_checkout, logger=phase4.logger)
        catalog = CatalogService(pack_repo, phase4.pack_access, assets)
        cart = CartService(users, pack_repo, phase4.pack_access, cart_checkout, assets)
        stats = StatsService(users)
        packs = PackService(pack_repo, phase4.pack_access, phase4.pack_lifecycle)
        checkout = CheckoutService(
            users,
            pack_repo,
            phase4.pack_access,
            cart_checkout,
            assets,
            snapshot_manager,
            history,
            phase4.backup_manager,
            snapshot_retention=snapshot_retention,
            logger=phase4.logger,
        )
        phase4.logger.info("phase 5 application services ready", extra={"event_code": "RUNTIME_PHASE5_READY"})
        return Phase5Runtime(
            phase4=phase4,
            user_repository=users,
            catalog_service=catalog,
            cart_checkout=cart_checkout,
            cart_service=cart,
            snapshot_manager=snapshot_manager,
            snapshot_cleaner=snapshot_cleaner,
            snapshot_retention=snapshot_retention,
            history_service=history,
            history_maintenance_service=history_maintenance,
            stats_service=stats,
            pack_service=packs,
            checkout_service=checkout,
            snapshot_cleanup_removed=tuple(removed),
        )
    except Exception:
        phase4.close()
        raise

@dataclass(slots=True)
class Phase6Runtime:
    phase5: Phase5Runtime
    bridge: "BridgeApi"
    image_resolver: "LocalImageResolver"
    resource_locator: ResourceLocator
    ui_index: Path

    @property
    def paths(self) -> AppPaths:
        return self.phase5.paths

    @property
    def lock(self) -> AppInstanceLock:
        return self.phase5.lock

    @property
    def logger(self) -> logging.Logger:
        return self.phase5.logger

    def close(self) -> None:
        self.logger.info("phase 6 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.phase5.close()

    def __enter__(self) -> "Phase6Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bootstrap_phase6(
    data_root: Path | str | None = None,
    *,
    file_picker=None,
    resource_locator: ResourceLocator | None = None,
    phase5_runtime: Phase5Runtime | None = None,
    store_manager: bool = False,
) -> Phase6Runtime:
    """Bootstrap through Phase 6 Bridge/UI resource readiness.

    This intentionally does not create a pywebview window or claim WebView2,
    native-dialog, CSP, or packaging integration. Those remain Phase 7.
    """
    from fantasy_store.bridge.api import BridgeApi
    from fantasy_store.bridge.file_picker import DeferredFilePicker
    from fantasy_store.bridge.image_resolver import LocalImageResolver
    from fantasy_store.pack.asset_resolver import AssetResolver
    from fantasy_store.pack.path_resolver import PackPathResolver

    phase5 = phase5_runtime or bootstrap_phase5(data_root)
    try:
        locator = resource_locator or ResourceLocator()
        ui_index = locator.resolve("ui/index.html")
        if not ui_index.is_file():
            raise RuntimeError("Phase 6 UI resource is missing")
        assets = AssetResolver(PackPathResolver(phase5.paths))
        image_resolver = LocalImageResolver(assets, phase5.snapshot_manager, phase5.user_repository)
        bridge_class = BridgeApi
        if store_manager:
            from fantasy_store.bridge.store_manager_api import StoreManagerBridgeApi
            bridge_class = StoreManagerBridgeApi
        bridge_kwargs = dict(
            catalog=phase5.catalog_service,
            cart=phase5.cart_service,
            checkout=phase5.checkout_service,
            history=phase5.history_service,
            stats=phase5.stats_service,
            packs=phase5.pack_service,
            file_picker=file_picker or DeferredFilePicker(),
            logger=phase5.logger,
        )
        if store_manager:
            bridge_kwargs["history_maintenance"] = phase5.history_maintenance_service
        bridge = bridge_class(**bridge_kwargs)
        phase5.logger.info("phase 6 bridge/UI boundary ready", extra={"event_code": "RUNTIME_PHASE6_READY"})
        return Phase6Runtime(
            phase5=phase5,
            bridge=bridge,
            image_resolver=image_resolver,
            resource_locator=locator,
            ui_index=ui_index,
        )
    except Exception:
        phase5.logger.exception("phase 6 bootstrap failed", extra={"event_code": "RUNTIME_PHASE6_BOOTSTRAP_FAILED"})
        phase5.close()
        raise

@dataclass(slots=True)
class Phase7Runtime:
    phase6: Phase6Runtime
    webview2_probe: WebView2ProbeResult
    window_holder: "WindowHolder"
    webview_integration: "WebViewIntegration"

    @property
    def paths(self) -> AppPaths:
        return self.phase6.paths

    @property
    def lock(self) -> AppInstanceLock:
        return self.phase6.lock

    @property
    def logger(self) -> logging.Logger:
        return self.phase6.logger

    def run_window(self):
        return self.webview_integration.run()

    def close(self) -> None:
        # Stop new Bridge/file-picker/UI work first, then keep persistence and the
        # process instance lock alive until in-flight pywebview API workers finish.
        self.webview_integration.managed_bridge._begin_shutdown()
        self.window_holder.stop_accepting_work()
        self.webview_integration.dispatcher.close()
        self.webview_integration.managed_bridge._wait_for_idle()
        self.logger.info("phase 7 runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.phase6.close()

    def __enter__(self) -> "Phase7Runtime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()




@dataclass(slots=True)
class StoreManagerRecoveryRuntime:
    phase4: Phase4Runtime
    webview2_probe: WebView2ProbeResult
    recovery_decisions: tuple["RecoveryDecision", ...]
    safety_failures: tuple["PackSafetyDiagnostic", ...]
    maintenance: "StoreManagerRecoveryMaintenance"
    maintenance_api: "StoreManagerRecoveryApi"
    recovery_integration: "StoreManagerRecoveryIntegration"

    @property
    def paths(self) -> AppPaths:
        return self.phase4.paths

    @property
    def lock(self) -> AppInstanceLock:
        return self.phase4.lock

    @property
    def logger(self) -> logging.Logger:
        return self.phase4.logger

    @property
    def recovery_mode(self) -> bool:
        return True

    def run_window(self):
        return self.recovery_integration.run()

    def close(self) -> None:
        self.logger.info("store-manager recovery runtime shutdown", extra={"event_code": "RUNTIME_STOP"})
        self.phase4.close()

def bootstrap_phase7(
    data_root: Path | str | None = None,
    *,
    resource_locator: ResourceLocator | None = None,
    webview_provider=None,
    webview2_version_getter=None,
    platform_name: str | None = None,
    store_manager: bool = False,
):
    """Build the Phase-7 integration boundary without declaring Gate B success.

    Normal startup remains fail-closed on unresolved Pack recovery.  A Human-
    requested ``store_manager`` startup still runs automatic recovery first, but
    if the state cannot be safely resolved it returns a restricted maintenance
    runtime that exposes recovery evidence only and no store/lifecycle Bridge.
    """
    import importlib

    from fantasy_store.runtime.webview2_probe import probe_webview2
    from fantasy_store.runtime.webview_file_picker import PyWebViewFilePicker, WindowHolder
    from fantasy_store.runtime.webview_runtime import WebViewIntegration

    provider = webview_provider or (lambda: importlib.import_module("webview"))

    if store_manager:
        phase4 = bootstrap_phase4(data_root, allow_recovery_required=True)
        failed = tuple(d for d in phase4.pack_recovery_decisions if d.final_state == "RECOVERY_REQUIRED")
        safety_failures = tuple(phase4.pack_safety_failures)
        if safety_failures:
            try:
                from fantasy_store.runtime.store_manager_recovery import (
                    StoreManagerRecoveryApi,
                    StoreManagerRecoveryIntegration,
                    StoreManagerRecoveryMaintenance,
                    build_recovery_rows,
                    render_store_manager_recovery_html,
                )
                probe = probe_webview2(
                    platform_name=platform_name,
                    version_getter=webview2_version_getter,
                    logger=phase4.logger,
                )
                journals = [phase4.pack_recovery.journals.load(p) for p in phase4.pack_recovery.journals.list_final_journals()]
                maintenance = StoreManagerRecoveryMaintenance(phase4)
                maintenance_statuses = {
                    diagnostic.pack_id: maintenance.assess(diagnostic.pack_id)
                    for diagnostic in safety_failures
                    if diagnostic.pack_id is not None
                }
                rows = build_recovery_rows(
                    journals,
                    failed,
                    safety_failures=safety_failures,
                    maintenance_statuses=maintenance_statuses,
                )
                maintenance_api = StoreManagerRecoveryApi(maintenance, logger=phase4.logger)
                for diagnostic in safety_failures:
                    phase4.logger.critical(
                        "store-manager restricted startup reason=%s pack_id=%s",
                        diagnostic.category,
                        diagnostic.pack_id or "-",
                        extra={"event_code": "STORE_MANAGER_RESTRICTED_STARTUP"},
                    )
                integration = StoreManagerRecoveryIntegration(
                    render_store_manager_recovery_html(rows),
                    api=maintenance_api,
                    logger=phase4.logger,
                    webview_provider=provider,
                )
                return StoreManagerRecoveryRuntime(
                    phase4=phase4,
                    webview2_probe=probe,
                    recovery_decisions=failed,
                    safety_failures=safety_failures,
                    maintenance=maintenance,
                    maintenance_api=maintenance_api,
                    recovery_integration=integration,
                )
            except Exception:
                phase4.close()
                raise
        # Recovery is healthy: continue from the already-acquired Phase 4 runtime
        # rather than re-running bootstrap and trying to acquire the process lock.
        phase5 = bootstrap_phase5(data_root, phase4_runtime=phase4)
        phase6 = bootstrap_phase6(
            data_root,
            file_picker=None,
            resource_locator=resource_locator,
            phase5_runtime=phase5,
            store_manager=True,
        )
    else:
        phase6 = bootstrap_phase6(data_root, resource_locator=resource_locator)

    holder = WindowHolder()
    picker = PyWebViewFilePicker(holder.get, webview_provider=provider)
    # Replace the deferred picker only after the Phase 6 runtime exists.
    phase6.bridge.file_picker = picker
    try:
        probe = probe_webview2(platform_name=platform_name, version_getter=webview2_version_getter, logger=phase6.logger)
        phase6.logger.info(
            "formal WebView2 probe result=%s version=%s",
            probe.status,
            probe.version or "-",
            extra={"event_code": "WEBVIEW2_PROBE"},
        )
        ui_index = phase6.resource_locator.resolve("ui/index-store-manager.html" if store_manager else "ui/index.html")
        if not ui_index.is_file():
            raise RuntimeError("Phase 7 UI mode resource is missing")
        integration = WebViewIntegration(phase6, holder, webview_provider=provider, ui_index=ui_index)
        return Phase7Runtime(
            phase6=phase6,
            webview2_probe=probe,
            window_holder=holder,
            webview_integration=integration,
        )
    except Exception:
        phase6.logger.exception("phase 7 bootstrap failed", extra={"event_code": "RUNTIME_PHASE7_BOOTSTRAP_FAILED"})
        phase6.close()
        raise

