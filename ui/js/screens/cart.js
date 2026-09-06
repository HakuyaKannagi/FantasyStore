import { el, errorPanel, loadingPanel, replace } from "../components/dom.js";
import { cartLine } from "../components/cart-line.js";
import { formatMoney } from "../money.js";
import { clearCheckoutAttempt, isCurrentGeneration } from "../state.js";

function uuidV4() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

async function enrichCartLines(client, lines) {
  if (typeof client.getProductDetail !== "function") return lines;
  return Promise.all(lines.map(async (line) => {
    if (!line.available) return line;
    try {
      const detail = await client.getProductDetail({ pack_id: line.pack_id, item_id: line.item_id });
      return { ...line, category: detail.product.category };
    } catch {
      return line;
    }
  }));
}

export async function renderCart({ root, client, router, state, generation }) {
  async function load() {
    replace(root, loadingPanel("カートを読み込んでいます…"));
    try {
      const raw = await client.getCart();
      const lines = await enrichCartLines(client, raw.lines);
      if (!isCurrentGeneration(generation)) return;
      draw({ ...raw, lines });
    } catch (error) { if (isCurrentGeneration(generation)) replace(root, errorPanel(error.message, load)); }
  }

  function openConfirmation(data) {
    const lines = el("div", { className: "confirm-lines" });
    for (const line of data.lines) {
      if (!line.available) continue;
      lines.append(el("div", { className: "confirm-line" }, [
        el("span", { text: line.name ?? "商品" }),
        el("span", { className: "money", text: `単価 ${formatMoney(line.unit_price)} × ${line.quantity}` }),
        el("strong", { className: "money", text: `小計 ${formatMoney(line.line_total)}` }),
      ]));
    }
    const status = el("p", { className: "checkout-status", attrs: { role: "status" } });
    const back = el("button", { type: "button", className: "btn btn-secondary", text: "戻る" });
    const confirm = el("button", { type: "button", className: "btn btn-primary btn-large", text: "架空購入を確定" });
    back.addEventListener("click", load);
    confirm.addEventListener("click", async () => {
      confirm.disabled = true; back.disabled = true;
      if (!state.checkoutRequestId) state.checkoutRequestId = uuidV4();
      try {
        const result = await client.checkout({ request_id: state.checkoutRequestId });
        state.checkoutOrder = result.order;
        clearCheckoutAttempt();
        router.go("checkout-complete", { idempotent_replay: result.idempotent_replay });
      } catch (error) {
        status.textContent = `${error.message} 再試行しても同じ架空購入試行として安全に処理します。`;
        confirm.disabled = false; back.disabled = false;
      }
    });
    const overlay = el("div", { className: "checkout-confirm-overlay", attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "checkout-confirm-title" } }, [
      el("section", { className: "checkout-confirm-card" }, [
        el("p", { className: "eyebrow", text: "最終確認" }),
        el("h2", { id: "checkout-confirm-title", text: "架空購入の内容を確認" }),
        lines,
        el("div", { className: "confirm-total" }, [el("span", { text: "合計" }), el("strong", { className: "money", text: formatMoney(data.total_amount) })]),
        el("div", { className: "simulation-notice", text: "これは架空の注文です。実際の決済・請求・配送は行われません。" }),
        status,
        el("div", { className: "actions actions-end" }, [back, confirm]),
      ]),
    ]);
    root.append(overlay);
  }

  function draw(data) {
    const items = el("section", { className: "cart-items panel" });
    if (data.lines.length === 0) items.append(el("div", { className: "empty-cart" }, [el("h3", { text: "カートは空です" }), el("p", { className: "muted", text: "商品一覧から気になる架空商品を追加してみましょう。" })]));
    const handlers = {
      update: async (line, quantity, button) => { button.disabled = true; try { await client.updateCartItem({ pack_id: line.pack_id, item_id: line.item_id, quantity }); clearCheckoutAttempt(); await load(); } catch (error) { replace(root, errorPanel(error.message, load)); } },
      remove: async (line, button) => { button.disabled = true; try { await client.removeCartItem({ pack_id: line.pack_id, item_id: line.item_id }); clearCheckoutAttempt(); await load(); } catch (error) { replace(root, errorPanel(error.message, load)); } },
      openDetail: (line) => router.go("product-detail", { pack_id: line.pack_id, item_id: line.item_id }),
    };
    for (const line of data.lines) items.append(cartLine(line, handlers));

    const checkout = el("button", { type: "button", className: "btn btn-primary btn-large checkout-button", text: "架空レジに進む", disabled: data.lines.length === 0 || data.has_unavailable });
    checkout.addEventListener("click", () => openConfirmation(data));
    const clear = el("button", { type: "button", className: "btn btn-danger-link", text: "カートを空にする", disabled: data.lines.length === 0 });
    clear.addEventListener("click", async () => { clear.disabled = true; try { await client.clearCart(); clearCheckoutAttempt(); await load(); } catch (error) { replace(root, errorPanel(error.message, load)); } });
    const summary = el("aside", { className: "order-summary panel" }, [
      el("h3", { text: "注文概要" }),
      el("div", { className: "summary-row" }, [el("span", { text: "小計" }), el("strong", { className: "money", text: formatMoney(data.total_amount) })]),
      el("div", { className: "summary-row" }, [el("span", { text: "架空送料" }), el("span", { text: "0 円" })]),
      el("div", { className: "summary-row" }, [el("span", { text: "商品点数" }), el("span", { text: `${data.total_quantity} 点` })]),
      el("div", { className: "summary-row total-row" }, [el("span", { text: "合計" }), el("strong", { className: "money", text: formatMoney(data.total_amount) })]),
      checkout,
      el("p", { className: "summary-disclaimer", text: "実際の決済・請求・配送は発生しません。" }),
    ]);
    if (data.has_unavailable) items.append(el("p", { className: "warning", text: "現在購入できない商品があります。内容を確認して削除してください。" }));
    const continueButton = el("button", { type: "button", className: "btn btn-secondary", text: "買い物を続ける" });
    continueButton.addEventListener("click", () => router.go("catalog"));
    replace(root,
      el("div", { className: "section-heading" }, [el("h2", { text: "ショッピングカート" }), continueButton]),
      el("div", { className: "cart-layout" }, [items, summary]),
      clear,
    );
  }
  await load();
}

export { uuidV4, enrichCartLines };
