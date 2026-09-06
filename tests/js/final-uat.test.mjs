import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { webcrypto } from "node:crypto";
import { installFakeDom, FakeElement, deepText, findAll, findByText } from "./dom-harness.mjs";
import { renderPacks } from "../../ui/js/screens/packs.js";
import { renderCart } from "../../ui/js/screens/cart.js";
import { renderCheckoutComplete } from "../../ui/js/screens/checkout-complete.js";
import { renderHistory } from "../../ui/js/screens/history.js";
import { renderCatalog } from "../../ui/js/screens/catalog.js";
import { state } from "../../ui/js/state.js";

function resetState() {
  state.routeGeneration = 1; state.route = "catalog"; state.routeParams = {};
  state.checkoutRequestId = null; state.checkoutOrder = null; state.storeManagerMode = false;
  globalThis.crypto ??= webcrypto;
}
function ctx(root, client, router = { go(route, params = {}) { this.last = { route, params }; } }) {
  resetState(); return { root, client, router, state, generation: 1 };
}

const money = (display) => ({ display, terms: [] });

test("store-manager navigation is ショップ管理 and recovery UI has no shop-management surface", () => {
  const manager = fs.readFileSync("ui/index-store-manager.html", "utf8");
  const normal = fs.readFileSync("ui/index.html", "utf8");
  const recovery = fs.readFileSync("fantasy_store/runtime/store_manager_recovery.py", "utf8");
  assert.match(manager, />ショップ管理</);
  assert.doesNotMatch(normal, />ショップ管理</);
  assert.doesNotMatch(recovery, /ショップ管理/);
});

test("ショップ管理 separates Pack management and store data reset with confirmation", async () => {
  installFakeDom(); const root = new FakeElement("main"); let resets = 0;
  const client = {
    getPacks: async () => ({ packs: [], pack_state: "NO_PACKS" }),
    importPack: async () => ({ status: "CANCELLED", pack: null }),
    resetPurchaseHistory: async () => { resets += 1; return { deleted_orders: 2, removed_snapshot_directories: 2, failed_snapshot_directories: 0 }; },
  };
  await renderPacks(ctx(root, client));
  assert.ok(deepText(root).includes("ショップ管理"));
  assert.ok(deepText(root).includes("商品Pack管理"));
  assert.ok(deepText(root).includes("店舗データ管理"));
  await findByText(root, "購入履歴をすべてリセット").dispatch("click");
  assert.equal(resets, 0);
  assert.ok(deepText(root).includes("この操作は元に戻せません"));
  const confirmReset = findAll(root, (n) => n.tagName === "BUTTON" && n._text === "購入履歴をすべてリセット")[0];
  await confirmReset.dispatch("click");
  assert.equal(resets, 1);
  assert.ok(deepText(root).includes("購入履歴をすべてリセットしました"));
});

test("cart image and name link to current product detail when available", async () => {
  installFakeDom(); const root = new FakeElement("main"); const router = { go(route, params = {}) { this.last = { route, params }; } };
  const client = {
    getCart: async () => ({ lines: [{ pack_id: "p", item_id: "i", quantity: 1, available: true, unavailable_reason: null, name: "商品A", unit_price: money("3,980"), line_total: money("3,980"), primary_image_ref: "pack-asset:p:assets/a.png" }], total_amount: money("3,980"), total_quantity: 1, has_unavailable: false }),
    getProductDetail: async () => ({ product: { category: "cat" } }),
  };
  await renderCart(ctx(root, client, router));
  const nameButton = findAll(root, (n) => n.tagName === "BUTTON" && n.className.includes("cart-item-name"))[0];
  const imageButton = findAll(root, (n) => n.tagName === "BUTTON" && n.className.includes("product-media-link"))[0];
  assert.ok(nameButton && imageButton);
  await nameButton.dispatch("click");
  assert.deepEqual(router.last, { route: "product-detail", params: { pack_id: "p", item_id: "i" } });
  await imageButton.dispatch("click");
  assert.deepEqual(router.last, { route: "product-detail", params: { pack_id: "p", item_id: "i" } });
});

test("checkout confirmation and purchase complete show unit price times quantity and subtotal", async () => {
  installFakeDom(); const root = new FakeElement("main"); resetState();
  const client = {
    getCart: async () => ({ lines: [{ pack_id: "p", item_id: "i", quantity: 3, available: true, unavailable_reason: null, name: "座布団", unit_price: money("7,200"), line_total: money("21,600"), primary_image_ref: null }], total_amount: money("21,600"), total_quantity: 3, has_unavailable: false }),
    getProductDetail: async () => ({ product: { category: "生活" } }),
  };
  await renderCart({ root, client, router: { go() {} }, state, generation: 1 });
  await findByText(root, "架空レジに進む").dispatch("click");
  assert.ok(deepText(root).includes("単価 7,200 円 × 3"));
  assert.ok(deepText(root).includes("小計 21,600 円"));

  const completeRoot = new FakeElement("main");
  state.checkoutOrder = { order_id: "o", purchased_at: "2026-09-05T00:00:00.000Z", total_amount: money("21,600"), total_quantity: 3, lines: [{ name: "座布団", quantity: 3, unit_price: money("7,200"), line_total: money("21,600") }] };
  renderCheckoutComplete({ root: completeRoot, router: { go() {} }, state });
  assert.ok(deepText(completeRoot).includes("単価 7,200 円 × 3"));
  assert.ok(deepText(completeRoot).includes("小計 21,600 円"));
});

