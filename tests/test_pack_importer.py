from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
import pytest

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.importer import ImportKind, PackImporter
from fantasy_store.runtime.paths import AppPaths
from tests.phase3_helpers import base_items, base_pack, image_bytes, write_vpack


def setup_paths(tmp_path):
    p=AppPaths(tmp_path/"data"); p.create_application_dirs(); return p


def test_normal_pack_png_jpeg_webp_bom_multiple(tmp_path):
    paths=setup_paths(tmp_path)
    assets={"assets/a.png":image_bytes("PNG"),"assets/b.jpg":image_bytes("JPEG"),"assets/c.webp":image_bytes("WEBP")}
    items=base_items(["assets/a.png","assets/b.jpg"])
    second=dict(items["items"][0]); second["item_id"]="item-2"; second["images"]=["assets/c.webp"]
    items["items"].append(second)
    src=write_vpack(tmp_path/"ok.vpack",items=items,assets=assets,bom_pack=True,bom_items=True)
    result=PackImporter(paths).prepare(src)
    assert result.import_kind==ImportKind.NEW
    assert len(result.prepared_items_master)==2 and result.staging_path.is_dir()
    assert len(result.content_digest)==64
    assert not any(paths.installed_packs.iterdir())


def test_existing_pack_only_classifies_no_switch(tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"ok.vpack")
    r=PackImporter(paths).prepare(src,existing_pack_ids={"demo.pack"})
    assert r.import_kind==ImportKind.EXISTING_PACK and not (paths.installed_packs/"demo.pack").exists()


def test_zip_order_does_not_change_digest(tmp_path):
    p1=setup_paths(tmp_path/"a"); p2=setup_paths(tmp_path/"b")
    members=["pack.json","items.json","assets/a.png"]
    a=write_vpack(tmp_path/"a.vpack",order=members)
    b=write_vpack(tmp_path/"b.vpack",order=list(reversed(members)))
    assert PackImporter(p1).prepare(a).content_digest==PackImporter(p2).prepare(b).content_digest



def test_backslash_member_normalizes_to_same_digest(tmp_path):
    p1=setup_paths(tmp_path/"a"); p2=setup_paths(tmp_path/"b")
    normal=write_vpack(tmp_path/"normal.vpack")
    pack=base_pack(); items=base_items(["assets/a.png"])
    with zipfile.ZipFile(tmp_path/"backslash.vpack", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pack.json", json.dumps(pack,separators=(",",":")))
        zf.writestr("items.json", json.dumps(items,separators=(",",":")))
        zf.writestr(r"assets\a.png", image_bytes("PNG"))
    # A single separator style is accepted and normalized; mixed styles are rejected separately.
    assert PackImporter(p1).prepare(normal).content_digest == PackImporter(p2).prepare(tmp_path/"backslash.vpack").content_digest


def test_missing_reference_failure_cleans_staging_and_preserves_state(tmp_path):
    paths=setup_paths(tmp_path)
    sentinel=(paths.installed_packs/"keep"); sentinel.mkdir(); (sentinel/"x").write_text("keep")
    user=(paths.user_data/"sentinel"); user.write_text("keep")
    paths.pack_db.write_bytes(b"pack-db-sentinel")
    items=base_items(["assets/missing.png"])
    src=write_vpack(tmp_path/"bad.vpack",items=items)
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert (sentinel/"x").read_text()=="keep" and user.read_text()=="keep"
    assert paths.pack_db.read_bytes()==b"pack-db-sentinel"
    assert list(paths.staging_packs.iterdir())==[]


def test_staging_creation_failure(monkeypatch,tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack")
    import fantasy_store.pack.zip_safety as zs
    original=Path.mkdir
    def fail(self,*a,**k):
        if self.parent==paths.staging_packs: raise OSError("boom")
        return original(self,*a,**k)
    monkeypatch.setattr(Path,"mkdir",fail)
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir()) == []
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()


def test_extraction_write_failure(monkeypatch,tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack")
    import fantasy_store.pack.zip_safety as zs
    monkeypatch.setattr(zs,"_copy_stream",lambda *a,**k: (_ for _ in ()).throw(OSError("write")))
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir())==[]


def test_stream_read_failure(monkeypatch,tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack")
    import fantasy_store.pack.zip_safety as zs
    class BadSrc:
        def read(self,*a): raise OSError("read")
    monkeypatch.setattr(zs,"_copy_stream",lambda *a,**k: (_ for _ in ()).throw(OSError("read")))
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir()) == []
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()


def test_json_read_failure(monkeypatch,tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack")
    import fantasy_store.pack.importer as imp
    monkeypatch.setattr(imp,"load_strict_json",lambda p: (_ for _ in ()).throw(PackValidationError("json read")))
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir()) == []
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()


def test_image_decode_failure(tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack",assets={"assets/a.png":b"bad"})
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir()) == []
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()


def test_digest_read_failure(monkeypatch,tmp_path):
    paths=setup_paths(tmp_path); src=write_vpack(tmp_path/"x.vpack")
    import fantasy_store.pack.importer as imp
    monkeypatch.setattr(imp,"compute_content_digest",lambda p: (_ for _ in ()).throw(PackValidationError("digest read")))
    with pytest.raises(PackValidationError): PackImporter(paths).prepare(src)
    assert list(paths.staging_packs.iterdir()) == []
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()


def test_cleanup_failure_logged_not_success(monkeypatch,tmp_path):
    from unittest.mock import Mock
    paths=setup_paths(tmp_path); items=base_items(["assets/missing.png"]); src=write_vpack(tmp_path/"x.vpack",items=items)
    import fantasy_store.pack.importer as imp
    monkeypatch.setattr(imp.shutil,"rmtree",lambda p: (_ for _ in ()).throw(OSError("cleanup")))
    logger=Mock()
    with pytest.raises(PackValidationError): PackImporter(paths, logger=logger).prepare(src)
    assert logger.error.called
    assert any(paths.staging_packs.iterdir())  # cleanup failure is visible, never success
    assert list(paths.installed_packs.iterdir()) == []
    assert not paths.pack_db.exists() and not paths.user_db.exists()
