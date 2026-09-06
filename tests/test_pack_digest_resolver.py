from __future__ import annotations

from pathlib import Path
import pytest

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.asset_resolver import AssetResolver
from fantasy_store.pack.digest import compute_content_digest
from fantasy_store.pack.path_resolver import PackPathResolver
from fantasy_store.runtime.paths import AppPaths
from tests.phase3_helpers import image_bytes


def make_root(root: Path):
    (root/"assets").mkdir(parents=True)
    (root/"pack.json").write_text("{}")
    (root/"items.json").write_text("{}")
    (root/"assets/a.png").write_bytes(image_bytes("PNG"))
    return root


def test_digest_stable_and_changes(tmp_path):
    r=make_root(tmp_path/"r")
    d1=compute_content_digest(r); d2=compute_content_digest(r)
    assert d1==d2 and len(d1)==64 and d1==d1.lower()
    int(d1, 16)
    (r/"assets/a.png").write_bytes((r/"assets/a.png").read_bytes()+b"x")
    assert compute_content_digest(r)!=d1


def test_digest_path_change(tmp_path):
    r=make_root(tmp_path/"r"); d1=compute_content_digest(r)
    (r/"assets/a.png").rename(r/"assets/b.png")
    assert compute_content_digest(r)!=d1


def test_digest_nfc_path_normalization(tmp_path):
    r1=make_root(tmp_path/"r1"); (r1/"assets/a.png").rename(r1/"assets/é.png")
    r2=make_root(tmp_path/"r2"); (r2/"assets/a.png").rename(r2/"assets/e\u0301.png")
    assert compute_content_digest(r1)==compute_content_digest(r2)


def test_asset_resolver_valid_and_escape(tmp_path):
    paths=AppPaths(tmp_path/"data"); paths.create_application_dirs()
    pack=(paths.installed_packs/"demo.pack"); (pack/"assets").mkdir(parents=True)
    (pack/"assets/a.png").write_bytes(image_bytes("PNG"))
    r=AssetResolver(PackPathResolver(paths))
    assert r.resolve("demo.pack", "assets/a.png").is_file()
    for bad in ("../x.png", "/x.png", "assets/../../other.pack/assets/a.png"):
        with pytest.raises(PackValidationError): r.resolve("demo.pack", bad)
    with pytest.raises(PackValidationError): r.resolve("other.pack", "assets/a.png")
    with pytest.raises(PackValidationError):
        r.paths.asset_path(tmp_path/"outside-pack", "assets/a.png")
