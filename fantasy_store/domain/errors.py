from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DomainError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ValidationError(DomainError):
    def __init__(self, message: str, code: str = "VALIDATION_INVALID_ARGUMENT") -> None:
        super().__init__(code, message)


class AlreadyRunningError(DomainError):
    def __init__(self) -> None:
        super().__init__("RUNTIME_ALREADY_RUNNING", "another instance is using the same data area")


class DatabaseError(DomainError):
    pass


class DatabaseOpenError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_OPEN_FAILED", message)


class DatabaseSchemaCorruptError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_SCHEMA_CORRUPT", message)


class DatabaseVersionUnsupportedError(DatabaseError):
    def __init__(self, found: int, supported: int) -> None:
        super().__init__(
            "DB_VERSION_UNSUPPORTED",
            f"database schema version {found} is newer than supported version {supported}",
        )

class DatabaseBackupError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_BACKUP_FAILED", message)


class DatabaseRecoveryError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_RECOVERY_FAILED", message)


class DatabaseMigrationError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_MIGRATION_FAILED", message)



class PackValidationError(ValidationError):
    def __init__(self, message: str, code: str = "PACK_VALIDATION_FAILED") -> None:
        super().__init__(message, code=code)

class PackBusyError(DomainError):
    def __init__(self, pack_id: str) -> None:
        super().__init__("PACK_BUSY", f"pack is busy: {pack_id}")


class PackFileLockedError(DomainError):
    def __init__(self, pack_id: str, message: str = "pack files are locked") -> None:
        super().__init__("PACK_FILE_LOCKED", f"{message}: {pack_id}")


class PackRecoveryRequiredError(DomainError):
    def __init__(self, pack_id: str | None, message: str) -> None:
        label = pack_id if pack_id is not None else "unknown"
        super().__init__("RECOVERY_REQUIRED", f"pack {label}: {message}")


@dataclass(frozen=True, slots=True)
class PackSafetyDiagnostic:
    """Safe startup diagnostic for a Pack-subsystem-only failure.

    These fields may be shown by the restricted store-manager UI. Raw
    exception details and filesystem paths must never be stored here.

    ``maintenance_*`` fields are observation evidence only. They are never
    persisted to Pack journals and never weaken normal storefront fail-closed
    startup. A restricted maintenance operation is allowed only when every
    required observation remains true at the moment the operation starts.
    """

    category: str
    pack_id: str | None
    pack_version: str | None
    operation_id: str | None
    operation_type: str | None
    state: str
    message: str
    maintenance_level: str = "DIAGNOSTIC_ONLY"
    current_generation_safe: bool = False
    db_filesystem_match: bool = False
    recovery_lineage_healthy: bool = False
    enable_state_known: bool = False
    is_enabled: bool | None = None
    pack_name: str | None = None

    @property
    def safe_maintenance_allowed(self) -> bool:
        return (
            self.category == "PACK_VERSION_SCHEMA_INCOMPATIBLE"
            and self.maintenance_level == "SAFE_MAINTENANCE"
            and self.current_generation_safe
            and self.db_filesystem_match
            and self.recovery_lineage_healthy
            and self.enable_state_known
            and self.is_enabled is not None
            and self.pack_id is not None
        )


class PackStartupSafetyError(PackRecoveryRequiredError):
    """Pack-only startup safety failure eligible for restricted manager mode."""

    __slots__ = ("diagnostic",)

    def __init__(self, diagnostic: PackSafetyDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.pack_id, diagnostic.message)


class PackUninstallRequiresDisabledError(DomainError):
    def __init__(self, pack_id: str) -> None:
        super().__init__(
            "PACK_UNINSTALL_REQUIRES_DISABLED",
            f"pack must be disabled before uninstall: {pack_id}",
        )

class PackOperationError(DomainError):
    def __init__(self, message: str, code: str = "PACK_OPERATION_FAILED") -> None:
        super().__init__(code, message)

class ProductNotAvailableError(DomainError):
    def __init__(self, message: str = "product is not currently available") -> None:
        super().__init__("PRODUCT_NOT_AVAILABLE", message)


class CartQuantityLimitError(DomainError):
    def __init__(self, message: str = "cart quantity must remain between 1 and 999") -> None:
        super().__init__("CART_QUANTITY_LIMIT", message)


class CheckoutItemUnavailableError(DomainError):
    def __init__(self, message: str = "one or more checkout items are unavailable") -> None:
        super().__init__("CHECKOUT_ITEM_UNAVAILABLE", message)


class OrderNotFoundError(DomainError):
    def __init__(self, order_id: str) -> None:
        super().__init__("ORDER_NOT_FOUND", f"order was not found: {order_id}")


class DatabaseWriteError(DatabaseError):
    def __init__(self, message: str) -> None:
        super().__init__("DB_WRITE_FAILED", message)


class FileAccessDeniedError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("FS_ACCESS_DENIED", message)


class FileDiskFullError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("FS_DISK_FULL", message)
