import { el } from "./dom.js";

export function packRow(pack, onToggle, onUninstall, registerButton = null) {
  const info = el("div", {}, [
    el("h3", { text: pack.name }),
    el("p", { className: "muted", text: `Pack ID: ${pack.pack_id}` }),
    el("p", { className: "muted", text: `Version ${pack.version}` }),
    el("p", { text: pack.author }),
    el("p", { text: pack.description }),
    el("p", { className: "pack-state", text: `状態: ${pack.enabled ? "有効" : "無効"}` }),
  ]);
  const toggle = el("button", { type: "button", className: "btn btn-secondary", text: pack.enabled ? "無効にする" : "有効にする", disabled: pack.busy });
  toggle.addEventListener("click", () => onToggle(pack, toggle));
  const uninstall = el("button", {
    type: "button",
    className: "btn btn-danger",
    text: "アンインストール",
    disabled: pack.busy || pack.enabled,
    attrs: { title: pack.enabled ? "アンインストールするには、先にこの商品パックを無効にしてください。" : "" },
  });
  uninstall.addEventListener("click", () => onUninstall(pack, uninstall));
  if (registerButton) { registerButton(toggle); registerButton(uninstall); }
  const controls = el("div", { className: "pack-actions" }, [
    pack.busy ? el("span", { className: "badge", text: "処理中" }) : null,
    toggle,
    uninstall,
    pack.enabled ? el("small", { className: "muted", text: "アンインストールするには、先にこの商品パックを無効にしてください。" }) : null,
  ]);
  return el("div", { className: "pack-row" }, [info, controls]);
}
