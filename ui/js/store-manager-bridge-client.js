import { bridgeClient, invokeBridge } from "./bridge-client.js";

function apiObject() {
  const api = globalThis.pywebview?.api ?? globalThis.__fantasyStoreBridge;
  if (!api) throw new Error("アプリ内部との通信を開始できません。");
  return api;
}

export const storeManagerBridgeClient = {
  ...bridgeClient,
  async uninstallPack(request) { return invokeBridge(() => apiObject().uninstall_pack(request)); },
  async resetPurchaseHistory() { return invokeBridge(() => apiObject().reset_purchase_history()); },
};
