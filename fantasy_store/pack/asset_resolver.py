from __future__ import annotations

from pathlib import Path

from fantasy_store.domain.errors import PackValidationError
from .path_resolver import PackPathResolver


class AssetResolver:
    """Runtime-safe resolver; revalidates DB/caller-derived image references."""

    def __init__(self, paths: PackPathResolver) -> None:
        self.paths = paths

    def resolve(self, pack_id: str, image_reference: str) -> Path:
        pack_root = self.paths.installed_pack_root(pack_id)
        candidate = self.paths.asset_path(pack_root, image_reference)
        if not candidate.is_file() or candidate.is_symlink():
            raise PackValidationError("resolved asset is not a regular existing file")
        return candidate
