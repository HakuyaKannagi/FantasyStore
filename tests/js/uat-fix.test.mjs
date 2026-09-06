import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { webcrypto } from "node:crypto";
import { installFakeDom, FakeElement, deepText, findByText } from "./dom-harness.mjs";
import { Router } from "../../ui/js/router.js";
import { formatMoney } from "../../ui/js/money.js";
import { renderCatalog } from "../../ui/js/screens/catalog.js";
import { renderCart } from "../../ui/js/screens/cart.js";
import { renderCheckoutComplete } from "../../ui/js/screens/checkout-complete.js";
import { renderHistory } from "../../ui/js/screens/history.js";
import { renderPacks } from "../../ui/js/screens/packs.js";
import { bridgeClient } from "../../ui/js/bridge-client.js";
import { state } from "../../ui/js/state.js";

const ROOT = process.cwd();
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");

function resetState() {
  state.routeGeneration = 1;
  state.route = "catalog";
  state.routeParams = {};
  state.checkoutRequestId = null;
  state.checkoutOrder = null;
  state.storeManagerMode = false;
  globalThis.crypto ??= webcrypto;
}

function ctx(root, client) {
  resetState();
  return { root, client, router: { go(route, params = {}) { this.last = { route, params }; } }, state, generation: 1 };
}

test("normal HTML hides 店長モード while --store-manager resource exposes it", () => {
  const normal = read("ui/index.html");
  const admin = read("ui/index-store-manager.html");
  assert.match(normal, /data-store-manager="false"/);
  assert.doesNotMatch(normal, /data-route="packs"/);
  assert.doesNotMatch(normal, />パック管理</);
  assert.match(admin, /data-store-manager="true"/);
  assert.match(admin, /data-route="packs"/);
  assert.match(admin, />ショップ管理</);
});

test("normal Router blocks direct packs route while store-manager Router allows it", () => {
  let rendered = "";
  const renderers = { catalog: () => { rendered = "catalog"; }, packs: () => { rendered = "packs"; } };
  const normal = new Router(renderers, {}, {}, { storeManagerMode: false });
  normal.go("packs"); assert.equal(rendered, "catalog");
  const admin = new Router(renderers, {}, {}, { storeManagerMode: true });
  admin.go("packs"); assert.equal(rendered, "packs");
});

test("Money display adds yen and compacts giant integer without changing terms", () => {
  const terms = [{ significand: "123456789", exponent: "15" }];
  const money = { display: "498,000", terms };
  assert.equal(formatMoney(money), "498,000 円");
  assert.deepEqual(money.terms, terms);
  assert.equal(formatMoney({ display: "123456789 × 10²¹", terms }), "123456789 × 10²¹ 円");
  assert.equal(formatMoney({ display: "約 123456789 × 10⁹⁹⁹⁹⁹²", terms }), "約 123456789 × 10⁹⁹⁹⁹⁹² 円");
});

test("huge Money layout policy prevents card width expansion", () => {
  const css = read("ui/css/app.css");
  assert.match(css, /\.product-card\s*\{[^}]*min-width:\s*0/s);
  assert.match(css, /\.money\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(css, /body\s*\{[^}]*overflow-x:\s*hidden/s);
});

test("normal NO_PACKS and ALL_PACKS_DISABLED wording does not expose management action", async () => {
  for (const [catalogState, text] of [["NO_PACKS", "現在商品がありません"], ["ALL_PACKS_DISABLED", "現在購入できる商品がありません"]]) {
    installFakeDom(); const root = new FakeElement("main");
    const client = { getCategories: async () => ({ categories: [] }), getProducts: async () => ({ items: [], total: 0, page: 1, page_size: 24, catalog_state: catalogState }) };
    await renderCatalog(ctx(root, client));
    assert.ok(deepText(root).includes(text));
    assert.ok(!deepText(root).includes("商品パックを管理"));
    assert.ok(!deepText(root).includes("有効化"));
  }
});

test("checkout confirmation opens before checkout and request_id is generated only on confirm", async () => {
  installFakeDom(); const root = new FakeElement("main"); resetState();
  let calls = 0; let seen = null;
  const client = {
    getCart: async () => ({ lines: [{ pack_id: "p", item_id: "i", quantity: 2, available: true, unavailable_reason: null, name: "月面たこ焼き器", unit_price: { display: "1,000", terms: [] }, line_total: { display: "2,000", terms: [] }, primary_image_ref: null }], total_amount: { display: "2,000", terms: [] }, total_quantity: 2, has_unavailable: false }),
    checkout: async ({ request_id }) => { calls += 1; seen = request_id; return { order: { order_id: "o", purchased_at: new Date().toISOString(), total_amount: { display: "2,000", terms: [] }, total_quantity: 2, line_count: 1, lines: [] }, idempotent_replay: false }; },
  };
  const router = { go(route) { this.last = route; } };
  await renderCart({ root, client, router, state, generation: 1 });
  await findByText(root, "架空レジに進む").dispatch("click");
  assert.equal(calls, 0); assert.equal(state.checkoutRequestId, null);
  assert.ok(deepText(root).includes("実際の決済・請求・配送は行われません"));
  await findByText(root, "架空購入を確定").dispatch("click");
  assert.equal(calls, 1); assert.match(seen, /^[0-9a-f-]{36}$/i); assert.equal(router.last, "checkout-complete");
});

test("purchase complete reiterates fictional nature and fixed delivery performance", () => {
  installFakeDom(); const root = new FakeElement("main"); resetState();
  state.checkoutOrder = { order_id: "order-1", purchased_at: "2026-09-05T00:00:00.000Z", total_amount: { display: "12,000", terms: [] }, total_quantity: 1, lines: [{ name: "宇宙炊飯器", quantity: 1, unit_price: { display: "12,000", terms: [] }, line_total: { display: "12,000", terms: [] } }] };
  renderCheckoutComplete({ root, router: { go(route) { this.last = route; } }, state });
  const text = deepText(root);
  assert.ok(text.includes("架空購入が完了しました！"));
  assert.ok(text.includes("実際の請求・決済・配送は行われません"));
  assert.ok(text.includes("架空配送ステータス"));
  assert.ok(text.includes("時空の狭間"));
  assert.ok(text.includes("実際の配送は行われません"));
});

test("history cards include representative purchase-time item name and currency units", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const orderId = "11111111-1111-4111-8111-111111111111";
  const detail = { order_id: orderId, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: { display: "30,000", terms: [] }, total_quantity: 3, line_count: 2, lines: [{ line_no: 1, pack_id: "p", item_id: "i", name: "月面対応たこ焼き器 Mk-II", unit_price: { display: "10,000", terms: [] }, quantity: 1, line_total: { display: "10,000", terms: [] }, category: "家電", description: "snapshot", attributes: {}, snapshot_image_ref: null, snapshot_image_available: false }] };
  const client = {
    getStatistics: async () => ({ total_amount: { display: "30,000", terms: [] }, order_count: 1, total_quantity: 3 }),
    getOrderHistory: async () => ({ orders: [{ order_id: orderId, purchased_at: detail.purchased_at, total_amount: detail.total_amount, total_quantity: 3, line_count: 2 }], total: 1, page: 1, page_size: 24 }),
    getOrderDetail: async () => ({ order: detail }),
  };
  await renderHistory(ctx(root, client));
  const text = deepText(root);
  assert.ok(text.includes("月面対応たこ焼き器 Mk-II"));
  assert.ok(text.includes("ほか1商品"));
  assert.ok(text.includes("30,000 円"));
  assert.ok(text.includes("1 件"));
  assert.ok(text.includes("3 点"));
});

