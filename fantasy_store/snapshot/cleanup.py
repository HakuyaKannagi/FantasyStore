from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fantasy_store.domain.ids import validate_uuid_v4
from fantasy_store.persistence.user_repository import UserRepository
from fantasy_store.runtime.paths import AppPaths


class SnapshotOrphanCleaner:
    def __init__(
        self,
        paths: AppPaths,
        repository: UserRepository,
        *,
        logger: logging.Logger | None = None,
        min_age_seconds: int = 24 * 60 * 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.repository = repository
        self.logger = logger or logging.getLogger(__name__)
        self.min_age_seconds = min_age_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _old_enough(self, path: Path) -> bool:
        age = self._now().timestamp() - path.stat().st_mtime
        return age >= self.min_age_seconds

    @staticmethod
    def _managed_uuid_dir(path: Path) -> bool:
        if not path.is_dir() or path.is_symlink():
            return False
        try:
            validate_uuid_v4(path.name)
            return True
        except Exception:
            return False

    def _remove(self, path: Path) -> bool:
        try:
            shutil.rmtree(path)
            self.logger.info("snapshot orphan removed: %s", path.name, extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP"})
            return True
        except OSError as exc:
            self.logger.warning("snapshot orphan cleanup failed: %s", exc, extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED"})
            return False

    def cleanup(self) -> tuple[Path, ...]:
        removed: list[Path] = []
        referenced = set(self.repository.list_snapshot_paths())
        referenced_order_ids: set[str] = set()
        for rel in referenced:
            parts = rel.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[:2] == ["snapshots", "images"]:
                referenced_order_ids.add(parts[2])

        for root, is_pending in ((self.paths.snapshot_pending, True), (self.paths.snapshot_images, False)):
            if not root.exists():
                continue
            for child in root.iterdir():
                if not self._managed_uuid_dir(child):
                    continue
                try:
                    if not self._old_enough(child):
                        continue
                except OSError as exc:
                    self.logger.warning("snapshot orphan age check failed: %s", exc, extra={"event_code": "SNAPSHOT_ORPHAN_CLEANUP_FAILED"})
                    continue
                if not is_pending and child.name in referenced_order_ids:
                    continue
                if self._remove(child):
                    removed.append(child)
        return tuple(removed)
