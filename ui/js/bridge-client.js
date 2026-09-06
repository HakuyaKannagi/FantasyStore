function apiObject() {
  const api = globalThis.pywebview?.api ?? globalThis.__fantasyStoreBridge;
  if (!api) throw new Error("アプリ内部との通信を開始できません。");
  return api;
}

function validateEnvelope(response) {
  if (!response || typeof response !== "object" || typeof response.ok !== "boolean") {
    throw new Error("Bridge response is malformed");
  }
  if (response.ok) {
    if (response.error !== null || response.data === null || typeof response.data !== "object") {
      throw new Error("Bridge success response is malformed");
    }
    return response.data;
  }
  if (response.data !== null || !response.error || typeof response.error.code !== "string" || typeof response.error.message !== "string") {
    throw new Error("Bridge error response is malformed");
  }
  const error = new Error(response.error.message);
  error.code = response.error.code;
  throw error;
}

async function invokeBridge(call) {
  let response;
  try {
    response = await call();
  } catch {
    const error = new Error("アプリ内部との通信に失敗しました。もう一度お試しください。");
    error.code = "INTERNAL_ERROR";
    throw error;
  }
  return validateEnvelope(response);
}

export const bridgeClient = {
  async getProducts(request) { return invokeBridge(() => apiObject().get_products(request)); },
  async getCategories() { return invokeBridge(() => apiObject().get_categories()); },
  async getProductDetail(request) { return invokeBridge(() => apiObject().get_product_detail(request)); },
  async getCart() { return invokeBridge(() => apiObject().get_cart()); },
  async addToCart(request) { return invokeBridge(() => apiObject().add_to_cart(request)); },
  async updateCartItem(request) { return invokeBridge(() => apiObject().update_cart_item(request)); },
  async removeCartItem(request) { return invokeBridge(() => apiObject().remove_cart_item(request)); },
  async clearCart() { return invokeBridge(() => apiObject().clear_cart()); },
  async checkout(request) { return invokeBridge(() => apiObject().checkout(request)); },
  async getOrderHistory(request) { return invokeBridge(() => apiObject().get_order_history(request)); },
  async getOrderDetail(request) { return invokeBridge(() => apiObject().get_order_detail(request)); },
  async getStatistics() { return invokeBridge(() => apiObject().get_statistics()); },
  async getPacks() { return invokeBridge(() => apiObject().get_packs()); },
  async importPack() { return invokeBridge(() => apiObject().import_pack()); },
  async setPackEnabled(request) { return invokeBridge(() => apiObject().set_pack_enabled(request)); },
};

export { validateEnvelope, invokeBridge };
