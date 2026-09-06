import { webcrypto } from "node:crypto";
import test from "node:test";
import assert from "node:assert/strict";
import { validateEnvelope } from "../../ui/js/bridge-client.js";
import { imageUrlFromRef } from "../../ui/js/components/dom.js";
import { moneyLiteralFromInput } from "../../ui/js/screens/catalog.js";
import { uuidV4 } from "../../ui/js/screens/cart.js";


test("Bridge envelope accepts success and rejects malformed responses", () => {
  assert.deepEqual(validateEnvelope({ ok: true, data: { x: 1 }, error: null }), { x: 1 });
  assert.throws(() => validateEnvelope(null), /malformed/);
  assert.throws(() => validateEnvelope({ ok: true, data: null, error: null }), /malformed/);
  assert.throws(() => validateEnvelope({ ok: false, data: {}, error: { code: "X", message: "x" } }), /malformed/);
});

test("Bridge envelope turns safe error into Error with code", () => {
  assert.throws(() => validateEnvelope({ ok: false, data: null, error: { code: "PACK_BUSY", message: "待ってください", details: null } }), (err) => err.code === "PACK_BUSY" && err.message === "待ってください");
});

test("Money input parser stays string-exact and canonical", () => {
  assert.deepEqual(moneyLiteralFromInput("1000"), { significand: "1", exponent: "3" });
  assert.deepEqual(moneyLiteralFromInput("0012300e0004"), { significand: "123", exponent: "6" });
  assert.deepEqual(moneyLiteralFromInput("0"), { significand: "0", exponent: "0" });
  assert.deepEqual(moneyLiteralFromInput("123e1000000000000"), { significand: "123", exponent: "1000000000000" });
  assert.equal(moneyLiteralFromInput(""), null);
  assert.throws(() => moneyLiteralFromInput("12.3"));
});

test("opaque image ref becomes only fantasy-image URL", () => {
  const url = imageUrlFromRef("pack-asset:demo.pack:assets/商品.png");
  assert.ok(url.startsWith("fantasy-image://resource/"));
  assert.ok(!url.includes("C:\\"));
  assert.ok(!url.startsWith("file:"));
});

test("checkout UUID is v4", () => {
  globalThis.crypto ??= webcrypto;
  const id = uuidV4();
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
});
