from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fantasy_store.domain.errors import DatabaseBackupError, DatabaseMigrationError
from fantasy_store.persistence.migration import (
    MigrationStep,
    USER_DB_NAME,
    initialize_database,
    migrate_database,
    validate_database_file,
)
from fantasy_store.persistence.user_backup import UserDataBackupManager


def make_manager(tmp_path: Path) -> tuple[Path, UserDataBackupManager]:
    db = tmp_path / "user_data" / "user_data.db"
    initialize_database(db, USER_DB_NAME)
    manager = UserDataBackupManager(
        db,
        tmp_path / "backups" / "user_data",
        tmp_path / "user_data" / "recovery_hold",
        max_supported_version=2,  # test-only future target
    )
    return db, manager


def test_test_only_migration_step_has_prebackup_transaction_validation_and_postbackup(tmp_path: Path):
    db, manager = make_manager(tmp_path)

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_fixture(value TEXT) STRICT")

    result = migrate_database(
        db,
        USER_DB_NAME,
        target_version=2,
        steps=[MigrationStep(1, 2, apply, "test-only")],
        backup_manager=manager,
    )
    assert result == 2
    assert validate_database_file(db, USER_DB_NAME, max_supported_version=2) == 2
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='migration_fixture'").fetchone() is not None
    finally:
        conn.close()
    assert len(manager.valid_backups()) >= 2  # pre + post migration generations
    assert not list(manager.backup_dir.glob("*.migration-protected"))


def test_prebackup_failure_prevents_migration_from_starting(tmp_path: Path):
    db, _ = make_manager(tmp_path)
    called = {"apply": 0}

    class FailingBackupManager:
        def backup_before_migration(self):
            raise DatabaseBackupError("injected")

    def apply(conn: sqlite3.Connection) -> None:
        called["apply"] += 1

    with pytest.raises(DatabaseMigrationError, match="not started"):
        migrate_database(
            db,
            USER_DB_NAME,
            target_version=2,
            steps=[MigrationStep(1, 2, apply)],
            backup_manager=FailingBackupManager(),  # type: ignore[arg-type]
        )
    assert called["apply"] == 0
    assert validate_database_file(db, USER_DB_NAME, max_supported_version=2) == 1


def test_failed_step_rolls_back_schema_and_version_and_is_not_retried(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    called = {"apply": 0}

    def apply(conn: sqlite3.Connection) -> None:
        called["apply"] += 1
        conn.execute("CREATE TABLE should_rollback(value TEXT) STRICT")
        raise RuntimeError("injected migration failure")

    with pytest.raises(DatabaseMigrationError):
        migrate_database(
            db,
            USER_DB_NAME,
            target_version=2,
            steps=[MigrationStep(1, 2, apply)],
            backup_manager=manager,
        )
    assert called["apply"] == 1
    assert validate_database_file(db, USER_DB_NAME, max_supported_version=2) == 1
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None
    finally:
        conn.close()
    assert list(manager.backup_dir.glob("*.migration-protected"))


def test_post_migration_invalid_db_is_held_and_restored_from_prebackup(tmp_path: Path):
    db, manager = make_manager(tmp_path)

    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE user_settings")

    with pytest.raises(DatabaseMigrationError, match="restored pre-backup"):
        migrate_database(
            db,
            USER_DB_NAME,
            target_version=2,
            steps=[MigrationStep(1, 2, apply)],
            backup_manager=manager,
        )
    assert validate_database_file(db, USER_DB_NAME, max_supported_version=2) == 1
    assert list(manager.recovery_hold_dir.glob("*/user_data.db"))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='user_settings'").fetchone() is not None
    finally:
        conn.close()