test("pywebview raw binding rejection is replaced with safe UI error", async () => {
  globalThis.__fantasyStoreBridge = { get_categories: async () => { throw new TypeError("ManagedBridgeApi.get_categories() missing 1 required positional argument: request"); } };
  await assert.rejects(() => bridgeClient.getCategories(), (error) => error.code === "INTERNAL_ERROR" && !error.message.includes("ManagedBridgeApi") && !error.message.includes("TypeError"));
  delete globalThis.__fantasyStoreBridge;
});


test("store-manager uninstall requires disabled pack and Human confirmation", async () => {
  installFakeDom(); const root = new FakeElement("main");
  let packs = [{ pack_id: "demo.pack", name: "Demo Pack", version: "1.2", author: "a", description: "d", enabled: false, busy: false }];
  let uninstallCalls = 0;
  const client = {
    getPacks: async () => ({ packs, pack_state: packs.length ? "ALL_PACKS_DISABLED" : "NO_PACKS" }),
    importPack: async () => ({ status: "CANCELLED", pack: null }),
    setPackEnabled: async () => ({}),
    uninstallPack: async ({ pack_id }) => { uninstallCalls += 1; assert.equal(pack_id, "demo.pack"); packs = []; return { pack: null }; },
  };
  await renderPacks(ctx(root, client));
  const button = findByText(root, "アンインストール");
  assert.equal(button.disabled, false);
  await button.dispatch("click");
  assert.equal(uninstallCalls, 0);
  assert.ok(deepText(root).includes("過去の購入履歴は削除されません"));
  await findByText(root, "アンインストール").dispatch("click");
  assert.equal(uninstallCalls, 1);
  assert.ok(deepText(root).includes("購入履歴は保持されています"));
});

test("enabled pack uninstall is visibly disabled with reason", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = {
    getPacks: async () => ({ packs: [{ pack_id: "demo.pack", name: "Demo Pack", version: "1.2", author: "a", description: "d", enabled: true, busy: false }], pack_state: "READY" }),
    importPack: async () => ({ status: "CANCELLED", pack: null }),
    setPackEnabled: async () => ({}),
    uninstallPack: async () => { throw new Error("must not run"); },
  };
  await renderPacks(ctx(root, client));
  assert.equal(findByText(root, "アンインストール").disabled, true);
  assert.ok(deepText(root).includes("先にこの商品パックを無効にしてください"));
});

test("downgrade skip is presented as intentional policy, not failure", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = {
    getPacks: async () => ({ packs: [{ pack_id: "demo.pack", name: "Demo Pack", version: "1.2", author: "a", description: "d", enabled: true, busy: false }], pack_state: "READY" }),
    importPack: async () => ({ status: "DOWNGRADE_SKIPPED", pack: { pack_id: "demo.pack" }, classification: "DOWNGRADE_SKIPPED", installed_version: "1.2", incoming_version: "1.1" }),
    setPackEnabled: async () => ({}),
    uninstallPack: async () => ({}),
  };
  await renderPacks(ctx(root, client));
  await findByText(root, "商品パックを導入").dispatch("click");
  assert.ok(deepText(root).includes("導入をスキップしました"));
  assert.ok(deepText(root).includes("1.2"));
  assert.ok(deepText(root).includes("1.1"));
  assert.ok(!deepText(root).includes("処理できませんでした"));
});
