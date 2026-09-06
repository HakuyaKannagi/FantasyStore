import test from "node:test";
import assert from "node:assert/strict";
import { renderCatalog } from "../../ui/js/screens/catalog.js";
import { state } from "../../ui/js/state.js";
import { installFakeDom, FakeElement, findAll, deepText } from "./dom-harness.mjs";

function resetState() {
  state.routeGeneration = 1; state.route = "catalog"; state.routeParams = {};
  state.checkoutRequestId = null; state.checkoutOrder = null; state.storeManagerMode = false;
}
function context(root, client) {
  resetState(); return { root, client, router: { go() {} }, state, generation: 1 };
}
function inputByPlaceholder(root, placeholder) {
  return findAll(root, (n) => n.tagName === "INPUT" && n.attributes.placeholder === placeholder)[0];
}
function selectByFirstOption(root, text) {
  return findAll(root, (n) => n.tagName === "SELECT" && n.children.some((c) => c._text === text))[0];
}
function form(root) { return findAll(root, (n) => n.tagName === "FORM")[0]; }

function catalogClient(requests) {
  return {
    getCategories: async () => ({ categories: ["家具", "雑貨"] }),
    getProducts: async (request) => {
      requests.push(structuredClone(request));
      return { items: [], total: 0, page: request.page, page_size: request.page_size, catalog_state: "NO_SEARCH_RESULTS" };
    },
  };
}

test("catalog preserves keyword/category/sort and raw min/max strings after redraw", async () => {
  installFakeDom(); const root = new FakeElement("main"); const requests = [];
  await renderCatalog(context(root, catalogClient(requests)));
  inputByPlaceholder(root, "商品名・説明など").value = "月面";
  inputByPlaceholder(root, "最低価格（円）").value = "123e4";
  inputByPlaceholder(root, "最高価格（円）").value = "999e5";
  const selects = findAll(root, (n) => n.tagName === "SELECT");
  selects[0].value = "家具"; selects[1].value = "price_desc";
  await form(root).dispatch("submit");
  assert.equal(inputByPlaceholder(root, "商品名・説明など").value, "月面");
  assert.equal(inputByPlaceholder(root, "最低価格（円）").value, "123e4");
  assert.equal(inputByPlaceholder(root, "最高価格（円）").value, "999e5");
  const afterSelects = findAll(root, (n) => n.tagName === "SELECT");
  assert.equal(afterSelects[0].value, "家具");
  assert.equal(afterSelects[1].value, "price_desc");
  const request = requests.at(-1);
  assert.deepEqual(request.min_price, { significand: "123", exponent: "4" });
  assert.deepEqual(request.max_price, { significand: "999", exponent: "5" });
});

test("catalog clearing min/max removes applied price condition and keeps inputs empty", async () => {
  installFakeDom(); const root = new FakeElement("main"); const requests = [];
  await renderCatalog(context(root, catalogClient(requests)));
  inputByPlaceholder(root, "最低価格（円）").value = "123e4";
  inputByPlaceholder(root, "最高価格（円）").value = "999e5";
  await form(root).dispatch("submit");
  inputByPlaceholder(root, "最低価格（円）").value = "";
  inputByPlaceholder(root, "最高価格（円）").value = "";
  await form(root).dispatch("submit");
  assert.equal(inputByPlaceholder(root, "最低価格（円）").value, "");
  assert.equal(inputByPlaceholder(root, "最高価格（円）").value, "");
  assert.equal(requests.at(-1).min_price, null);
  assert.equal(requests.at(-1).max_price, null);
});

test("invalid price keeps Human input visible for correction", async () => {
  installFakeDom(); const root = new FakeElement("main"); const requests = [];
  await renderCatalog(context(root, catalogClient(requests)));
  const min = inputByPlaceholder(root, "最低価格（円）"); min.value = "-10";
  await form(root).dispatch("submit");
  assert.equal(inputByPlaceholder(root, "最低価格（円）").value, "-10");
  assert.ok(deepText(root).includes("価格は整数または指数形式で入力してください"));
});

test("catalog shows one shared e-notation helper for the price condition", async () => {
  installFakeDom(); const root = new FakeElement("main"); const requests = [];
  await renderCatalog(context(root, catalogClient(requests)));
  const helpers = findAll(root, (n) => n.tagName === "SMALL" && n._text.includes("123e4"));
  assert.equal(helpers.length, 1);
  assert.ok(helpers[0]._text.includes("123 × 10⁴"));
});
