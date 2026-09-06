from __future__ import annotations

import sys
from pathlib import Path


class ResourceLocator:
    def __init__(self, base: Path | None = None) -> None:
        if base is not None:
            self._base = Path(base).resolve()
        elif getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            self._base = Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
        else:
            self._base = Path(__file__).resolve().parents[2]

    @property
    def base(self) -> Path:
        return self._base

    def resolve(self, relative: str | Path) -> Path:
        candidate = (self._base / relative).resolve()
        if candidate != self._base and self._base not in candidate.parents:
            raise ValueError("resource path escapes the application resource root")
        return candidate
