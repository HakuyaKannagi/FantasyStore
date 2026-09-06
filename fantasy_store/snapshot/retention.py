from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fantasy_store.domain.ids import validate_uuid_v4
from fantasy_store.persistence.user_repository import UserRepository
from fantasy_store.snapshot.manager import SnapshotManager

SNAPSHOT_HISTORY_HARD_LIMIT_BYTES = 536_870_912


@dataclass(frozen=True, slots=True)
class SnapshotRetentionResult:
    total_before: int
    total_after: int
    removed_paths: tuple[str, ...]
    observation_complete: bool = True


@dataclass(frozen=True, slots=True)
class SnapshotCapacityResult:
    required_bytes: int
    total_before: int
    total_after_prune: int
    capacity_available: bool
    observation_complete: bool
    removed_paths: tuple[str, ...]


class SnapshotRetentionManager:
    """Hard-cap retention for managed purchase-history snapshot image files.

    Physical bytes under ``snapshots/images/<order UUID>/`` are the capacity
    authority. Unreferenced managed files are not purchase-history authority and
    are removed first when capacity pressure exists. Referenced history images
    are then retired oldest-first using order timestamps from user_data.db;
    filesystem mtimes are deliberately not used for that history ordering.

    Only image files are retired. DB order and order-line snapshot metadata stay
    intact, so HistoryService naturally uses the bundled history placeholder
    when a referenced file is gone.

    For checkout, ``ensure_capacity`` is a fail-closed *image* admission gate:
    if current usage cannot be observed completely or enough bytes cannot be
    reclaimed, the order may still commit but no new snapshot image is written.
    """

    def __init__(
        self,
        repository: UserRepository,
        snapshots: SnapshotManager,
        *,
        max_bytes: int = SNAPSHOT_HISTORY_HARD_LIMIT_BYTES,
        logger: logging.Logger | None = None,
    ) -> None:
        if not isinstance(max_bytes, int) or max_bytes < 0:
            raise ValueError("max_bytes must be a non-negative integer")
        self.repository = repository
        self.snapshots = snapshots
        self.max_bytes = max_bytes
        self.logger = logger or logging.getLogger(__name__)

    def _physical_managed_files(self) -> tuple[dict[str, tuple[Path, int]], bool]:
        """Observe managed final bytes and whether the observation is complete."""
        result: dict[str, tuple[Path, int]] = {}
        root = self.snapshots.paths.snapshot_images
        if not root.exists():
            return result, True
        try:
            children = tuple(root.iterdir())
        except OSError as exc:
            self.logger.warning(
                "snapshot retention root observation failed: %s",
                exc,
                extra={"event_code": "SNAPSHOT_RETENTION_OBSERVE_FAILED"},
            )
            return result, False

        complete = True
        user_root = self.snapshots.paths.user_data.resolve()
        try:
            images_root = root.resolve()
        except OSError as exc:
            self.logger.warning(
                "snapshot retention root resolution failed: %s",
                exc,
                extra={"event_code": "SNAPSHOT_RETENTION_OBSERVE_FAILED"},
            )
            return result, False

        for child in children:
            try:
                is_dir = child.is_dir()
                is_symlink = child.is_symlink()
            except OSError as exc:
                complete = False
                self.logger.warning(
                    "snapshot retention child observation failed: %s",
                    exc,
                    extra={"event_code": "SNAPSHOT_RETENTION_OBSERVE_FAILED"},
                )
                continue
            if not is_dir or is_symlink:
                continue
            try:
                validate_uuid_v4(child.name)
                files = tuple(child.iterdir())
            except ValueError:
                continue
            except OSError as exc:
                complete = False
                self.logger.warning(
                    "snapshot retention directory observation failed: %s",
                    exc,
                    extra={"event_code": "SNAPSHOT_RETENTION_OBSERVE_FAILED"},
                )
                continue
            for path in files:
                try:
                    if not path.is_file() or path.is_symlink():
                        continue
                    resolved = path.resolve()
                    if images_root not in resolved.parents:
                        continue
                    size = int(path.stat().st_size)
                    if size < 0:
                        raise OSError("negative snapshot file size")
                    relative_path = resolved.relative_to(user_root).as_posix()
                except (OSError, ValueError) as exc:
                    complete = False
                    self.logger.warning(
                        "snapshot retention size observation failed: %s",
                        exc,
                        extra={"event_code": "SNAPSHOT_RETENTION_OBSERVE_FAILED"},
                    )
                    continue
                result[relative_path] = (path, size)
        return result, complete

    def _remove_file(self, relative_path: str, path: Path) -> bool:
        try:
            path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                pass
            self.logger.info(
                "purchase-history snapshot retired by capacity policy",
                extra={"event_code": "SNAPSHOT_RETENTION_PRUNE", "snapshot_path": relative_path},
            )
            return True
        except OSError as exc:
            self.logger.warning(
                "snapshot retention delete failed: %s",
                exc,
                extra={"event_code": "SNAPSHOT_RETENTION_DELETE_FAILED", "snapshot_path": relative_path},
            )
            return False

    def _prune_to_target(self, target_bytes: int) -> SnapshotRetentionResult:
        if target_bytes < 0:
            target_bytes = 0
        physical, complete = self._physical_managed_files()
        total = sum(size for _path, size in physical.values())
        before = total
        removed: list[str] = []
        if total <= target_bytes:
            return SnapshotRetentionResult(before, total, (), complete)

        ordered_referenced: list[str] = []
        referenced: set[str] = set()
        for _purchased_at, _order_id, _line_no, relative_path in self.repository.list_snapshot_retention_candidates():
            if relative_path in referenced:
                continue
            referenced.add(relative_path)
            if relative_path in physical:
                ordered_referenced.append(relative_path)

        # Managed files without DB history authority are capacity orphans. Under
        # checkout this runs while CartCheckoutCoordinator excludes another
        # checkout; at startup no checkout is active. Reclaim these first.
        orphan_paths = sorted(path for path in physical if path not in referenced)
        removal_order = orphan_paths + ordered_referenced

        for relative_path in removal_order:
            if total <= target_bytes:
                break
            entry = physical.get(relative_path)
            if entry is None:
                continue
            path, size = entry
            if self._remove_file(relative_path, path):
                total -= size
                removed.append(relative_path)

        if removed:
            self.logger.info(
                "snapshot retention prune pass completed",
                extra={
                    "event_code": "SNAPSHOT_RETENTION_PRUNE_COMPLETE",
                    "bytes_before": before,
                    "bytes_after": total,
                    "target_bytes": target_bytes,
                    "removed_count": len(removed),
                },
            )
        if total > target_bytes:
            self.logger.warning(
                "snapshot retention remains above requested capacity target",
                extra={
                    "event_code": "SNAPSHOT_RETENTION_LIMIT_REMAINS",
                    "bytes": total,
                    "target_bytes": target_bytes,
                },
            )
        return SnapshotRetentionResult(before, total, tuple(removed), complete)

    def prune(self) -> SnapshotRetentionResult:
        """Best-effort startup/post-checkout stable-state prune to the product cap."""
        return self._prune_to_target(self.max_bytes)

    def ensure_capacity(self, required_bytes: int) -> SnapshotCapacityResult:
        """Reserve budget for a whole new order before writing its images.

        The method never reserves filesystem state; checkout serialization and
        Pack read locks make the admission observation authoritative for normal
        in-process writers. If observation is incomplete, deletion cannot reclaim
        enough bytes, current usage remains over the cap, or the order itself is
        larger than the cap, image admission fails closed while checkout may
        continue without images.
        """
        if not isinstance(required_bytes, int) or required_bytes < 0:
            raise ValueError("required_bytes must be a non-negative integer")

        if required_bytes > self.max_bytes:
            physical, complete = self._physical_managed_files()
            total = sum(size for _path, size in physical.values())
            self.logger.warning(
                "new order snapshot exceeds hard limit and will be skipped",
                extra={
                    "event_code": "SNAPSHOT_RETENTION_CAPACITY_UNAVAILABLE",
                    "required_bytes": required_bytes,
                    "current_bytes": total,
                    "max_bytes": self.max_bytes,
                },
            )
            return SnapshotCapacityResult(required_bytes, total, total, False, complete, ())

        target = self.max_bytes - required_bytes
        result = self._prune_to_target(target)
        if result.total_before > self.max_bytes:
            self.logger.warning(
                "managed snapshot storage is already above hard limit",
                extra={
                    "event_code": "SNAPSHOT_RETENTION_ALREADY_OVER_LIMIT",
                    "current_bytes": result.total_before,
                    "max_bytes": self.max_bytes,
                },
            )

        available = result.observation_complete and result.total_after <= target
        if not available:
            self.logger.warning(
                "snapshot capacity unavailable; new order images must be skipped",
                extra={
                    "event_code": "SNAPSHOT_RETENTION_CAPACITY_UNAVAILABLE",
                    "required_bytes": required_bytes,
                    "current_bytes": result.total_after,
                    "max_bytes": self.max_bytes,
                    "observation_complete": result.observation_complete,
                },
            )
        return SnapshotCapacityResult(
            required_bytes,
            result.total_before,
            result.total_after,
            available,
            result.observation_complete,
            result.removed_paths,
        )
