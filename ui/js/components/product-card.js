import { el, imageUrlFromRef } from "./dom.js";
import { formatMoney } from "../money.js";

export function productCard(product, onOpen) {
  const img = el("img", {
    src: product.primary_image_ref ? imageUrlFromRef(product.primary_image_ref) : "./assets/image-preparing.png",
    alt: product.primary_image_ref ? product.name : `${product.name}（準備中）`,
  });
  const button = el("button", { type: "button", className: "btn btn-secondary card-cta", text: "詳細を見る" });
  button.addEventListener("click", () => onOpen(product.pack_id, product.item_id));
  return el("article", { className: "product-card" }, [
    img,
    el("h3", { className: "product-name", text: product.name }),
    el("p", { className: "product-category", text: product.category }),
    el("p", { className: "money product-price", text: formatMoney(product.price) }),
    product.primary_image_ref ? null : el("span", { className: "badge", text: "準備中★" }),
    button,
  ]);
}
