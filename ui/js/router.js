import { beginRoute, state } from "./state.js";

const STORE_ROUTES = new Set(["catalog", "product-detail", "cart", "checkout-complete", "history"]);

export class Router {
  constructor(renderers, root, client, { storeManagerMode = false } = {}) {
    this.renderers = renderers;
    this.root = root;
    this.client = client;
    this.storeManagerMode = Boolean(storeManagerMode);
  }
  go(route, params = {}) {
    const allowed = STORE_ROUTES.has(route) || (route === "packs" && this.storeManagerMode);
    if (!allowed) { route = "catalog"; params = {}; }
    const generation = beginRoute(route, params);
    const renderer = this.renderers[route];
    renderer({ root: this.root, client: this.client, router: this, state, generation });
  }
}
