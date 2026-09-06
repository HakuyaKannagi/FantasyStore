from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path

from fantasy_store.domain.errors import PackValidationError


def compute_content_digest(pack_root: Path) -> str:
    root = Path(pack_root).resolve()
    files: list[tuple[str, Path]] = []
    for fixed in ("pack.json", "items.json"):
        path = root / fixed
        if not path.is_file() or path.is_symlink():
            raise PackValidationError(f"digest source missing: {fixed}")
        files.append((fixed, path))
    assets = root / "assets"
    if not assets.is_dir():
        raise PackValidationError("digest assets directory missing")
    for path in assets.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(root).as_posix()
            files.append((unicodedata.normalize("NFC", rel), path))

    files.sort(key=lambda pair: pair[0])
    overall = hashlib.sha256()
    for rel, path in files:
        file_hash = hashlib.sha256()
        try:
            with path.open("rb") as fp:
                for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                    file_hash.update(chunk)
        except OSError as exc:
            raise PackValidationError(f"digest file read failed: {exc}") from exc
        overall.update(unicodedata.normalize("NFC", rel.replace("\\", "/")).encode("utf-8"))
        overall.update(b"\x00")
        overall.update(file_hash.digest())
    return overall.hexdigest()
