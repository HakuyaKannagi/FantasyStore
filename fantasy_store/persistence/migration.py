from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TYPE_CHECKING

from fantasy_store.config import SCHEMA_VERSION
from fantasy_store.domain.errors import (
    DatabaseMigrationError,
    DatabaseSchemaCorruptError,
    DatabaseVersionUnsupportedError,
)
from .connection import connect, connect_readonly, connect_readonly_immutable

if TYPE_CHECKING:
    from .user_backup import UserDataBackupManager

USER_DB_NAME = "user_data"
PACK_DB_NAME = "pack_manifest"

USER_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS cart_items (
    pack_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 999),
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (pack_id, item_id)
) STRICT;
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    purchased_at TEXT NOT NULL,
    total_amount_json TEXT NOT NULL,
    total_quantity INTEGER NOT NULL CHECK (total_quantity > 0),
    line_count INTEGER NOT NULL CHECK (line_count > 0)
) STRICT;
CREATE TABLE IF NOT EXISTS order_items_snapshot (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    line_no INTEGER NOT NULL CHECK (line_no > 0),
    pack_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    unit_price_json TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    line_total_json TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    primary_image_snapshot_path TEXT,
    primary_image_snapshot_sha256 TEXT,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    UNIQUE (order_id, line_no),
    CHECK ((primary_image_snapshot_path IS NULL AND primary_image_snapshot_sha256 IS NULL)
        OR (primary_image_snapshot_path IS NOT NULL AND primary_image_snapshot_sha256 IS NOT NULL))
) STRICT;
CREATE TABLE IF NOT EXISTS purchase_requests (
    request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
) STRICT;
CREATE TABLE IF NOT EXISTS user_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_orders_purchased_at ON orders(purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items_snapshot(order_id, line_no);
"""

PACK_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS installed_packs (
    pack_id TEXT PRIMARY KEY,
    pack_name TEXT NOT NULL,
    pack_version TEXT NOT NULL,
    author TEXT NOT NULL,
    description TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    content_digest TEXT NOT NULL,
    install_dir TEXT NOT NULL,
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS items_master (
    pack_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    price_significand TEXT NOT NULL,
    price_exponent INTEGER NOT NULL CHECK (price_exponent BETWEEN 0 AND 9000000000000000000),
    price_magnitude INTEGER NOT NULL CHECK (price_magnitude BETWEEN -1 AND 9000000000000000063),
    price_sort_digits TEXT NOT NULL CHECK (length(price_sort_digits) = 64),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    images_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    PRIMARY KEY (pack_id, item_id),
    FOREIGN KEY (pack_id) REFERENCES installed_packs(pack_id) ON DELETE CASCADE
) STRICT;
CREATE INDEX IF NOT EXISTS idx_installed_packs_enabled ON installed_packs(is_enabled, pack_id);
CREATE INDEX IF NOT EXISTS idx_items_category ON items_master(category, pack_id, item_id);
CREATE INDEX IF NOT EXISTS idx_items_price ON items_master(price_magnitude, price_sort_digits, pack_id, item_id);
CREATE INDEX IF NOT EXISTS idx_items_name ON items_master(item_name, pack_id, item_id);
"""

USER_REQUIRED_TABLES = frozenset({
    "schema_meta", "cart_items", "orders", "order_items_snapshot", "purchase_requests", "user_settings"
})
PACK_REQUIRED_TABLES = frozenset({"schema_meta", "installed_packs", "items_master"})


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    name: str
    ddl: str
    required_tables: frozenset[str]


SCHEMAS = {
    USER_DB_NAME: DatabaseSchema(USER_DB_NAME, USER_DDL, USER_REQUIRED_TABLES),
    PACK_DB_NAME: DatabaseSchema(PACK_DB_NAME, PACK_DDL, PACK_REQUIRED_TABLES),
}


def sqlite_supports_strict_tables() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE strict_probe (value TEXT NOT NULL) STRICT")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _execute_script_statements(conn: sqlite3.Connection, ddl: str) -> None:
    # executescript() performs transaction control of its own; split the frozen
    # DDL into simple statements so creation + schema version remain one explicit
    # transaction.
    for statement in ddl.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows if not row[0].startswith("sqlite_")}


