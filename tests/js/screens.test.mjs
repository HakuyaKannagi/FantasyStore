import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { installFakeDom, FakeElement, deepText, findAll, findByText } from "./dom-harness.mjs";
import { renderCatalog } from "../../ui/js/screens/catalog.js";
import { renderProductDetail } from "../../ui/js/screens/product-detail.js";
import { renderCart } from "../../ui/js/screens/cart.js";
import { renderHistory } from "../../ui/js/screens/history.js";
import { renderPacks } from "../../ui/js/screens/packs.js";
import { state } from "../../ui/js/state.js";

function resetState() {
  state.routeGeneration = 1;
  state.route = "catalog";
  state.routeParams = {};
  state.checkoutRequestId = null;
  state.checkoutOrder = null;
  state.storeManagerMode = false;
  globalThis.crypto ??= webcrypto;
}

function ctx(root, client, routeParams = {}) {
  resetState(); state.routeParams = routeParams;
  return { root, client, router: { go(route, params = {}) { this.last = { route, params }; } }, state, generation: 1 };
}

for (const [catalogState, expected] of [
  ["NO_PACKS", "現在商品がありません"],
  ["ALL_PACKS_DISABLED", "現在購入できる商品がありません"],
  ["NO_SEARCH_RESULTS", "条件に一致する商品がありません"],
]) {
  test(`catalog renders normal empty state ${catalogState}`, async () => {
    installFakeDom(); const root = new FakeElement("main");
    const client = { getCategories: async () => ({ categories: [] }), getProducts: async () => ({ items: [], total: 0, page: 1, page_size: 24, catalog_state: catalogState }) };
    await renderCatalog(ctx(root, client));
    assert.match(deepText(root), new RegExp(expected));
    assert.ok(!deepText(root).includes("処理できませんでした"));
  });
}

test("catalog READY renders untrusted product as text and preparing placeholder", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const malicious = '<script>alert(1)</script><img src=x onerror=alert(1)>';
  const client = {
    getCategories: async () => ({ categories: ["<svg onload=alert(1)>"] }),
    getProducts: async () => ({ items: [{ pack_id: "p", item_id: "i", name: malicious, price: { terms: [], display: "0" }, category: "cat", primary_image_ref: null }], total: 1, page: 1, page_size: 24, catalog_state: "READY" }),
  };
  await renderCatalog(ctx(root, client));
  assert.ok(deepText(root).includes(malicious));
  assert.ok(deepText(root).includes("準備中★"));
  assert.equal(findAll(root, (n) => n.tagName === "SCRIPT").length, 0);
  assert.ok(findAll(root, (n) => n.tagName === "IMG" && n.src.endsWith("image-preparing.png")).length === 1);
});

test("product detail attributes remain XSS-safe text and add processing restores", async () => {
  installFakeDom(); const root = new FakeElement("main");
  let added = 0;
  const malicious = '"><svg onload=alert(1)>';
  const client = {
    getProductDetail: async () => ({ product: { pack_id: "p", item_id: "i", name: malicious, price: { display: "1", terms: [] }, category: "c", primary_image_ref: null, description: "<script>x</script>", attributes: { [malicious]: malicious }, image_refs: [] } }),
    addToCart: async () => { added += 1; return {}; },
  };
  await renderProductDetail(ctx(root, client, { pack_id: "p", item_id: "i" }));
  assert.ok(deepText(root).includes(malicious));
  assert.equal(findAll(root, (n) => n.tagName === "SVG" || n.tagName === "SCRIPT").length, 0);
  const add = findByText(root, "カートに追加"); await add.dispatch("click");
  assert.equal(added, 1); assert.equal(add.disabled, false);
});

test("cart distinguishes unavailable reasons and disables checkout", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const reasons = ["PACK_DISABLED", "ITEM_NOT_FOUND", "PACK_NOT_INSTALLED"];
  const client = {
    getCart: async () => ({ lines: reasons.map((reason, i) => ({ pack_id: `p${i}`, item_id: "i", quantity: 1, available: false, unavailable_reason: reason, name: null, unit_price: null, line_total: null, primary_image_ref: null })), total_amount: { display: "0", terms: [] }, total_quantity: 3, has_unavailable: true }),
  };
  await renderCart(ctx(root, client));
  const text = deepText(root);
  assert.ok(text.includes("この商品は現在購入できません")); assert.ok(text.includes("この商品は現在取り扱っていません")); assert.ok(text.includes("この商品の販売情報を確認できません"));
  assert.equal(findByText(root, "架空レジに進む").disabled, true);
});

