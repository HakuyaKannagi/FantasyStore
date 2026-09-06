from __future__ import annotations

import io
from pathlib import Path
import pytest
from PIL import Image

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.image_reference_validator import ImageReferenceValidator
from fantasy_store.pack.image_validator import ImageValidator
from fantasy_store.pack.path_resolver import normalize_image_reference
from tests.phase3_helpers import image_bytes


def write(path: Path, data: bytes): path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path

@pytest.mark.parametrize("fmt,ext", [("PNG",".png"),("JPEG",".jpg"),("WEBP",".webp")])
def test_valid_images(tmp_path, fmt, ext):
    p=write(tmp_path/("a"+ext), image_bytes(fmt))
    assert ImageValidator().validate(p).format == fmt


def test_extension_disguise_and_corrupt(tmp_path):
    p=write(tmp_path/"a.png", image_bytes("JPEG"))
    with pytest.raises(PackValidationError): ImageValidator().validate(p)
    q=write(tmp_path/"b.png", b"not-image")
    with pytest.raises(PackValidationError): ImageValidator().validate(q)


def test_truncated_image(tmp_path):
    data=image_bytes("PNG")
    p=write(tmp_path/"a.png", data[:len(data)//2])
    with pytest.raises(PackValidationError): ImageValidator().validate(p)


def test_40mp_limit(tmp_path):
    p=tmp_path/"large.png"
    Image.new("1", (6400, 6400)).save(p, format="PNG")
    with pytest.raises(PackValidationError, match="40 MP"): ImageValidator().validate(p)


def test_pillow_bomb_protection_is_error(monkeypatch, tmp_path):
    p=write(tmp_path/"a.png", image_bytes("PNG", (20,20)))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(PackValidationError): ImageValidator().validate(p)

@pytest.mark.parametrize("ref", ["../a.png", "/a.png", "C:/a.png", r"\\x\a.png", "assets/../a.png", "other/a.png", "assets/CON.png"])
def test_bad_image_reference(ref):
    with pytest.raises(PackValidationError): normalize_image_reference(ref)


def test_reference_missing_and_nfc_collision(tmp_path):
    root=tmp_path/"pack"; write(root/"assets/a.png", image_bytes("PNG"))
    v=ImageReferenceValidator(root, {"assets/a.png"}, {"assets/a.png"})
    assert v.validate_one("assets/a.png") == "assets/a.png"
    with pytest.raises(PackValidationError): v.validate_one("assets/missing.png")
    # Direct boundary test: normalized equivalent references collide independently of Schema.
    write(root/"assets/é.png", image_bytes("PNG"))
    v2=ImageReferenceValidator(root, {"assets/é.png"}, {"assets/é.png"})
    with pytest.raises(PackValidationError): v2.validate_many(["assets/e\u0301.png", "assets/é.png"])
