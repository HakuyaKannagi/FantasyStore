import { el, replace } from "../components/dom.js";
import { formatMoney } from "../money.js";

export function renderCheckoutComplete({ root, router, state }) {
  const order = state.checkoutOrder;
  if (!order) { router.go("history"); return; }
  const lines = el("section", { className: "order-detail-card" });
  for (const line of order.lines) {
    lines.append(el("div", { className: "order-line-card" }, [
      el("div", {}, [
        el("strong", { text: line.name }),
        el("p", { className: "money muted", text: `単価 ${formatMoney(line.unit_price)} × ${line.quantity}` }),
      ]),
      el("strong", { className: "money", text: `小計 ${formatMoney(line.line_total)}` }),
    ]));
  }
  const history = el("button", { type: "button", className: "btn btn-primary btn-large", text: "購入履歴を見る" });
  history.addEventListener("click", () => router.go("history"));
  const catalog = el("button", { type: "button", className: "btn btn-secondary", text: "買い物を続ける" });
  catalog.addEventListener("click", () => router.go("catalog"));
  replace(root,
    el("section", { className: "success-hero" }, [
      el("div", { className: "success-icon", text: "✓", attrs: { "aria-hidden": "true" } }),
      el("h2", { text: "架空購入が完了しました！" }),
      el("p", { text: "架空の注文が完了しました。実際の請求・決済・配送は行われません。" }),
    ]),
    el("section", { className: "panel order-meta-card" }, [
      el("div", {}, [el("span", { className: "meta-label", text: "注文ID" }), el("strong", { className: "breakable", text: order.order_id })]),
      el("div", {}, [el("span", { className: "meta-label", text: "購入日時" }), el("strong", { text: new Date(order.purchased_at).toLocaleString() })]),
    ]),
    lines,
    el("section", { className: "panel delivery-card" }, [
      el("h3", { text: "📦 架空配送ステータス" }),
      el("p", { text: "ただいま商品は時空の狭間を通過中です。" }),
      el("p", { text: "到着予定：パラレルワールド時間で明日。" }),
      el("p", { className: "simulation-notice", text: "これは固定の架空演出です。実際の配送は行われません。" }),
    ]),
    el("section", { className: "panel complete-total" }, [
      el("span", { text: `合計商品点数 ${order.total_quantity} 点` }),
      el("strong", { className: "money", text: `合計 ${formatMoney(order.total_amount)}` }),
    ]),
    el("div", { className: "actions actions-end" }, [catalog, history]),
  );
}
