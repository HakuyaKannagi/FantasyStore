from __future__ import annotations

import stat
import zipfile
from pathlib import Path
import pytest

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.zip_safety import normalize_archive_path, validate_entry_sizes, validate_zip_metadata, validate_source_size

@pytest.mark.parametrize("name", [
    "../evil", "/absolute", r"C:\evil", "C:/evil", r"\\server\share\evil", "./x", "a/../x",
    "a//x", r"assets/a\../b.png", r"assets\sub/a.png", "assets/a\x01.png", "assets/x ", "assets/x.",
])
def test_zip_slip_and_bad_paths(name):
    with pytest.raises(PackValidationError): normalize_archive_path(name)

@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL.png", "COM1.webp", "LPT9.jpg", "assets/con.txt"])
def test_reserved_windows_names(name):
    with pytest.raises(PackValidationError): normalize_archive_path(name)


def info(name, size=1, compressed=1, mode=0):
    z=zipfile.ZipInfo(name); z.file_size=size; z.compress_size=compressed; z.external_attr=mode << 16; return z


def valid_infos():
    return [info("pack.json",10,10), info("items.json",10,10), info("assets/a.png",10,10)]


def test_duplicate_member():
    with pytest.raises(PackValidationError): validate_zip_metadata(valid_infos()+[info("assets/a.png")])


def test_nfc_collision():
    xs=valid_infos(); xs[-1]=info("assets/e\u0301.png"); xs.append(info("assets/é.png"))
    with pytest.raises(PackValidationError): validate_zip_metadata(xs)


def test_case_insensitive_collision():
    xs=valid_infos()+[info("assets/A.PNG")]
    with pytest.raises(PackValidationError): validate_zip_metadata(xs)


def test_symlink_and_device():
    for mode in (stat.S_IFLNK|0o777, stat.S_IFCHR|0o600, stat.S_IFBLK|0o600, stat.S_IFIFO|0o600, stat.S_IFSOCK|0o600):
        xs=valid_infos(); xs[-1]=info("assets/a.png", mode=mode)
        with pytest.raises(PackValidationError): validate_zip_metadata(xs)


def test_windows_device_and_reparse_metadata():
    for attr in (0x0040, 0x0400):
        z=info("assets/a.png"); z.create_system=0; z.external_attr=attr
        xs=valid_infos(); xs[-1]=z
        with pytest.raises(PackValidationError): validate_zip_metadata(xs)


def test_root_extra_and_bad_extension():
    for extra in (info("evil.txt"), info("assets/a.svg")):
        with pytest.raises(PackValidationError): validate_zip_metadata(valid_infos()+[extra])


def test_missing_assets_and_required_roots():
    with pytest.raises(PackValidationError): validate_zip_metadata([info("pack.json"), info("items.json")])
    with pytest.raises(PackValidationError): validate_zip_metadata([info("pack.json"), info("assets/a.png")])


def test_size_boundaries_metadata(monkeypatch):
    import fantasy_store.pack.zip_safety as zs
    z=info("assets/a.png", zs.VPACK_MAX_SINGLE_FILE_BYTES+1, 1024)
    with pytest.raises(PackValidationError): validate_entry_sizes(z)
    z=info("pack.json", zs.VPACK_MAX_PACK_JSON_BYTES+1, 1024)
    with pytest.raises(PackValidationError): validate_entry_sizes(z)
    z=info("items.json", zs.VPACK_MAX_ITEMS_JSON_BYTES+1, 1024)
    with pytest.raises(PackValidationError): validate_entry_sizes(z)
    z=info("assets/a.png", zs.VPACK_COMPRESSION_RATIO_MIN_SIZE, 1)
    with pytest.raises(PackValidationError): validate_entry_sizes(z)
    z=info("assets/a.png", 1, 0)
    with pytest.raises(PackValidationError): validate_entry_sizes(z)


def test_total_and_file_count_without_large_files(monkeypatch):
    import fantasy_store.pack.zip_safety as zs
    monkeypatch.setattr(zs, "VPACK_MAX_EXPANDED_BYTES", 25)
    with pytest.raises(PackValidationError): validate_zip_metadata(valid_infos())
    monkeypatch.setattr(zs, "VPACK_MAX_EXPANDED_BYTES", 10**9)
    monkeypatch.setattr(zs, "VPACK_MAX_FILE_COUNT", 2)
    with pytest.raises(PackValidationError): validate_zip_metadata(valid_infos())



def test_exact_file_count_and_expanded_total_limits():
    import fantasy_store.pack.zip_safety as zs
    # 2 required roots + 4,999 assets = 5,001 files -> reject, metadata only.
    many=[info("pack.json"), info("items.json")] + [info(f"assets/{i}.png") for i in range(4999)]
    with pytest.raises(PackValidationError, match="5,000"): validate_zip_metadata(many)
    # 16 x 64 MiB assets plus required roots exceed 1 GiB without allocating payloads.
    huge=[info("pack.json"), info("items.json")]
    huge += [info(f"assets/{i}.png", zs.VPACK_MAX_SINGLE_FILE_BYTES, zs.VPACK_MAX_SINGLE_FILE_BYTES) for i in range(16)]
    with pytest.raises(PackValidationError, match="1 GiB"): validate_zip_metadata(huge)


def test_source_512mib_limit_sparse(tmp_path):
    import fantasy_store.pack.zip_safety as zs
    p=tmp_path/"x.vpack"; p.touch(); p.write_bytes(b"")
    with p.open("r+b") as fp: fp.truncate(zs.VPACK_MAX_SOURCE_BYTES+1)
    with pytest.raises(PackValidationError): validate_source_size(p)


def test_actual_stream_byte_limits_are_rechecked():
    import io
    import fantasy_store.pack.zip_safety as zs
    with pytest.raises(PackValidationError, match="single-file"):
        zs._copy_stream(io.BytesIO(b"12345"), io.BytesIO(), max_file=4, remaining_total=100)
    with pytest.raises(PackValidationError, match="expanded-size"):
        zs._copy_stream(io.BytesIO(b"12345"), io.BytesIO(), max_file=100, remaining_total=4)
