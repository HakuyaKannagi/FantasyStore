from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fantasy_store.bootstrap import bootstrap_phase2
from fantasy_store.domain.errors import DatabaseRecoveryError, DatabaseVersionUnsupportedError
from fantasy_store.persistence.user_repository import UserRepository, UserSetting


def test_phase2_first_run_creates_snapshot_dirs_and_initial_backup(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    try:
        assert runtime.paths.snapshot_images.is_dir()
        assert runtime.paths.snapshot_pending.is_dir()
        assert runtime.user_schema_version == 1
        assert len(runtime.backup_manager.valid_backups()) == 1
    finally:
        runtime.close()


def test_phase2_bootstrap_recovers_corrupt_existing_db_from_backup(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    db = runtime.paths.user_db
    runtime.close()

    UserRepository(db).upsert_setting(UserSetting("later", '{"value":1}', "t"))
    db.write_bytes(b"corrupt")

    recovered = bootstrap_phase2(tmp_path)
    try:
        assert recovered.recovery_result is not None
        assert recovered.recovery_result.recovered is True
        # Initial backup predates the later setting, proving it restored rather
        # than silently creating a new DB or retaining the corrupt current DB.
        assert UserRepository(db).get_setting("later") is None
    finally:
        recovered.close()


def test_phase2_corrupt_db_without_backup_stops_without_empty_db(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    db = runtime.paths.user_db
    backup_dir = runtime.paths.user_backups
    runtime.close()
    for path in backup_dir.iterdir():
        path.unlink()
    db.write_bytes(b"corrupt")

    with pytest.raises(DatabaseRecoveryError):
        bootstrap_phase2(tmp_path)
    assert not db.exists()
    assert list((tmp_path / "user_data" / "recovery_hold").glob("*/user_data.db"))


def test_phase2_future_schema_stops_without_recovery_rollback(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    db = runtime.paths.user_db
    runtime.close()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()

    with pytest.raises(DatabaseVersionUnsupportedError):
        bootstrap_phase2(tmp_path)
    assert db.exists()
    assert list((tmp_path / "user_data" / "recovery_hold").glob("*/user_data.db")) == []


def test_second_start_after_failed_recovery_still_refuses_empty_db(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    db = runtime.paths.user_db
    backup_dir = runtime.paths.user_backups
    runtime.close()
    for path in backup_dir.iterdir():
        path.unlink()
    db.write_bytes(b"corrupt")

    with pytest.raises(DatabaseRecoveryError):
        bootstrap_phase2(tmp_path)
    assert not db.exists()

    # The held corrupt DB proves this is not a pristine data area. A second
    # launch must fail again rather than reinterpret the missing DB as first run.
    with pytest.raises(DatabaseRecoveryError):
        bootstrap_phase2(tmp_path)
    assert not db.exists()


def test_missing_current_with_valid_backup_recovers_instead_of_initializing_empty(tmp_path: Path):
    runtime = bootstrap_phase2(tmp_path)
    db = runtime.paths.user_db
    UserRepository(db).upsert_setting(UserSetting("keep", '{"value":1}', "t"))
    runtime.backup_manager.create_backup()
    runtime.close()
    db.unlink()

    recovered = bootstrap_phase2(tmp_path)
    try:
        assert recovered.recovery_result is not None and recovered.recovery_result.recovered
        assert UserRepository(db).get_setting("keep") is not None
    finally:
        recovered.close()
