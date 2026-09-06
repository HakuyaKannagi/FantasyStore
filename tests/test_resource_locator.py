from pathlib import Path
import pytest

from fantasy_store.runtime.resource_locator import ResourceLocator


def test_resource_locator_rejects_escape(tmp_path: Path):
    locator = ResourceLocator(tmp_path)
    assert locator.resolve("ui/index.html") == (tmp_path / "ui/index.html").resolve()
    with pytest.raises(ValueError):
        locator.resolve("../outside")
