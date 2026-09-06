from __future__ import annotations

import errno
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fantasy_store.domain.errors import FileAccessDeniedError, FileDiskFullError, ValidationError
from fantasy_store.domain.ids import validate_uuid_v4
from fantasy_store.pack.image_validator import ImageValidator
from fantasy_store.runtime.paths import AppPaths


@dataclass(frozen=True, slots=True)
class SnapshotSource:
    line_no: int
    source_path: Path


@dataclass(frozen=True, slots=True)
class PreparedSnapshot:
    line_no: int
    filename: str
    sha256: str


class SnapshotManager:
    def __init__(self, paths: AppPaths, *, image_validator: ImageValidator | None = None) -> None:
        self.paths = paths
        self.image_validator = image_validator or ImageValidator()

    @staticmethod
    def _translate_os_error(exc: OSError) -> Exception:
        if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
            return FileAccessDeniedError(str(exc))
        if exc.errno == errno.ENOSPC:
            return FileDiskFullError(str(exc))
        return exc

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with Path(path).open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            raise self._translate_os_error(exc)
        return digest.hexdigest()

    def _pending_dir(self, request_id: str) -> Path:
        validate_uuid_v4(request_id)
        root = self.paths.snapshot_pending.resolve()
        candidate = (root / request_id).resolve()
        if root not in candidate.parents:
            raise ValidationError("snapshot pending path escapes managed root")
        return candidate

    def _final_dir(self, order_id: str) -> Path:
        validate_uuid_v4(order_id)
        root = self.paths.snapshot_images.resolve()
        candidate = (root / order_id).resolve()
        if root not in candidate.parents:
            raise ValidationError("snapshot final path escapes managed root")
        return candidate

    def estimate_sources_bytes(self, sources: Iterable[SnapshotSource]) -> int:
        """Validate source images and return exact bytes that a snapshot copy would add."""
        total = 0
        for source in sources:
            if not isinstance(source.line_no, int) or source.line_no <= 0:
                raise ValidationError("snapshot line number must be positive")
            path = Path(source.source_path)
            self.image_validator.validate(path)
            try:
                size = int(path.stat().st_size)
            except OSError as exc:
                raise self._translate_os_error(exc)
            if size < 0:
                raise ValidationError("snapshot source size is invalid")
            total += size
        return total

    def prepare_pending(self, request_id: str, sources: Iterable[SnapshotSource]) -> tuple[PreparedSnapshot, ...]:
        pending = self._pending_dir(request_id)
        if pending.exists():
            # Same request_id is serialized by CartCheckoutCoordinator. A stale
            # pending directory from an earlier failed attempt is operation-owned
            # only when no committed purchase_request exists; CheckoutService
            # performs idempotency gating before this method.
            shutil.rmtree(pending)
        try:
            pending.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise self._translate_os_error(exc)

        prepared: list[PreparedSnapshot] = []
        try:
            for source in sources:
                if not isinstance(source.line_no, int) or source.line_no <= 0:
                    raise ValidationError("snapshot line number must be positive")
                validated = self.image_validator.validate(Path(source.source_path))
                suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[validated.format]
                filename = f"{source.line_no}{suffix}"
                target = pending / filename
                try:
                    with Path(source.source_path).open("rb") as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
                        dst.flush()
                        os.fsync(dst.fileno())
                except OSError as exc:
                    raise self._translate_os_error(exc)
                self.image_validator.validate(target)
                prepared.append(PreparedSnapshot(source.line_no, filename, self._hash_file(target)))
            return tuple(prepared)
        except Exception:
            self.cleanup_pending(request_id)
            raise

    def finalize(self, request_id: str, order_id: str) -> Path:
        pending = self._pending_dir(request_id)
        final = self._final_dir(order_id)
        if not pending.is_dir():
            raise ValidationError("snapshot pending directory does not exist")
        if final.exists():
            raise ValidationError("snapshot final directory already exists")
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(pending, final)
        except OSError as exc:
            raise self._translate_os_error(exc)
        return final

    def cleanup_pending(self, request_id: str) -> bool:
        try:
            path = self._pending_dir(request_id)
            if path.exists():
                shutil.rmtree(path)
            return True
        except OSError:
            return False

    def cleanup_final(self, order_id: str) -> bool:
        try:
            path = self._final_dir(order_id)
            if path.exists():
                shutil.rmtree(path)
            return True
        except OSError:
            return False

    def cleanup_all_history_images(self) -> tuple[int, int]:
        """Best-effort removal of all managed final snapshot directories."""
        removed = 0
        failed = 0
        root = self.paths.snapshot_images
        if not root.exists():
            return 0, 0
        for child in tuple(root.iterdir()):
            if not child.is_dir() or child.is_symlink():
                continue
            try:
                validate_uuid_v4(child.name)
            except Exception:
                continue
            try:
                shutil.rmtree(child)
                removed += 1
            except OSError:
                failed += 1
        return removed, failed

    def relative_snapshot_path(self, order_id: str, filename: str) -> str:
        final = self._final_dir(order_id)
        candidate = (final / filename).resolve()
        images_root = self.paths.snapshot_images.resolve()
        if images_root not in candidate.parents:
            raise ValidationError("snapshot file escapes managed image root")
        return candidate.relative_to(self.paths.user_data.resolve()).as_posix()

    def resolve_history_path(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise ValidationError("snapshot path must be a managed relative path")
        normalized = relative_path.replace("\\", "/")
        parts = normalized.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValidationError("snapshot path contains unsafe segment")
        if len(parts) != 4 or parts[0] != "snapshots" or parts[1] != "images":
            raise ValidationError("snapshot path is outside managed final image area")
        validate_uuid_v4(parts[2])
        root = self.paths.snapshot_images.resolve()
        candidate = (self.paths.user_data / Path(*parts)).resolve()
        if root not in candidate.parents:
            raise ValidationError("snapshot path escapes managed root")
        return candidate

    def validate_history_image(self, relative_path: str | None, expected_sha256: str | None) -> tuple[bool, Path | None]:
        if relative_path is None or expected_sha256 is None:
            return False, None
        try:
            path = self.resolve_history_path(relative_path)
            if not path.is_file() or path.is_symlink():
                return False, None
            self.image_validator.validate(path)
            actual = self._hash_file(path)
            if actual != expected_sha256:
                return False, None
            return True, path
        except Exception:
            return False, None
