import { el, errorPanel, loadingPanel, replace } from "../components/dom.js";
import { emptyState } from "../components/empty-state.js";
import { packRow } from "../components/pack-row.js";
import { isCurrentGeneration } from "../state.js";

function importMessage(result) {
  if (result.status === "DOWNGRADE_SKIPPED") {
    return `現在のバージョン ${result.installed_version} より古い ${result.incoming_version} が選択されたため、導入をスキップしました。`;
  }
  if (result.classification === "UPGRADE") {
    return `商品パックをアップグレードしました（${result.installed_version} → ${result.incoming_version}）。`;
  }
  if (result.classification === "REINSTALL") {
    return `Version ${result.incoming_version} を同一バージョン再導入しました。`;
  }
  return "商品パックを導入しました。";
}

export async function renderPacks({ root, client, router, generation }) {
  let processing = false;
  async function load(message = "") {
    replace(root, loadingPanel("ショップ管理を読み込んでいます…"));
    try {
      const data = await client.getPacks();
      if (!isCurrentGeneration(generation)) return;
      draw(data, message);
    } catch (error) { if (isCurrentGeneration(generation)) replace(root, errorPanel(error.message, load)); }
  }

  function confirmUninstall(pack) {
    const cancel = el("button", { type: "button", className: "btn btn-secondary", text: "キャンセル" });
    const confirm = el("button", { type: "button", className: "btn btn-danger", text: "アンインストール" });
    const card = el("section", { className: "panel uninstall-confirm", attrs: { role: "dialog", "aria-modal": "true" } }, [
      el("h2", { text: "商品パックのアンインストール確認" }),
      el("p", { text: "この商品パックをアンインストールします。" }),
      el("h3", { text: pack.name }),
      el("p", { text: `Pack ID: ${pack.pack_id}` }),
      el("p", { text: `Version: ${pack.version}` }),
      el("p", { text: "このPackの商品は商品一覧から削除されます。過去の購入履歴は削除されません。" }),
      el("div", { className: "dialog-actions" }, [cancel, confirm]),
    ]);
    cancel.addEventListener("click", () => load());
    confirm.addEventListener("click", async () => {
      if (processing) return;
      processing = true;
      cancel.disabled = true; confirm.disabled = true;
      try {
        await client.uninstallPack({ pack_id: pack.pack_id });
        processing = false;
        await load("商品パックをアンインストールしました。購入履歴は保持されています。");
      } catch (error) {
        processing = false;
        replace(root, errorPanel(error.message, load));
      }
    });
    replace(root, card);
  }

  function confirmHistoryReset() {
    const cancel = el("button", { type: "button", className: "btn btn-secondary", text: "キャンセル" });
    const confirm = el("button", { type: "button", className: "btn btn-danger", text: "購入履歴をすべてリセット" });
    const card = el("section", { className: "panel uninstall-confirm", attrs: { role: "dialog", "aria-modal": "true" } }, [
      el("h2", { text: "購入履歴をすべてリセット" }),
      el("p", { text: "購入履歴をすべてリセットします。" }),
      el("p", { text: "削除されるもの：注文履歴、購入時の商品情報、購入統計、購入履歴用の保存画像" }),
      el("p", { text: "削除されないもの：商品Pack、カート、設定" }),
      el("p", { className: "warning", text: "この操作は元に戻せません。" }),
      el("div", { className: "dialog-actions" }, [cancel, confirm]),
    ]);
    cancel.addEventListener("click", () => load());
    confirm.addEventListener("click", async () => {
      if (processing) return;
      processing = true; cancel.disabled = true; confirm.disabled = true;
      try {
        const result = await client.resetPurchaseHistory();
        processing = false;
        const warning = result.failed_snapshot_directories
          ? ` DB上の履歴はリセットしましたが、保存画像 ${result.failed_snapshot_directories} 件は後続cleanup対象です。`
          : "";
        await load(`購入履歴をすべてリセットしました。${warning}`);
      } catch (error) {
        processing = false;
        replace(root, errorPanel(error.message, load));
      }
    });
    replace(root, card);
  }

  function draw(data, message) {
    const operationButtons = [];
    const add = el("button", { type: "button", className: "btn btn-primary", text: "商品パックを導入", disabled: processing });
    operationButtons.push(add);
    add.addEventListener("click", async () => {
      if (processing) return; processing = true;
      for (const button of operationButtons) button.disabled = true;
      try {
        const result = await client.importPack();
        processing = false;
        if (result.status === "CANCELLED") { await load(); return; }
        await load(importMessage(result));
      } catch (error) { processing = false; replace(root, errorPanel(error.message, load)); }
    });

    const packPanel = el("section", { className: "panel shop-management-section" }, [
      el("div", { className: "section-heading" }, [el("h2", { text: "商品Pack管理" }), add]),
    ]);
    if (message) packPanel.append(el("p", { attrs: { role: "status" }, text: message }));
    if (data.packs.length === 0) packPanel.append(emptyState("まだ商品パックがありません", "商品一覧へ戻る", () => router.go("catalog")));
    for (const pack of data.packs) packPanel.append(packRow(
      pack,
      async (target, button) => {
        button.disabled = true;
        try { await client.setPackEnabled({ pack_id: target.pack_id, enabled: !target.enabled }); await load(); }
        catch (error) { replace(root, errorPanel(error.message, load)); }
      },
      (target) => confirmUninstall(target),
      (button) => operationButtons.push(button),
    ));

    const reset = el("button", { type: "button", className: "btn btn-danger", text: "購入履歴をすべてリセット" });
    reset.addEventListener("click", confirmHistoryReset);
    const dataPanel = el("section", { className: "panel shop-management-section" }, [
      el("h2", { text: "店舗データ管理" }),
      el("p", { className: "muted", text: "購入履歴・購入統計・購入履歴用画像をまとめて削除します。商品Pack、カート、設定は残ります。" }),
      reset,
    ]);

    replace(root, el("h1", { text: "ショップ管理" }), packPanel, dataPanel);
  }
  await load();
}