test("history keeps one order card, shows each line snapshot once and 点 / 商品 semantics", async () => {
  installFakeDom(); const root = new FakeElement("main"); const router = { go(route, params = {}) { this.last = { route, params }; } };
  const orderId = "11111111-1111-4111-8111-111111111111";
  const lines = [
    { line_no: 1, pack_id: "p", item_id: "a", name: "座布団", unit_price: money("1,000"), quantity: 3, line_total: money("3,000"), category: "c", description: "d", attributes: {}, snapshot_image_ref: "snapshot:o:1.png", snapshot_image_available: true },
    { line_no: 2, pack_id: "p", item_id: "b", name: "延長コード", unit_price: money("2,000"), quantity: 2, line_total: money("4,000"), category: "c", description: "d", attributes: {}, snapshot_image_ref: "snapshot:o:2.png", snapshot_image_available: true },
    { line_no: 3, pack_id: "p", item_id: "c", name: "月曜日", unit_price: money("10"), quantity: 25, line_total: money("250"), category: "c", description: "d", attributes: {}, snapshot_image_ref: null, snapshot_image_available: false },
  ];
  const detail = { order_id: orderId, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: money("7,250"), total_quantity: 30, line_count: 3, lines };
  const client = {
    getStatistics: async () => ({ total_amount: money("7,250"), order_count: 1, total_quantity: 30 }),
    getOrderHistory: async () => ({ orders: [{ order_id: orderId, purchased_at: detail.purchased_at, total_amount: detail.total_amount, total_quantity: 30, line_count: 3 }], total: 1, page: 1, page_size: 24 }),
    getOrderDetail: async () => ({ order: detail }),
    getProductDetail: async ({ item_id }) => { if (item_id === "c") throw new Error("missing"); return { product: { item_id } }; },
  };
  await renderHistory(ctx(root, client, router));
  assert.equal(findAll(root, (n) => n.className === "history-card").length, 1);
  assert.equal(findAll(root, (n) => n.className === "history-card-product").length, 3);
  assert.ok(deepText(root).includes("30点 / 3商品"));
  assert.ok(deepText(root).includes("ほか2商品"));
  assert.equal(findAll(root, (n) => n.tagName === "IMG" && n.src.includes("fantasy-image://")).length, 2);
  assert.equal(findAll(root, (n) => n.tagName === "IMG" && n.src.endsWith("history-image-missing.png")).length, 1);
  const currentName = findAll(root, (n) => n.tagName === "BUTTON" && n._text === "座布団")[0];
  assert.ok(currentName);
  await currentName.dispatch("click");
  assert.deepEqual(router.last, { route: "product-detail", params: { pack_id: "p", item_id: "a" } });
  assert.equal(findAll(root, (n) => n.tagName === "BUTTON" && n._text === "月曜日").length, 0);
});

test("catalog explains e notation next to price fields and in invalid-input message", async () => {
  installFakeDom(); const root = new FakeElement("main");
  const client = { getCategories: async () => ({ categories: [] }), getProducts: async () => ({ items: [], total: 0, page: 1, page_size: 24, catalog_state: "NO_SEARCH_RESULTS" }) };
  await renderCatalog(ctx(root, client));
  assert.ok(deepText(root).includes("例: 123e4（= 123 × 10⁴）"));
  const inputs = findAll(root, (n) => n.tagName === "INPUT");
  const min = inputs.find((n) => n.attributes.placeholder === "最低価格（円）");
  min.value = "-1";
  const form = findAll(root, (n) => n.tagName === "FORM")[0];
  await form.dispatch("submit");
  assert.ok(deepText(root).includes("価格は整数または指数形式で入力してください"));
  assert.ok(deepText(root).includes("123 × 10⁴"));
});

test("history detail image and name link only when current identity resolves", async () => {
  installFakeDom(); const root = new FakeElement("main"); const router = { go(route, params = {}) { this.last = { route, params }; } };
  const orderId = "22222222-2222-4222-8222-222222222222";
  const line = { line_no: 1, pack_id: "p", item_id: "i", name: "購入時の名前", unit_price: money("1,000"), quantity: 1, line_total: money("1,000"), category: "c", description: "snapshot", attributes: {}, snapshot_image_ref: "snapshot:o:1.png", snapshot_image_available: true };
  const detail = { order_id: orderId, purchased_at: "2026-09-05T00:00:00.000Z", total_amount: money("1,000"), total_quantity: 1, line_count: 1, lines: [line] };
  const client = {
    getStatistics: async () => ({ total_amount: money("1,000"), order_count: 1, total_quantity: 1 }),
    getOrderHistory: async () => ({ orders: [{ order_id: orderId, purchased_at: detail.purchased_at, total_amount: detail.total_amount, total_quantity: 1, line_count: 1 }], total: 1, page: 1, page_size: 24 }),
    getOrderDetail: async () => ({ order: detail }),
    getProductDetail: async () => ({ product: { pack_id: "p", item_id: "i" } }),
  };
  await renderHistory(ctx(root, client, router));
  await findByText(root, "注文詳細を見る").dispatch("click");
  const name = findAll(root, (n) => n.tagName === "BUTTON" && n._text === "購入時の名前")[0];
  const image = findAll(root, (n) => n.tagName === "BUTTON" && n.className.includes("history-product-link"))[0];
  assert.ok(name && image);
  await name.dispatch("click");
  assert.deepEqual(router.last, { route: "product-detail", params: { pack_id: "p", item_id: "i" } });
});
