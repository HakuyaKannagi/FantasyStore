from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fantasy_store.persistence.user_backup as ub
from fantasy_store.domain.errors import (
    DatabaseBackupError,
    DatabaseRecoveryError,
    DatabaseSchemaCorruptError,
    DatabaseVersionUnsupportedError,
)
from fantasy_store.persistence.connection import connect
from fantasy_store.persistence.migration import USER_DB_NAME, initialize_database
from fantasy_store.persistence.user_backup import UserDataBackupManager
from fantasy_store.persistence.user_repository import UserRepository, UserSetting


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 5, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def make_manager(tmp_path: Path, *, max_version: int = 1) -> tuple[Path, UserDataBackupManager]:
    user_dir = tmp_path / "user_data"
    db = user_dir / "user_data.db"
    initialize_database(db, USER_DB_NAME)
    manager = UserDataBackupManager(
        db,
        tmp_path / "backups" / "user_data",
        user_dir / "recovery_hold",
        max_supported_version=max_version,
        now=Clock(),
    )
    return db, manager


def put_setting(db: Path, value: str) -> None:
    UserRepository(db).upsert_setting(UserSetting("marker", f'{{"v":"{value}"}}', value))


def get_setting(db: Path) -> str | None:
    item = UserRepository(db).get_setting("marker")
    return item.setting_value_json if item else None


def corrupt(path: Path) -> None:
    path.write_bytes(b"not a sqlite database")


def test_normal_backup_uses_valid_tmp_then_final(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "one")
    backup = manager.create_backup()
    assert backup.exists()
    assert manager.validate(backup).schema_version == 1
    assert not list(manager.backup_dir.glob("*.tmp.db"))
    assert not Path(str(backup) + "-wal").exists()
    assert not Path(str(backup) + "-shm").exists()
    assert get_setting(backup) == '{"v":"one"}'


