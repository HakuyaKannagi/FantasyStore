import { el, errorPanel, loadingPanel, replace } from "../components/dom.js";
import { emptyState } from "../components/empty-state.js";
import { productCard } from "../components/product-card.js";
import { isCurrentGeneration } from "../state.js";

function moneyLiteralFromInput(text) {
  const raw = String(text ?? "").trim();
  if (!raw) return null;
  const match = raw.match(/^([0-9]+)(?:[eE]([0-9]+))?$/);
  if (!match) throw new Error("価格は整数または指数形式で入力してください。例: 123e4（= 123 × 10⁴）");
  let digits = match[1];
  let exponent = match[2] ?? "0";
  digits = digits.replace(/^0+(?=\d)/, "");
  exponent = exponent.replace(/^0+(?=\d)/, "");
  if (/^0+$/.test(digits)) return { significand: "0", exponent: "0" };
  let zeros = 0;
  while (digits.endsWith("0")) { digits = digits.slice(0, -1); zeros += 1; }
  if (zeros) {
    let carry = String(zeros);
    let i = exponent.length - 1, j = carry.length - 1, c = 0, out = "";
    while (i >= 0 || j >= 0 || c) {
      const a = i >= 0 ? exponent.charCodeAt(i--) - 48 : 0;
      const b = j >= 0 ? carry.charCodeAt(j--) - 48 : 0;
      const s = a + b + c; out = String(s % 10) + out; c = s >= 10 ? 1 : 0;
    }
    exponent = out;
  }
  return { significand: digits, exponent };
}

export async function renderCatalog(ctx) {
  const { root, client, router, generation, state } = ctx;
  replace(root, loadingPanel("商品を読み込んでいます…"));
  const request = { query: String(state.routeParams?.query ?? ""), category: null, min_price: null, max_price: null, sort: "name_asc", page: 1, page_size: 24 };
  const formState = {
    query: request.query,
    category: "",
    minPriceText: "",
    maxPriceText: "",
    sort: request.sort,
  };

  async function load() {
    replace(root, loadingPanel("商品を読み込んでいます…"));
    try {
      const [categories, data] = await Promise.all([client.getCategories(), client.getProducts(request)]);
      if (!isCurrentGeneration(generation)) return;
      draw(categories.categories, data);
    } catch (error) {
      if (!isCurrentGeneration(generation)) return;
      replace(root, errorPanel(error.message, load));
    }
  }

  function draw(categories, data) {
    const heading = el("div", { className: "section-heading" }, [
      el("div", {}, [el("p", { className: "eyebrow", text: "FantasyStore" }), el("h2", { text: "商品一覧" })]),
      el("p", { className: "muted", text: data.total ? `${data.total} 商品` : "" }),
    ]);
    const form = el("form", { className: "search-filter-panel" });
    const q = el("input", { type: "search", value: formState.query, attrs: { maxlength: "200", placeholder: "商品名・説明など" } });
    const cat = el("select");
    cat.append(el("option", { value: "", text: "すべてのカテゴリ" }));
    for (const value of categories) cat.append(el("option", { value, text: value }));
    if (formState.category) cat.value = formState.category;
    const min = el("input", { type: "text", value: formState.minPriceText, attrs: { inputmode: "numeric", placeholder: "最低価格（円）" } });
    const max = el("input", { type: "text", value: formState.maxPriceText, attrs: { inputmode: "numeric", placeholder: "最高価格（円）" } });
    const sort = el("select");
    for (const [value, label] of [["name_asc","名前順"],["price_asc","価格が低い順"],["price_desc","価格が高い順"]]) sort.append(el("option", { value, text: label }));
    sort.value = formState.sort;
    const submit = el("button", { type: "submit", className: "btn btn-primary", text: "検索する" });
    form.append(
      el("label", { className: "field grow" }, [el("span", { text: "キーワード" }), q]),
      el("label", { className: "field" }, [el("span", { text: "カテゴリ" }), cat]),
      el("label", { className: "field" }, [el("span", { text: "最低価格" }), min]),
      el("label", { className: "field" }, [el("span", { text: "最高価格" }), max]),
      el("label", { className: "field" }, [el("span", { text: "並び順" }), sort]), submit,
      el("small", { className: "field-hint price-filter-hint", text: "指数形式も入力できます。例: 123e4（= 123 × 10⁴）" }),
    );
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); submit.disabled = true;
      try {
        formState.query = q.value;
        formState.category = cat.value || "";
        formState.minPriceText = min.value;
        formState.maxPriceText = max.value;
        formState.sort = sort.value;
        request.query = formState.query;
        request.category = formState.category || null;
        request.min_price = moneyLiteralFromInput(formState.minPriceText);
        request.max_price = moneyLiteralFromInput(formState.maxPriceText);
        request.sort = formState.sort;
        request.page = 1;
        await load();
      } catch (error) {
        replace(root, form, errorPanel(error.message, () => router.go("catalog")));
      } finally { submit.disabled = false; }
    });

    const body = el("section");
    if (data.catalog_state === "NO_PACKS") {
      body.append(state.storeManagerMode ? emptyState("現在商品がありません", "商品パックを管理", () => router.go("packs")) : emptyState("現在商品がありません"));
    } else if (data.catalog_state === "ALL_PACKS_DISABLED") {
      body.append(state.storeManagerMode ? emptyState("現在購入できる商品がありません", "商品パックを管理", () => router.go("packs")) : emptyState("現在購入できる商品がありません"));
    } else if (data.catalog_state === "NO_SEARCH_RESULTS") {
      body.append(emptyState("条件に一致する商品がありません", "検索条件をリセット", () => router.go("catalog")));
    } else {
      const grid = el("div", { className: "product-grid" });
      for (const item of data.items) grid.append(productCard(item, (packId, itemId) => router.go("product-detail", { pack_id: packId, item_id: itemId })));
      body.append(grid);
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      const prev = el("button", { type: "button", className: "btn btn-secondary", text: "前へ", disabled: data.page <= 1 });
      const next = el("button", { type: "button", className: "btn btn-secondary", text: "次へ", disabled: data.page >= pages });
      prev.addEventListener("click", () => { request.page -= 1; load(); });
      next.addEventListener("click", () => { request.page += 1; load(); });
      body.append(el("div", { className: "pagination" }, [prev, el("span", { text: `${data.page} / ${pages}` }), next]));
    }
    replace(root, heading, form, body);
  }

  await load();
}

export { moneyLiteralFromInput };