def _read_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error as exc:
        raise DatabaseSchemaCorruptError("schema_meta cannot be read") from exc
    if row is None:
        raise DatabaseSchemaCorruptError("schema_meta.schema_version is missing")
    try:
        version = int(row[0])
    except (TypeError, ValueError) as exc:
        raise DatabaseSchemaCorruptError("schema_meta.schema_version is not an integer") from exc
    user_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if user_version != version:
        raise DatabaseSchemaCorruptError("schema_meta and PRAGMA user_version disagree")
    return version


def initialize_database(path: Path, schema_name: str) -> int:
    schema = SCHEMAS[schema_name]
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_readonly(path) if existed else connect(path)
    try:
        if existed:
            tables = _existing_tables(conn)
            if not schema.required_tables.issubset(tables):
                missing = sorted(schema.required_tables - tables)
                raise DatabaseSchemaCorruptError(f"required tables are missing: {missing}")
            version = _read_schema_version(conn)
            if version > SCHEMA_VERSION:
                raise DatabaseVersionUnsupportedError(version, SCHEMA_VERSION)
            if version < SCHEMA_VERSION:
                # No pre-v1 schema exists in the frozen MVP. Future migrations
                # are added as explicit sequential steps; never synthesize one.
                raise DatabaseSchemaCorruptError(
                    f"no migration path is defined from schema version {version} to {SCHEMA_VERSION}"
                )
            return version

        conn.execute("BEGIN IMMEDIATE")
        try:
            _execute_script_statements(conn, schema.ddl)
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return SCHEMA_VERSION
    finally:
        conn.close()


def get_schema_version(path: Path, schema_name: str) -> int:
    schema = SCHEMAS[schema_name]
    conn = connect_readonly(path)
    try:
        tables = _existing_tables(conn)
        if not schema.required_tables.issubset(tables):
            raise DatabaseSchemaCorruptError("required table set is incomplete")
        return _read_schema_version(conn)
    finally:
        conn.close()


def validate_database_file(
    path: Path,
    schema_name: str,
    *,
    max_supported_version: int = SCHEMA_VERSION,
    integrity_check: bool = True,
    immutable: bool = False,
) -> int:
    """Validate an existing DB without applying write PRAGMAs.

    Used by backup/recovery and migration gates. A future schema is reported as
    DB_VERSION_UNSUPPORTED rather than being treated as corruption so recovery
    cannot silently roll it back.
    """
    path = Path(path)
    if not path.exists():
        raise DatabaseSchemaCorruptError(f"database file is missing: {path}")
    schema = SCHEMAS[schema_name]
    conn = connect_readonly_immutable(path) if immutable else connect_readonly(path)
    try:
        if integrity_check:
            try:
                rows = conn.execute("PRAGMA integrity_check").fetchall()
            except sqlite3.Error as exc:
                raise DatabaseSchemaCorruptError("PRAGMA integrity_check failed") from exc
            if not rows or any(str(row[0]).lower() != "ok" for row in rows):
                detail = "; ".join(str(row[0]) for row in rows[:5]) if rows else "no result"
                raise DatabaseSchemaCorruptError(f"integrity_check is not ok: {detail}")

        tables = _existing_tables(conn)
        if not schema.required_tables.issubset(tables):
            missing = sorted(schema.required_tables - tables)
            raise DatabaseSchemaCorruptError(f"required tables are missing: {missing}")
        version = _read_schema_version(conn)
        if version < 1:
            raise DatabaseSchemaCorruptError("schema version must be positive")
        if version > max_supported_version:
            raise DatabaseVersionUnsupportedError(version, max_supported_version)

        # This implementation does not need a persistent migration state marker:
        # each step and its version update are one SQLite transaction. If a
        # future migration introduces one, non-complete states are rejected.
        try:
            state_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='migration_state'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseSchemaCorruptError("schema_meta migration state cannot be read") from exc
        if state_row is not None and state_row[0] not in ("complete", "idle"):
            raise DatabaseSchemaCorruptError("database is in an incomplete migration state")
        return version
    finally:
        conn.close()


MigrationApply = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class MigrationStep:
    from_version: int
    to_version: int
    apply: MigrationApply
    name: str = ""

    def __post_init__(self) -> None:
        if self.from_version < 1 or self.to_version != self.from_version + 1:
            raise ValueError("migration steps must advance exactly one positive schema version")


