from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from fantasy_store.domain.errors import PackOperationError
from fantasy_store.persistence.pack_repository import InstalledPack, PackRepository
from .journal import utc_text


@dataclass(frozen=True, slots=True)
class ManifestStateEntry:
    pack_id: str
    is_enabled: bool
    installed_at: str
    updated_at: str
    content_digest: str


class PackManifestStateStore:
    def __init__(self, path: Path, *, now=None) -> None:
        from datetime import datetime, timezone
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def write_repository_state(self, repository: PackRepository) -> None:
        self.write(repository.list_installed_packs())

    def write(self, packs: list[InstalledPack]) -> None:
        payload = {
            "format_version": 1,
            "generated_at": utc_text(self._now()),
            "packs": [
                {
                    "pack_id": p.pack_id,
                    "is_enabled": bool(p.is_enabled),
                    "installed_at": p.installed_at,
                    "updated_at": p.updated_at,
                    "content_digest": p.content_digest,
                }
                for p in sorted(packs, key=lambda x: x.pack_id)
            ],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as fp:
                json.dump(payload, fp, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            raise PackOperationError(
                f"pack manifest state backup write failed: {exc}",
                code="BACKUP_WRITE_FAILED",
            ) from exc

    def load(self) -> dict[str, ManifestStateEntry]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackOperationError(f"manifest state backup cannot be read: {exc}", code="BACKUP_STATE_INVALID") from exc
        if not isinstance(data, dict) or set(data) != {"format_version", "generated_at", "packs"}:
            raise PackOperationError("manifest state backup shape is invalid", code="BACKUP_STATE_INVALID")
        if data["format_version"] != 1:
            raise PackOperationError("manifest state backup version is unsupported", code="BACKUP_STATE_INVALID")
        if not isinstance(data["generated_at"], str) or not isinstance(data["packs"], list):
            raise PackOperationError("manifest state backup fields are invalid", code="BACKUP_STATE_INVALID")
        result: dict[str, ManifestStateEntry] = {}
        for raw in data["packs"]:
            if not isinstance(raw, dict) or set(raw) != {
                "pack_id", "is_enabled", "installed_at", "updated_at", "content_digest"
            }:
                raise PackOperationError("manifest state pack entry is invalid", code="BACKUP_STATE_INVALID")
            if not isinstance(raw["pack_id"], str) or raw["pack_id"] in result:
                raise PackOperationError("manifest state pack_id is invalid/duplicate", code="BACKUP_STATE_INVALID")
            if not isinstance(raw["is_enabled"], bool):
                raise PackOperationError("manifest state is_enabled is invalid", code="BACKUP_STATE_INVALID")
            digest = raw["content_digest"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise PackOperationError("manifest state content_digest is invalid", code="BACKUP_STATE_INVALID")
            if not isinstance(raw["installed_at"], str) or not isinstance(raw["updated_at"], str):
                raise PackOperationError("manifest state timestamps are invalid", code="BACKUP_STATE_INVALID")
            result[raw["pack_id"]] = ManifestStateEntry(
                pack_id=raw["pack_id"],
                is_enabled=raw["is_enabled"],
                installed_at=raw["installed_at"],
                updated_at=raw["updated_at"],
                content_digest=digest,
            )
        return result