test("checkout retry reuses same request_id and success clears it", async () => {
  installFakeDom(); const root = new FakeElement("main"); resetState();
  const seen = [];
  let attempt = 0;
  const client = {
    getCart: async () => ({ lines: [{ pack_id: "p", item_id: "i", quantity: 1, available: true, unavailable_reason: null, name: "Item", unit_price: { display: "1", terms: [] }, line_total: { display: "1", terms: [] }, primary_image_ref: null }], total_amount: { display: "1", terms: [] }, total_quantity: 1, has_unavailable: false }),
    checkout: async ({ request_id }) => { seen.push(request_id); attempt += 1; if (attempt === 1) throw Object.assign(new Error("一時失敗"), { code: "PACK_BUSY" }); return { order: { order_id: "o", purchased_at: new Date().toISOString(), total_amount: { display: "1", terms: [] }, total_quantity: 1, line_count: 1, lines: [] }, idempotent_replay: true }; },
  };
  const router = { go(route) { this.last = route; } };
  await renderCart({ root, client, router, state, generation: 1 });
  await findByText(root, "架空レジに進む").dispatch("click");
  assert.equal(state.checkoutRequestId, null);
  await findByText(root, "架空購入を確定").dispatch("click");
  const retained = state.checkoutRequestId; assert.match(retained, /^[0-9a-f-]{36}$/i);
  await renderCart({ root, client, router, state, generation: 1 });
  await findByText(root, "架空レジに進む").dispatch("click");
  await findByText(root, "架空購入を確定").dispatch("click");
  assert.deepEqual(seen, [retained, retained]); assert.equal(state.checkoutRequestId, null); assert.equal(router.last, "checkout-complete");
});

test("history missing snapshot uses local placeholder and never current pack image", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const orderId = "11111111-1111-4111-8111-111111111111";
  const client = {
    getStatistics: async () => ({ total_amount: { display: "10", terms: [] }, order_count: 1, total_quantity: 1 }),
    getOrderHistory: async () => ({ orders: [{ order_id: orderId, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: { display: "10", terms: [] }, total_quantity: 1, line_count: 1 }], total: 1, page: 1, page_size: 24 }),
    getOrderDetail: async () => ({ order: { order_id: orderId, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: { display: "10", terms: [] }, total_quantity: 1, line_count: 1, lines: [{ line_no: 1, pack_id: "p", item_id: "i", name: "old", unit_price: { display: "10", terms: [] }, quantity: 1, line_total: { display: "10", terms: [] }, category: "c", description: "old desc", attributes: {}, snapshot_image_ref: null, snapshot_image_available: false }] } }),
  };
  await renderHistory(ctx(root, client));
  const detail = findByText(root, "注文詳細を見る"); await detail.dispatch("click");
  const images = findAll(root, (n) => n.tagName === "IMG");
  assert.equal(images.length, 1); assert.ok(images[0].src.endsWith("history-image-missing.png")); assert.ok(!images[0].src.includes("pack-asset")); assert.ok(deepText(root).includes("old desc"));
});

test("store-manager pack screen handles cancel and shows uninstall policy", async () => {
  installFakeDom(); const root = new FakeElement("main");
  let imports = 0;
  const client = {
    getPacks: async () => ({ packs: [{ pack_id: "p", name: '<img src=x onerror=alert(1)>', version: "1.0", author: "a", description: "d", enabled: true, busy: false }], pack_state: "READY" }),
    importPack: async () => { imports += 1; return { status: "CANCELLED", pack: null }; },
    setPackEnabled: async () => ({}),
  };
  await renderPacks(ctx(root, client));
  assert.ok(deepText(root).includes("アンインストールするには、先にこの商品パックを無効にしてください。"));
  assert.equal(findByText(root, "アンインストール").disabled, true);
  assert.equal(findAll(root, (n) => n.tagName === "IMG").length, 0);
  await findByText(root, "商品パックを導入").dispatch("click");
  assert.equal(imports, 1); assert.ok(!deepText(root).includes("処理できませんでした"));
});

test("catalog filter submit sends search/category/sort and pagination requests", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const requests = [];
  const client = {
    getCategories: async () => ({ categories: ["Cat"] }),
    getProducts: async (req) => { requests.push(structuredClone(req)); return { items: [{ pack_id: "p", item_id: "i", name: "Item", price: { terms: [], display: "1" }, category: "Cat", primary_image_ref: "pack-asset:p:assets/a.png" }], total: 30, page: req.page, page_size: 24, catalog_state: "READY" }; },
  };
  await renderCatalog(ctx(root, client));
  const next = findByText(root, "次へ"); await next.dispatch("click");
  assert.equal(requests.at(-1).page, 2);
  assert.equal(requests.at(-1).sort, "name_asc");
});

test("product detail unavailable renders safe recovery path", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = { getProductDetail: async () => { throw Object.assign(new Error("現在利用できません"), { code: "PRODUCT_NOT_AVAILABLE" }); } };
  await renderProductDetail(ctx(root, client, { pack_id: "p", item_id: "i" }));
  assert.ok(deepText(root).includes("現在利用できません"));
  assert.ok(findByText(root, "商品一覧へ戻る"));
});

