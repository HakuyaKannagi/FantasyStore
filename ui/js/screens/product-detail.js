import { el, errorPanel, imageUrlFromRef, loadingPanel, replace } from "../components/dom.js";
import { formatMoney } from "../money.js";
import { clearCheckoutAttempt, isCurrentGeneration } from "../state.js";

export async function renderProductDetail({ root, client, router, state, generation }) {
  replace(root, loadingPanel("商品を読み込んでいます…"));
  try {
    const data = await client.getProductDetail(state.routeParams);
    if (!isCurrentGeneration(generation)) return;
    const product = data.product;
    const image = el("img", {
      className: "detail-image",
      src: product.primary_image_ref ? imageUrlFromRef(product.primary_image_ref) : "./assets/image-missing.png",
      alt: product.name,
    });
    const attrs = el("dl", { className: "attributes" });
    for (const [key, value] of Object.entries(product.attributes ?? {})) {
      attrs.append(el("dt", { text: key }), el("dd", { text: typeof value === "string" ? value : JSON.stringify(value) }));
    }
    const qty = el("input", { type: "number", value: "1", attrs: { min: "1", max: "999", "aria-label": "数量" } });
    const add = el("button", { type: "button", className: "btn btn-primary btn-large", text: "カートに追加" });
    const status = el("p", { attrs: { role: "status" } });
    add.addEventListener("click", async () => {
      add.disabled = true;
      try {
        await client.addToCart({ pack_id: product.pack_id, item_id: product.item_id, quantity: qty.valueAsNumber });
        clearCheckoutAttempt();
        status.textContent = "カートに追加しました。";
      } catch (error) { status.textContent = error.message; }
      finally { add.disabled = false; }
    });
    const back = el("button", { type: "button", className: "btn btn-secondary", text: "商品一覧へ戻る" });
    back.addEventListener("click", () => router.go("catalog"));
    const info = el("section", { className: "detail-info" }, [
      el("p", { className: "product-category", text: product.category }),
      el("h2", { className: "detail-title", text: product.name }),
      el("p", { className: "money detail-price", text: formatMoney(product.price) }),
      el("p", { className: "detail-description", text: product.description }),
      attrs,
      el("div", { className: "purchase-controls" }, [el("label", { className: "quantity-label" }, [el("span", { text: "数量" }), qty]), add]),
      status,
      back,
    ]);
    replace(root, el("div", { className: "detail-layout" }, [el("section", { className: "detail-media" }, [image]), info]));
  } catch (error) {
    if (!isCurrentGeneration(generation)) return;
    const panel = errorPanel(error.message, () => router.go("product-detail", state.routeParams));
    const back = el("button", { type: "button", className: "btn btn-secondary", text: "商品一覧へ戻る" });
    back.addEventListener("click", () => router.go("catalog"));
    panel.append(back); replace(root, panel);
  }
}
