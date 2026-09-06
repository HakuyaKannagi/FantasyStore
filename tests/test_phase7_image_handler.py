from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from PIL import Image

from fantasy_store.bridge.image_resolver import ResolvedImage
from fantasy_store.runtime.webview_image_handler import ImageResourceLoader, strict_opaque_from_url


class Resolver:
    def __init__(self, path: Path):
        self.path = path
        self.seen = None
    def resolve(self, opaque: str):
        self.seen = opaque
        return ResolvedImage(self.path, "image/png")


def make_png(path: Path):
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path, format="PNG")


def test_strict_image_url_decodes_once_and_rejects_malformed():
    ref = "pack-asset:sample.pack:assets/a b.png"
    url = "fantasy-image://resource/" + quote(ref, safe="")
    assert strict_opaque_from_url(url) == ref
    # Double-encoded traversal remains literal percent text after exactly one decode.
    double = "fantasy-image://resource/%252e%252e%252fsecret"
    assert strict_opaque_from_url(double) == "%2e%2e%2fsecret"
    for bad in [
        "file:///tmp/x",
        "https://example.test/x",
        "fantasy-image://wrong/x",
        "fantasy-image://resource/%ZZ",
        "fantasy-image://resource/",
        "fantasy-image://resource/a/b",
    ]:
        try:
            strict_opaque_from_url(bad)
        except Exception:
            pass
        else:
            raise AssertionError(f"accepted unsafe image URL: {bad}")


def test_image_loader_reads_validates_and_returns_bytes(tmp_path: Path):
    image = tmp_path / "image.png"
    make_png(image)
    resolver = Resolver(image)
    loader = ImageResourceLoader(resolver)
    ref = "pack-asset:sample.pack:assets/image.png"
    response = loader.load_url("fantasy-image://resource/" + quote(ref, safe=""))
    assert response.status == 200
    assert response.mime_type == "image/png"
    assert response.body.startswith(b"\x89PNG")
    assert resolver.seen == ref


def test_image_loader_safe_404_for_missing_or_corrupt(tmp_path: Path):
    missing = Resolver(tmp_path / "missing.png")
    response = ImageResourceLoader(missing).load_url("fantasy-image://resource/abc")
    assert response.status == 404
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not image")
    response2 = ImageResourceLoader(Resolver(corrupt)).load_url("fantasy-image://resource/abc")
    assert response2.status == 404
    assert response2.body == b""


def test_image_loader_does_not_hold_source_file_handle_after_response(tmp_path: Path):
    image = tmp_path / "image.png"
    make_png(image)
    loader = ImageResourceLoader(Resolver(image))
    response = loader.load_url("fantasy-image://resource/abc")
    assert response.status == 200
    renamed = tmp_path / "renamed.png"
    image.rename(renamed)
    assert renamed.is_file()