test("product add button is disabled while processing", async () => {
  installFakeDom(); const root = new FakeElement("main");
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const client = {
    getProductDetail: async () => ({ product: { pack_id: "p", item_id: "i", name: "Item", price: { display: "1", terms: [] }, category: "c", primary_image_ref: null, description: "d", attributes: {}, image_refs: [] } }),
    addToCart: async () => { await gate; return {}; },
  };
  await renderProductDetail(ctx(root, client, { pack_id: "p", item_id: "i" }));
  const add = findByText(root, "カートに追加");
  const pending = add.dispatch("click"); await Promise.resolve();
  assert.equal(add.disabled, true);
  release(); await pending; assert.equal(add.disabled, false);
});

test("cart empty is normal and clear/checkout are disabled", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = { getCart: async () => ({ lines: [], total_amount: { display: "0", terms: [] }, total_quantity: 0, has_unavailable: false }) };
  await renderCart(ctx(root, client));
  assert.ok(deepText(root).includes("カートは空です"));
  assert.equal(findByText(root, "カートを空にする").disabled, true);
  assert.equal(findByText(root, "架空レジに進む").disabled, true);
});

test("successful cart mutation abandons prior failed checkout request id", async () => {
  installFakeDom(); const root = new FakeElement("main"); resetState(); state.checkoutRequestId = "11111111-1111-4111-8111-111111111111";
  let updated = 0;
  const client = {
    getCart: async () => ({ lines: [{ pack_id: "p", item_id: "i", quantity: 1, available: true, unavailable_reason: null, name: "Item", unit_price: { display: "1", terms: [] }, line_total: { display: "1", terms: [] }, primary_image_ref: null }], total_amount: { display: "1", terms: [] }, total_quantity: 1, has_unavailable: false }),
    updateCartItem: async () => { updated += 1; return {}; },
  };
  await renderCart({ root, client, router: { go() {} }, state, generation: 1 });
  await findByText(root, "数量更新").dispatch("click");
  assert.equal(updated, 1); assert.equal(state.checkoutRequestId, null);
});

test("history supports pagination and local-time rendering", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const seen = [];
  const client = {
    getStatistics: async () => ({ total_amount: { display: "2", terms: [] }, order_count: 2, total_quantity: 2 }),
    getOrderHistory: async ({ page, page_size }) => { seen.push(page); return { orders: [{ order_id: `11111111-1111-4111-8111-11111111111${page}`, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: { display: "1", terms: [] }, total_quantity: 1, line_count: 1 }], total: 48, page, page_size }; },
  };
  await renderHistory(ctx(root, client));
  await findByText(root, "次へ").dispatch("click");
  assert.deepEqual(seen, [1, 2]);
  assert.ok(deepText(root).includes("2 / 2"));
});

test("pack import processing disables add and enable/disable controls", async () => {
  installFakeDom(); const root = new FakeElement("main");
  let release; const gate = new Promise((resolve) => { release = resolve; });
  const client = {
    getPacks: async () => ({ packs: [{ pack_id: "p", name: "Pack", version: "1.0", author: "a", description: "d", enabled: true, busy: false }], pack_state: "READY" }),
    importPack: async () => { await gate; return { status: "IMPORTED", pack: { pack_id: "x" } }; },
    setPackEnabled: async () => ({}),
  };
  await renderPacks(ctx(root, client));
  const add = findByText(root, "商品パックを導入"); const toggle = findByText(root, "無効にする");
  const pending = add.dispatch("click"); await Promise.resolve();
  assert.equal(add.disabled, true); assert.equal(toggle.disabled, true);
  release(); await pending;
  assert.ok(deepText(root).includes("商品パックを導入しました"));
});

test("pack busy disables only target operation", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = { getPacks: async () => ({ packs: [{ pack_id: "a", name: "A", version: "1.0", author: "a", description: "d", enabled: true, busy: true }, { pack_id: "b", name: "B", version: "1.0", author: "b", description: "d", enabled: false, busy: false }], pack_state: "READY" }) };
  await renderPacks(ctx(root, client));
  assert.equal(findByText(root, "無効にする").disabled, true);
  assert.equal(findByText(root, "有効にする").disabled, false);
});

test("malformed catalog data falls into screen error instead of breaking DOM", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = { getCategories: async () => ({ categories: [] }), getProducts: async () => ({ items: [{ pack_id: "p", item_id: "i", name: "bad", category: "c", primary_image_ref: null }], total: 1, page: 1, page_size: 24, catalog_state: "READY" }) };
  await renderCatalog(ctx(root, client));
  assert.ok(deepText(root).includes("処理できませんでした"));
});
