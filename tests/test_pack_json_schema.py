from __future__ import annotations

import json
import pytest

from fantasy_store.domain.errors import PackValidationError
from fantasy_store.pack.json_loader import loads_strict_json_bytes
from fantasy_store.pack.schema_validator import LocalSchemaValidator
from fantasy_store.runtime.resource_locator import ResourceLocator
from tests.phase3_helpers import base_items, base_pack


def test_json_utf8_and_bom():
    assert loads_strict_json_bytes(b'{"a":1}') == {"a": 1}
    assert loads_strict_json_bytes(b'\xef\xbb\xbf{"a":1}') == {"a": 1}

@pytest.mark.parametrize("data", [b"\xff\xfe{\x00}\x00", b"\xfe\xff\x00{\x00}", b"\xff", b'{"a":'])
def test_json_rejects_encoding_or_malformed(data):
    with pytest.raises(PackValidationError): loads_strict_json_bytes(data)

@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'])
def test_json_rejects_duplicate_and_nonstandard(text):
    with pytest.raises(PackValidationError): loads_strict_json_bytes(text.encode())


def test_schema_valid_and_local(tmp_path):
    v = LocalSchemaValidator(ResourceLocator(base=tmp_path)) if False else LocalSchemaValidator()
    v.validate_pack(base_pack()); v.validate_items(base_items())

@pytest.mark.parametrize("mutator", [
    lambda p: p.update(schema_version=2),
    lambda p: p.update(extra=True),
    lambda p: p.pop("author"),
])
def test_pack_schema_rejections(mutator):
    p=base_pack(); mutator(p)
    with pytest.raises(PackValidationError): LocalSchemaValidator().validate_pack(p)

@pytest.mark.parametrize("mutator", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(extra=True),
    lambda d: d["items"][0].pop("name"),
])
def test_items_schema_rejections(mutator):
    d=base_items(); mutator(d)
    with pytest.raises(PackValidationError): LocalSchemaValidator().validate_items(d)
