from __future__ import annotations

from pathlib import Path
from typing import Protocol


class FilePicker(Protocol):
    def pick_vpack(self) -> Path | None: ...


class DeferredFilePicker:
    """Phase 6 boundary only; Phase 7 wires this to pywebview native dialog."""

    def pick_vpack(self) -> Path | None:
        raise RuntimeError("native file picker is deferred to Phase 7")


class DeterministicFilePicker:
    """Deterministic adapter for Bridge/UI tests."""

    def __init__(self, selection: Path | str | None = None, *, error: Exception | None = None) -> None:
        self.selection = None if selection is None else Path(selection)
        self.error = error
        self.calls = 0

    def pick_vpack(self) -> Path | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.selection
