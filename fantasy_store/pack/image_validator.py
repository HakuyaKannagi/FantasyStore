from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from fantasy_store.config import VPACK_MAX_IMAGE_PIXELS
from fantasy_store.domain.errors import PackValidationError

_EXT_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    path: Path
    format: str
    width: int
    height: int


class ImageValidator:
    def validate(self, path: Path) -> ValidatedImage:
        path = Path(path)
        expected = _EXT_FORMATS.get(path.suffix.casefold())
        if expected is None:
            raise PackValidationError("image extension is not allowed")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    actual = image.format
                    width, height = image.size
                    if width * height > VPACK_MAX_IMAGE_PIXELS:
                        raise PackValidationError("image exceeds 40 MP pixel limit")
                    if actual != expected:
                        raise PackValidationError("image format does not match file extension")
                    image.verify()
                with Image.open(path) as image2:
                    width2, height2 = image2.size
                    actual2 = image2.format
                    image2.load()
                if (width2, height2, actual2) != (width, height, actual):
                    raise PackValidationError("image metadata changed across verification reopen")
        except PackValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise PackValidationError(f"image decode/verification failed: {exc}") from exc
        return ValidatedImage(path, expected, width, height)
