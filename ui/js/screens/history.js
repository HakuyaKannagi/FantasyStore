import { el, errorPanel, imageUrlFromRef, loadingPanel, replace } from "../components/dom.js";
import { formatMoney } from "../money.js";
import { isCurrentGeneration } from "../state.js";

async function loadOrderPreviews(client, orders) {
  if (typeof client.getOrderDetail !== "function") return new Map();
  const pairs = await Promise.all(orders.map(async (summary) => {
    try {
      const detail = await client.getOrderDetail({ order_id: summary.order_id });
      return [summary.order_id, detail.order];
    } catch {
      return [summary.order_id, null];
    }
  }));
  return new Map(pairs);
}

function identityKey(line) { return `${line.pack_id}\u0000${line.item_id}`; }

async function resolveCurrentProductAvailability(client, lines) {
  const result = new Map();
  if (typeof client.getProductDetail !== "function") return result;
  const unique = new Map();
  for (const line of lines) unique.set(identityKey(line), line);
  await Promise.all([...unique.entries()].map(async ([key, line]) => {
    try {
      await client.getProductDetail({ pack_id: line.pack_id, item_id: line.item_id });
      result.set(key, true);
    } catch {
      result.set(key, false);
    }
  }));
  return result;
}

function snapshotImage(line, available, router, className = "history-image") {
  const image = el("img", {
    className,
    src: line.snapshot_image_available && line.snapshot_image_ref
      ? imageUrlFromRef(line.snapshot_image_ref)
      : "./assets/history-image-missing.png",
    alt: line.snapshot_image_available ? `${line.name}の購入時画像` : `${line.name}の購入時画像は利用できません`,
  });
  if (!available) return image;
  const button = el("button", { type: "button", className: "product-media-link history-product-link", attrs: { "aria-label": `${line.name}の現在の商品詳細を見る` } }, [image]);
  button.addEventListener("click", () => router.go("product-detail", { pack_id: line.pack_id, item_id: line.item_id }));
  return button;
}

function snapshotName(line, available, router, className = "") {
  if (!available) return el("strong", { className, text: line.name });
  const button = el("button", { type: "button", className: `text-link ${className}`.trim(), text: line.name });
  button.addEventListener("click", () => router.go("product-detail", { pack_id: line.pack_id, item_id: line.item_id }));
  return button;
}

export async function renderHistory({ root, client, router, generation }) {
  let page = 1;
  async function load() {
    replace(root, loadingPanel("購入履歴を読み込んでいます…"));
    try {
      const [stats, history] = await Promise.all([client.getStatistics(), client.getOrderHistory({ page, page_size: 24 })]);
      const previews = await loadOrderPreviews(client, history.orders);
      const allLines = [...previews.values()].flatMap((order) => order?.lines ?? []);
      const current = await resolveCurrentProductAvailability(client, allLines);
      if (!isCurrentGeneration(generation)) return;
      const summary = el("section", { className: "stats-grid" }, [
        el("article", { className: "stat-card" }, [el("span", { text: "累積購入金額" }), el("strong", { className: "money", text: formatMoney(stats.total_amount) })]),
        el("article", { className: "stat-card" }, [el("span", { text: "注文数" }), el("strong", { text: `${stats.order_count} 件` })]),
        el("article", { className: "stat-card" }, [el("span", { text: "総購入点数" }), el("strong", { text: `${stats.total_quantity} 点` })]),
      ]);
      const list = el("section", { className: "history-list" });
      if (history.orders.length === 0) list.append(el("div", { className: "empty", text: "購入履歴はまだありません。" }));
      for (const order of history.orders) {
        const detail = previews.get(order.order_id);
        const lines = detail?.lines ?? [];
        const representative = lines[0]?.name ?? "注文商品";
        const extra = order.line_count > 1 ? `ほか${order.line_count - 1}商品` : "";
        const button = el("button", { type: "button", className: "btn btn-secondary", text: "注文詳細を見る" });
        button.addEventListener("click", () => showDetail(order.order_id, detail));
        const gallery = el("div", { className: "history-card-gallery" });
        for (const line of lines) {
          const available = current.get(identityKey(line)) === true;
          gallery.append(el("div", { className: "history-card-product" }, [
            snapshotImage(line, available, router, "history-card-image"),
            snapshotName(line, available, router, "history-card-product-name"),
          ]));
        }
        list.append(el("article", { className: "history-card" }, [
          el("div", { className: "history-card-main" }, [
            el("time", { className: "muted", text: new Date(order.purchased_at).toLocaleString() }),
            lines[0]
              ? el("h3", {}, [snapshotName(lines[0], current.get(identityKey(lines[0])) === true, router, "history-representative-name")])
              : el("h3", { text: representative }),
            extra ? el("p", { className: "muted", text: extra }) : null,
            el("p", { className: "history-quantity-summary", text: `${order.total_quantity}点 / ${order.line_count}商品` }),
            gallery,
          ]),
          el("div", { className: "history-card-total" }, [el("strong", { className: "money", text: formatMoney(order.total_amount) }), button]),
        ]));
      }
      const pagination = el("div", { className: "pagination" });
      const prev = el("button", { type: "button", className: "btn btn-secondary", text: "前へ", disabled: page <= 1 });
      const next = el("button", { type: "button", className: "btn btn-secondary", text: "次へ", disabled: page * history.page_size >= history.total });
      prev.addEventListener("click", () => { page -= 1; return load(); });
      next.addEventListener("click", () => { page += 1; return load(); });
      pagination.append(prev, el("span", { text: `${page} / ${Math.max(1, Math.ceil(history.total / history.page_size))}` }), next);
      replace(root, el("h2", { text: "購入履歴・統計" }), summary, list, pagination);
    } catch (error) { if (isCurrentGeneration(generation)) replace(root, errorPanel(error.message, load)); }
  }

  async function showDetail(orderId, prefetched = null) {
    replace(root, loadingPanel("注文詳細を読み込んでいます…"));
    try {
      const order = prefetched ?? (await client.getOrderDetail({ order_id: orderId })).order;
      const current = await resolveCurrentProductAvailability(client, order.lines ?? []);
      const lines = el("section", { className: "order-detail-card" });
      for (const line of order.lines) {
        const available = current.get(identityKey(line)) === true;
        lines.append(el("article", { className: "history-detail-line" }, [
          snapshotImage(line, available, router),
          el("div", {}, [
            snapshotName(line, available, router, "history-detail-name"),
            el("p", { className: "product-category", text: line.category }),
            el("p", { text: line.description }),
            el("p", { className: "muted", text: `単価 ${formatMoney(line.unit_price)} × ${line.quantity}` }),
            el("p", { className: "money", text: `小計 ${formatMoney(line.line_total)}` }),
          ]),
        ]));
      }
      const back = el("button", { type: "button", className: "btn btn-secondary", text: "履歴一覧へ戻る" });
      back.addEventListener("click", load);
      replace(root,
        el("div", { className: "section-heading" }, [el("h2", { text: "購入履歴詳細" }), back]),
        el("section", { className: "panel order-meta-card" }, [
          el("div", {}, [el("span", { className: "meta-label", text: "購入日時" }), el("strong", { text: new Date(order.purchased_at).toLocaleString() })]),
          el("div", {}, [el("span", { className: "meta-label", text: "合計" }), el("strong", { className: "money", text: formatMoney(order.total_amount) })]),
        ]),
        lines,
      );
    } catch (error) { replace(root, errorPanel(error.message, load)); }
  }
  await load();
}

export { loadOrderPreviews, resolveCurrentProductAvailability };
