import { el, imageUrlFromRef } from "./dom.js";
import { formatMoney } from "../money.js";

const REASONS = {
  PACK_DISABLED: "この商品は現在購入できません",
  ITEM_NOT_FOUND: "この商品は現在取り扱っていません",
  PACK_NOT_INSTALLED: "この商品の販売情報を確認できません",
};

export function cartLine(line, handlers) {
  const image = el("img", {
    className: "cart-image",
    src: line.primary_image_ref ? imageUrlFromRef(line.primary_image_ref) : "./assets/image-missing.png",
    alt: line.name ? line.name : "商品画像",
  });
  const imageNode = line.available && handlers.openDetail
    ? el("button", { type: "button", className: "product-media-link", attrs: { "aria-label": `${line.name ?? "商品"}の詳細を見る` } }, [image])
    : image;
  if (imageNode !== image) imageNode.addEventListener("click", () => handlers.openDetail(line));

  const name = line.available && handlers.openDetail
    ? el("button", { type: "button", className: "text-link cart-item-name", text: line.name ?? "商品" })
    : el("strong", { className: "cart-item-name", text: line.name ?? "現在利用できない商品" });
  if (line.available && handlers.openDetail) name.addEventListener("click", () => handlers.openDetail(line));

  const info = el("div", { className: "cart-item-info" }, [
    name,
    line.category ? el("p", { className: "product-category", text: line.category }) : null,
    line.available ? el("p", { className: "money", text: `単価 ${formatMoney(line.unit_price)}` }) : null,
    line.available ? el("p", { className: "money line-total", text: `小計 ${formatMoney(line.line_total)}` }) : el("p", { className: "warning", text: REASONS[line.unavailable_reason] ?? "現在利用できません" }),
  ]);

  const qty = el("input", { type: "number", value: line.quantity, attrs: { min: "1", max: "999", "aria-label": `${line.name ?? "商品"}の数量` } });
  const minus = el("button", { type: "button", className: "btn btn-quantity", text: "−", disabled: line.quantity <= 1 || !line.available });
  const plus = el("button", { type: "button", className: "btn btn-quantity", text: "+", disabled: line.quantity >= 999 || !line.available });
  const update = el("button", { type: "button", className: "btn btn-secondary", text: "数量更新", disabled: !line.available });
  minus.addEventListener("click", () => handlers.update(line, Math.max(1, qty.valueAsNumber - 1), minus));
  plus.addEventListener("click", () => handlers.update(line, Math.min(999, qty.valueAsNumber + 1), plus));
  update.addEventListener("click", () => handlers.update(line, qty.valueAsNumber, update));
  const quantityControls = el("div", { className: "quantity-controls" }, [minus, qty, plus, update]);
  const remove = el("button", { type: "button", className: "btn btn-danger", text: "削除" });
  remove.addEventListener("click", () => handlers.remove(line, remove));
  return el("article", { className: "cart-line" }, [imageNode, info, quantityControls, remove]);
}
