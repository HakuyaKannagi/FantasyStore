from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fantasy_store.config import APP_DIR_NAME


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @property
    def logs(self) -> Path: return self.root / "logs"
    @property
    def log_file(self) -> Path: return self.logs / "app.log"
    @property
    def lock_file(self) -> Path: return self.root / "app.lock"
    @property
    def user_data(self) -> Path: return self.root / "user_data"
    @property
    def user_db(self) -> Path: return self.user_data / "user_data.db"
    @property
    def snapshots(self) -> Path: return self.user_data / "snapshots"
    @property
    def snapshot_images(self) -> Path: return self.snapshots / "images"
    @property
    def snapshot_pending(self) -> Path: return self.snapshots / ".pending"
    @property
    def recovery_hold(self) -> Path: return self.user_data / "recovery_hold"
    @property
    def packs(self) -> Path: return self.root / "packs"
    @property
    def pack_db(self) -> Path: return self.packs / "pack_manifest.db"
    @property
    def installed_packs(self) -> Path: return self.packs / "installed"
    @property
    def staging_packs(self) -> Path: return self.packs / "staging"
    @property
    def pack_backups(self) -> Path: return self.packs / "backup"
    @property
    def pack_recovery_hold(self) -> Path: return self.packs / "recovery_hold"
    @property
    def operations(self) -> Path: return self.root / "operations"
    @property
    def pack_operations(self) -> Path: return self.operations / "pack"
    @property
    def backups(self) -> Path: return self.root / "backups"
    @property
    def pack_manifest_state_backup(self) -> Path: return self.backups / "pack_manifest_state.json"
    @property
    def user_backups(self) -> Path: return self.backups / "user_data"
    @property
    def temp(self) -> Path: return self.root / "temp"
    @property
    def import_temp(self) -> Path: return self.temp / "import"

    def create_bootstrap_minimum(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

    def create_application_dirs(self) -> None:
        for path in (
            self.user_data,
            self.snapshot_images,
            self.snapshot_pending,
            self.recovery_hold,
            self.packs,
            self.installed_packs,
            self.staging_packs,
            self.pack_backups,
            self.pack_recovery_hold,
            self.pack_operations,
            self.user_backups,
            self.import_temp,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_persistent_root(override: Path | str | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    env_override = os.environ.get("FANTASY_STORE_DATA_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError("LOCALAPPDATA is unavailable")
        return (Path(local_app_data) / APP_DIR_NAME).resolve()
    # Development/test fallback only; the frozen release target remains Windows.
    return (Path.home() / ".local" / "share" / APP_DIR_NAME).resolve()
