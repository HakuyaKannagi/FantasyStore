from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from fantasy_store.domain.errors import ValidationError
from fantasy_store.domain.ids import validate_pack_id, validate_uuid_v4
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.persistence.user_repository import UserRepository
from fantasy_store.snapshot.manager import SnapshotManager

_UI_SCHEME = "fantasy-image"
_UI_HOST = "resource"


@dataclass(frozen=True, slots=True)
class ResolvedImage:
    path: Path
    mime_type: str


class LocalImageResolver:
    """Revalidates opaque image references without exposing OS paths to JS."""

    def __init__(self, assets: AssetResolver, snapshots: SnapshotManager, users: UserRepository) -> None:
        self.assets = assets
        self.snapshots = snapshots
        self.users = users

    @staticmethod
    def ui_url(opaque_ref: str) -> str:
        if not isinstance(opaque_ref, str) or not opaque_ref:
            raise ValidationError("image reference must be a non-empty string")
        # Ref stays opaque. It is percent-encoded, not interpreted as a file path.
        return f"{_UI_SCHEME}://{_UI_HOST}/{quote(opaque_ref, safe='')}"

    @staticmethod
    def opaque_from_ui_url(url: str) -> str:
        if not isinstance(url, str):
            raise ValidationError("image URL must be text")
        parts = urlsplit(url)
        if parts.scheme != _UI_SCHEME or parts.netloc != _UI_HOST or parts.query or parts.fragment:
            raise ValidationError("unsupported local image URL")
        if not parts.path.startswith("/") or parts.path == "/":
            raise ValidationError("local image URL is malformed")
        return unquote(parts.path[1:])

    @staticmethod
    def _mime(path: Path) -> str:
        suffix = path.suffix.lower()
        mapping = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        try:
            return mapping[suffix]
        except KeyError as exc:
            raise ValidationError("unsupported image format") from exc

    def resolve(self, opaque_ref: str) -> ResolvedImage:
        if not isinstance(opaque_ref, str):
            raise ValidationError("image reference must be text")
        if opaque_ref.startswith("pack-asset:"):
            rest = opaque_ref[len("pack-asset:"):]
            if ":" not in rest:
                raise ValidationError("malformed pack image reference")
            pack_id, image_reference = rest.split(":", 1)
            validate_pack_id(pack_id)
            path = self.assets.resolve(pack_id, image_reference)
            return ResolvedImage(path=path, mime_type=self._mime(path))
        if opaque_ref.startswith("snapshot:"):
            rest = opaque_ref[len("snapshot:"):]
            if ":" not in rest:
                raise ValidationError("malformed snapshot image reference")
            order_id, filename = rest.split(":", 1)
            validate_uuid_v4(order_id)
            if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
                raise ValidationError("unsafe snapshot filename")
            detail = self.users.get_order_detail(order_id)
            if detail is None:
                raise ValidationError("snapshot order does not exist")
            matched = None
            for item in detail.items:
                rel = item.primary_image_snapshot_path
                if rel and Path(rel).name == filename:
                    matched = item
                    break
            if matched is None:
                raise ValidationError("snapshot reference is not owned by the order")
            ok, path = self.snapshots.validate_history_image(
                matched.primary_image_snapshot_path,
                matched.primary_image_snapshot_sha256,
            )
            if not ok or path is None:
                raise ValidationError("snapshot image is unavailable")
            return ResolvedImage(path=path, mime_type=self._mime(path))
        raise ValidationError("unknown image reference scheme")

    def resolve_ui_url(self, url: str) -> ResolvedImage:
        return self.resolve(self.opaque_from_ui_url(url))