def test_backup_while_wal_connection_is_open_contains_committed_data(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    conn = connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO user_settings VALUES(?,?,?)", ("wal", '{"ok":true}', "t"))
        conn.execute("COMMIT")
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        backup = manager.create_backup()
    finally:
        conn.close()
    assert UserRepository(backup).get_setting("wal") is not None


def test_three_generation_retention(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    created = []
    for value in ["1", "2", "3", "4"]:
        put_setting(db, value)
        created.append(manager.create_backup())
    valid = manager.valid_backups()
    assert len(valid) == 3
    assert created[0] not in valid
    assert created[-1] in valid


def test_migration_protected_backup_is_excluded_from_normal_retention_deletion(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    protected = manager.backup_before_migration()
    for value in ["1", "2", "3", "4"]:
        put_setting(db, value)
        manager.create_backup()
    assert protected.exists()
    assert manager._protection_marker(protected).exists()
    assert len([p for p in manager.valid_backups() if p != protected]) == 3


def test_cleanup_failure_does_not_invalidate_new_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    old = []
    for value in ["1", "2", "3"]:
        put_setting(db, value)
        old.append(manager.create_backup())

    real_unlink = Path.unlink

    def fail_oldest(self: Path, *args, **kwargs):
        if self == old[0]:
            raise OSError("injected cleanup failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_oldest)
    put_setting(db, "4")
    newest = manager.create_backup()
    assert newest.exists()
    assert manager.validate(newest).schema_version == 1
    assert old[0].exists()


def test_latest_corrupt_backup_falls_back_to_previous_valid_generation(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "old")
    older = manager.create_backup()
    put_setting(db, "new")
    latest = manager.create_backup()
    corrupt(latest)
    assert manager.select_latest_valid_backup() == older


def test_all_corrupt_backups_yield_no_candidate(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    a = manager.create_backup()
    put_setting(db, "2")
    b = manager.create_backup()
    corrupt(a)
    corrupt(b)
    assert manager.select_latest_valid_backup() is None


def test_backup_missing_required_table_is_invalid(tmp_path: Path):
    _, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    conn = sqlite3.connect(backup)
    conn.execute("DROP TABLE user_settings")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseSchemaCorruptError):
        manager.validate(backup)


def test_integrity_failure_is_invalid(tmp_path: Path):
    _, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    corrupt(backup)
    with pytest.raises(Exception):
        manager.validate(backup)


def test_unknown_future_schema_backup_is_invalid_candidate(tmp_path: Path):
    _, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    conn = sqlite3.connect(backup)
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseVersionUnsupportedError):
        manager.validate(backup)
    assert manager.select_latest_valid_backup() is None


def test_incomplete_migration_state_is_rejected(tmp_path: Path):
    _, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    conn = sqlite3.connect(backup)
    conn.execute("INSERT INTO schema_meta(key,value) VALUES('migration_state','running')")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseSchemaCorruptError):
        manager.validate(backup)


def test_current_healthy_is_never_rolled_back_to_backup(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "backup")
    manager.create_backup()
    put_setting(db, "current-newer")
    result = manager.recover_if_current_invalid()
    assert result.recovered is False
    assert get_setting(db) == '{"v":"current-newer"}'


def test_corrupt_current_is_held_and_restored_from_latest_valid_backup(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "safe")
    backup = manager.create_backup()
    corrupt(db)
    Path(str(db) + "-wal").write_bytes(b"wal-sidecar")
    Path(str(db) + "-shm").write_bytes(b"shm-sidecar")

    result = manager.recover_if_current_invalid()
    assert result.recovered is True
    assert result.backup_path == backup
    assert result.recovery_hold_path is not None
    assert (result.recovery_hold_path / "user_data.db").exists()
    assert (result.recovery_hold_path / "user_data.db-wal").read_bytes() == b"wal-sidecar"
    assert (result.recovery_hold_path / "user_data.db-shm").exists()
    assert get_setting(db) == '{"v":"safe"}'


def test_corrupt_latest_backup_is_skipped_during_recovery(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "older")
    older = manager.create_backup()
    put_setting(db, "latest")
    latest = manager.create_backup()
    corrupt(latest)
    corrupt(db)
    result = manager.recover_if_current_invalid()
    assert result.backup_path == older
    assert get_setting(db) == '{"v":"older"}'


def test_no_valid_candidate_stops_and_does_not_generate_empty_db(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    put_setting(db, "lost-if-overwritten")
    corrupt(db)
    with pytest.raises(DatabaseRecoveryError):
        manager.recover_if_current_invalid()
    assert not db.exists()
    held = list(manager.recovery_hold_dir.glob("*/user_data.db"))
    assert len(held) == 1
    assert held[0].read_bytes() == b"not a sqlite database"


def test_future_current_schema_is_not_treated_as_corruption_or_rolled_back(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    manager.create_backup()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseVersionUnsupportedError):
        manager.recover_if_current_invalid()
    assert db.exists()
    assert list(manager.recovery_hold_dir.glob("*/user_data.db")) == []


def test_backup_midway_exception_does_not_create_final_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    real_connect = ub.connect

    class FailingSource:
        def __init__(self, inner):
            self.inner = inner

        def backup(self, destination):
            raise sqlite3.OperationalError("injected backup failure")

        def close(self):
            self.inner.close()

    monkeypatch.setattr(ub, "connect", lambda path: FailingSource(real_connect(path)))
    with pytest.raises(DatabaseBackupError):
        manager.create_backup()
    assert manager.list_backup_files() == []
    assert db.exists()


def test_backup_validation_failure_never_finalizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, manager = make_manager(tmp_path)
    real_validate = manager.validate

    def fail_tmp(path: Path, **kwargs):
        if Path(path).name.endswith(".tmp.db"):
            raise DatabaseSchemaCorruptError("injected validation failure")
        return real_validate(path, **kwargs)

    monkeypatch.setattr(manager, "validate", fail_tmp)
    with pytest.raises(DatabaseBackupError):
        manager.create_backup()
    assert manager.list_backup_files() == []
    assert not list(manager.backup_dir.glob("*.tmp.db"))


def test_backup_final_replace_failure_does_not_publish_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    real_replace = ub.os.replace

    def fail_final(src, dst):
        if str(src).endswith(".tmp.db") and Path(dst).parent == manager.backup_dir:
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(ub.os, "replace", fail_final)
    with pytest.raises(DatabaseBackupError):
        manager.create_backup()
    assert manager.list_backup_files() == []
    assert db.exists()


def test_recovery_hold_move_failure_stops_before_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    manager.create_backup()
    corrupt(db)
    real_replace = ub.os.replace

    def fail_hold(src, dst):
        if manager.recovery_hold_dir in Path(dst).parents:
            raise OSError("injected hold move failure")
        return real_replace(src, dst)

    monkeypatch.setattr(ub.os, "replace", fail_hold)
    with pytest.raises(DatabaseRecoveryError):
        manager.recover_if_current_invalid()
    assert db.exists()


def test_restore_tmp_creation_failure_stops_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    manager.create_backup()
    corrupt(db)
    real_sqlite_connect = ub.sqlite3.connect

    def fail_restore(path, *args, **kwargs):
        if "user_data.restore." in str(path):
            raise sqlite3.OperationalError("injected restore create failure")
        return real_sqlite_connect(path, *args, **kwargs)

    monkeypatch.setattr(ub.sqlite3, "connect", fail_restore)
    with pytest.raises(DatabaseRecoveryError):
        manager.recover_if_current_invalid()
    assert not db.exists()
    assert list(manager.recovery_hold_dir.glob("*/user_data.db"))


def test_restore_final_validation_failure_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    db.unlink()
    def fail_final_current():
        raise DatabaseSchemaCorruptError("injected final validation failure")

    monkeypatch.setattr(manager, "validate_current", fail_final_current)
    with pytest.raises(DatabaseRecoveryError):
        manager.restore_specific_backup(backup, hold_current=False)
    assert db.exists()  # switched DB remains, but caller must safe-stop


def test_schema_meta_and_user_version_mismatch_is_invalid(tmp_path: Path):
    _, manager = make_manager(tmp_path)
    backup = manager.create_backup()
    conn = sqlite3.connect(backup)
    conn.execute("PRAGMA user_version=0")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseSchemaCorruptError):
        manager.validate(backup)


def test_current_validation_is_wal_aware_and_sees_future_schema_in_uncheckpointed_wal(tmp_path: Path):
    db, manager = make_manager(tmp_path)
    conn = connect(db)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
        conn.execute("PRAGMA user_version=2")
        conn.execute("COMMIT")
        assert Path(str(db) + "-wal").exists()
        with pytest.raises(DatabaseVersionUnsupportedError):
            manager.recover_if_current_invalid()
    finally:
        conn.close()
    assert db.exists()
    assert list(manager.recovery_hold_dir.glob("*/user_data.db")) == []
