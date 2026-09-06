from pathlib import Path

import pytest

from fantasy_store.bootstrap import bootstrap_phase1
from fantasy_store.domain.errors import AlreadyRunningError
from fantasy_store.runtime.app_lock import AppInstanceLock
from fantasy_store.runtime.paths import AppPaths


def test_bootstrap_creates_required_directories_and_databases(tmp_path: Path):
    root = tmp_path / "FantasyStore"
    with bootstrap_phase1(root) as runtime:
        paths = runtime.paths
        assert paths.root.is_dir()
        assert paths.logs.is_dir()
        assert paths.user_data.is_dir()
        assert paths.snapshot_images.is_dir()
        assert paths.snapshot_pending.is_dir()
        assert paths.recovery_hold.is_dir()
        assert paths.installed_packs.is_dir()
        assert paths.staging_packs.is_dir()
        assert paths.pack_backups.is_dir()
        assert paths.pack_operations.is_dir()
        assert paths.user_backups.is_dir()
        assert paths.import_temp.is_dir()
        assert paths.user_db.is_file()
        assert paths.pack_db.is_file()
        assert paths.log_file.is_file()
        assert runtime.lock.is_locked


def test_second_instance_does_not_initialize_application_directories(tmp_path: Path):
    root = tmp_path / "FantasyStore"
    paths = AppPaths(root)
    paths.create_bootstrap_minimum()
    first = AppInstanceLock(paths.lock_file).acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            bootstrap_phase1(root)
        assert paths.root.is_dir()
        assert paths.logs.is_dir()
        assert not paths.user_data.exists()
        assert not paths.packs.exists()
        text = paths.log_file.read_text(encoding="utf-8")
        assert "RUNTIME_ALREADY_RUNNING" in text
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path: Path):
    lock_path = tmp_path / "app.lock"
    first = AppInstanceLock(lock_path).acquire()
    first.release()
    second = AppInstanceLock(lock_path).acquire()
    assert second.is_locked
    second.release()


def test_os_lock_blocks_a_separate_process(tmp_path: Path):
    import subprocess
    import sys

    lock_path = tmp_path / "app.lock"
    first = AppInstanceLock(lock_path).acquire()
    code = r'''
import sys
from pathlib import Path
from fantasy_store.runtime.app_lock import AppInstanceLock
from fantasy_store.domain.errors import AlreadyRunningError
try:
    AppInstanceLock(Path(sys.argv[1])).acquire()
except AlreadyRunningError:
    raise SystemExit(23)
raise SystemExit(0)
'''
    try:
        result = subprocess.run([sys.executable, "-c", code, str(lock_path)], check=False)
        assert result.returncode == 23
    finally:
        first.release()
