from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable


Sleep = Callable[[float], None]


def replace_with_retry(
    src: Path,
    dst: Path,
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
    sleep: Sleep = time.sleep,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> None:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    last: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            return
        except OSError as exc:
            last = exc
            if attempt >= attempts:
                break
            if on_retry is not None:
                on_retry(attempt, exc)
            sleep(base_delay * (2 ** (attempt - 1)))
    assert last is not None
    raise last


def remove_tree_best_effort(path: Path, *, on_error: Callable[[OSError], None] | None = None) -> bool:
    if not path.exists():
        return True
    try:
        shutil.rmtree(path)
        return True
    except OSError as exc:
        if on_error is not None:
            on_error(exc)
        return False