def _select_migration_steps(
    current_version: int,
    target_version: int,
    steps: Iterable[MigrationStep],
) -> list[MigrationStep]:
    by_from: dict[int, MigrationStep] = {}
    for step in steps:
        if step.from_version in by_from:
            raise DatabaseMigrationError(f"duplicate migration step from v{step.from_version}")
        by_from[step.from_version] = step
    selected: list[MigrationStep] = []
    version = current_version
    while version < target_version:
        step = by_from.get(version)
        if step is None:
            raise DatabaseMigrationError(f"no migration path from v{version} to v{target_version}")
        selected.append(step)
        version = step.to_version
    return selected


def migrate_database(
    path: Path,
    schema_name: str,
    *,
    target_version: int,
    steps: Iterable[MigrationStep],
    backup_manager: "UserDataBackupManager",
) -> int:
    """Run sequential migrations with mandatory protected pre-backup.

    Product schema is currently v1, so no product v1->v2 step is registered.
    Tests may supply test-only steps to exercise this framework.
    """
    current = validate_database_file(
        path,
        schema_name,
        max_supported_version=target_version,
        integrity_check=True,
    )
    if current > target_version:
        raise DatabaseVersionUnsupportedError(current, target_version)
    selected = _select_migration_steps(current, target_version, steps)
    if not selected:
        return current

    try:
        pre_backup = backup_manager.backup_before_migration()
    except Exception as exc:
        raise DatabaseMigrationError(f"migration pre-backup failed; migration not started: {exc}") from exc

    for step in selected:
        conn = connect(path)
        step_error: Exception | None = None
        rollback_failed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            step.apply(conn)
            conn.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(step.to_version),),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise DatabaseSchemaCorruptError("schema_version row update did not affect exactly one row")
            conn.execute(f"PRAGMA user_version = {step.to_version}")
            conn.execute("COMMIT")
        except Exception as exc:
            step_error = exc
            try:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
            except Exception:
                rollback_failed = True
        finally:
            conn.close()

        if step_error is not None:
            if rollback_failed:
                try:
                    backup_manager.restore_specific_backup(pre_backup, hold_current=True)
                except Exception as recovery_exc:
                    raise DatabaseMigrationError(
                        f"migration failed, rollback failed, and pre-backup recovery failed: {recovery_exc}"
                    ) from recovery_exc
            else:
                # Confirm the rolled-back DB is still structurally healthy.
                try:
                    validate_database_file(
                        path,
                        schema_name,
                        max_supported_version=target_version,
                        integrity_check=True,
                    )
                except Exception as validation_exc:
                    try:
                        backup_manager.restore_specific_backup(pre_backup, hold_current=True)
                    except Exception as recovery_exc:
                        raise DatabaseMigrationError(
                            f"migration failed and current DB became invalid; recovery failed: {recovery_exc}"
                        ) from recovery_exc
                    raise DatabaseMigrationError(
                        f"migration failed; invalid current DB restored from pre-backup: {validation_exc}"
                    ) from step_error
            raise DatabaseMigrationError(f"migration step v{step.from_version}->v{step.to_version} failed: {step_error}") from step_error

        try:
            validate_database_file(
                path,
                schema_name,
                max_supported_version=target_version,
                integrity_check=True,
            )
        except Exception as validation_exc:
            try:
                backup_manager.restore_specific_backup(pre_backup, hold_current=True)
            except Exception as recovery_exc:
                raise DatabaseMigrationError(
                    f"post-migration validation failed and pre-backup recovery failed: {recovery_exc}"
                ) from recovery_exc
            raise DatabaseMigrationError(
                f"post-migration validation failed; restored pre-backup: {validation_exc}"
            ) from validation_exc

    try:
        # Freeze design requires a validated backup of the new schema before
        # the migration-prebackup loses its retention protection.
        backup_manager.backup_after_migration()
        backup_manager.release_migration_protection(pre_backup)
    except Exception as exc:
        raise DatabaseMigrationError(
            f"migration committed and validated, but post-migration backup finalization failed: {exc}"
        ) from exc

    return target_version

