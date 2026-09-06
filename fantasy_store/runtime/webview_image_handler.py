from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import warnings
from typing import Any
from urllib.parse import unquote_to_bytes, urlsplit

from PIL import Image, UnidentifiedImageError

from fantasy_store.bridge.image_resolver import LocalImageResolver
from fantasy_store.config import VPACK_MAX_IMAGE_PIXELS
from fantasy_store.domain.errors import ValidationError

_SCHEME = "fantasy-image"
_HOST = "resource"
_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_FORMAT_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass(frozen=True, slots=True)
class ImageResourceResponse:
    status: int
    reason: str
    mime_type: str
    body: bytes


def strict_opaque_from_url(url: str) -> str:
    """Decode the opaque component exactly once with strict percent/UTF-8 handling."""
    if not isinstance(url, str):
        raise ValidationError("image URL must be text")
    parts = urlsplit(url)
    if parts.scheme != _SCHEME or parts.netloc != _HOST or parts.query or parts.fragment:
        raise ValidationError("unsupported local image URL")
    if not parts.path.startswith("/") or parts.path == "/" or "/" in parts.path[1:]:
        raise ValidationError("local image URL is malformed")
    encoded = parts.path[1:]
    if _PERCENT_RE.search(encoded):
        raise ValidationError("malformed percent escape")
    try:
        raw = unquote_to_bytes(encoded)
        opaque = raw.decode("utf-8", "strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("local image URL has invalid UTF-8 encoding") from exc
    if not opaque or "\x00" in opaque:
        raise ValidationError("local image reference is malformed")
    # Deliberately do not percent-decode `opaque` again. A double-encoded traversal
    # therefore remains literal `%2e...` data and is rejected/not-found downstream.
    return opaque


class ImageResourceLoader:
    """Resolve, fully read, close, revalidate, then return managed image bytes."""

    def __init__(self, resolver: LocalImageResolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _validate_bytes(data: bytes, expected_mime: str) -> str:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    fmt = image.format
                    width, height = image.size
                    if width * height > VPACK_MAX_IMAGE_PIXELS:
                        raise ValidationError("image exceeds configured pixel limit")
                    image.verify()
                with Image.open(BytesIO(data)) as image2:
                    fmt2 = image2.format
                    image2.load()
            if fmt is None or fmt2 != fmt:
                raise ValidationError("image format changed across verification")
            mime = _FORMAT_MIME.get(fmt)
            if mime is None or mime != expected_mime:
                raise ValidationError("image MIME does not match validated format")
            return mime
        except ValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValidationError("image resource validation failed") from exc

    def load_url(self, url: str) -> ImageResourceResponse:
        try:
            opaque = strict_opaque_from_url(url)
            resolved = self.resolver.resolve(opaque)
            # read_bytes opens and closes the OS file before a WebView response exists,
            # preventing WebView2 from owning a Pack/snapshot file handle.
            data = resolved.path.read_bytes()
            mime = self._validate_bytes(data, resolved.mime_type)
            return ImageResourceResponse(200, "OK", mime, data)
        except Exception:
            return ImageResourceResponse(404, "Not Found", "text/plain; charset=utf-8", b"")


class WindowsWebView2ImageHandler:
    """Attach a `fantasy-image://` bytes handler to pywebview's EdgeChromium control.

    The handler is Windows-only at runtime, but its URL/bytes logic is platform-neutral
    and is fully unit-testable without pythonnet/WebView2.
    """

    def __init__(self, loader: ImageResourceLoader, *, logger=None) -> None:
        self.loader = loader
        self.logger = logger
        self._attached = False
        self._core = None

    def attach(self, native_webview: Any) -> None:
        if self._attached:
            return
        self._attached = True
        core = getattr(native_webview, "CoreWebView2", None)
        if core is not None:
            self._install_core(core)
            return
        native_webview.CoreWebView2InitializationCompleted += self._on_initialized

    def _on_initialized(self, sender: Any, args: Any) -> None:
        try:
            if hasattr(args, "IsSuccess") and not bool(args.IsSuccess):
                if self.logger:
                    self.logger.error("WebView2 initialization failed before image handler attach", extra={"event_code": "WEBVIEW_IMAGE_INIT_FAILED"})
                return
            core = getattr(sender, "CoreWebView2", None)
            if core is not None:
                self._install_core(core)
        except Exception:
            if self.logger:
                self.logger.exception("failed to attach WebView2 image handler", extra={"event_code": "WEBVIEW_IMAGE_ATTACH_FAILED"})

    def _install_core(self, core: Any) -> None:
        if self._core is core:
            return
        # Import only after EdgeChromium has initialized its WebView2 assemblies.
        from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext  # type: ignore[import-not-found]

        core.AddWebResourceRequestedFilter("fantasy-image://*", CoreWebView2WebResourceContext.Image)
        core.WebResourceRequested += self._on_request
        self._core = core

    def _on_request(self, sender: Any, args: Any) -> None:
        uri = str(args.Request.Uri)
        if not uri.lower().startswith("fantasy-image://"):
            return
        response = self.loader.load_url(uri)
        try:
            from System import Array, Byte  # type: ignore[import-not-found]
            from System.IO import MemoryStream  # type: ignore[import-not-found]

            managed = Array[Byte](response.body)
            stream = MemoryStream(managed, False)
            headers = (
                f"Content-Type: {response.mime_type}\r\n"
                "Cache-Control: no-store\r\n"
                "X-Content-Type-Options: nosniff\r\n"
            )
            args.Response = sender.Environment.CreateWebResourceResponse(
                stream,
                response.status,
                response.reason,
                headers,
            )
        except Exception:
            if self.logger:
                self.logger.exception("failed to serve WebView2 image resource", extra={"event_code": "WEBVIEW_IMAGE_RESPONSE_FAILED"})
