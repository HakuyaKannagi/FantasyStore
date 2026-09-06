from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from PIL import Image


def image_bytes(fmt: str, size=(8, 8)) -> bytes:
    bio = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(bio, format=fmt)
    return bio.getvalue()


def base_pack(pack_id="demo.pack"):
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "name": "Demo Pack",
        "version": "1.0",
        "author": "Tester",
        "description": "demo",
    }


def base_items(images=None, *, item_id="item-1", price=None, attrs=None):
    images = images or ["assets/a.png"]
    return {
        "schema_version": 1,
        "items": [{
            "item_id": item_id,
            "name": "Item One",
            "price": price or {"significand": "123", "exponent": "2"},
            "category": "Category",
            "description": "Description",
            "attributes": attrs if attrs is not None else {"color": "blue", "rank": 2, "flag": True},
            "images": images,
        }],
    }


def write_vpack(path: Path, *, pack=None, items=None, assets=None, bom_pack=False, bom_items=False, order=None):
    pack = pack or base_pack()
    items = items or base_items()
    assets = assets or {"assets/a.png": image_bytes("PNG")}
    pack_b = json.dumps(pack, ensure_ascii=False, separators=(",", ":")).encode()
    items_b = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode()
    if bom_pack: pack_b = b"\xef\xbb\xbf" + pack_b
    if bom_items: items_b = b"\xef\xbb\xbf" + items_b
    members = {"pack.json": pack_b, "items.json": items_b, **assets}
    names = order or list(members)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, members[name])
    return path
