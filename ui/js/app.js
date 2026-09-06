import { bridgeClient } from "./bridge-client.js";
import { storeManagerBridgeClient } from "./store-manager-bridge-client.js";
import { Router } from "./router.js";
import { state } from "./state.js";
import { renderCatalog } from "./screens/catalog.js";
import { renderProductDetail } from "./screens/product-detail.js";
import { renderCart } from "./screens/cart.js";
import { renderCheckoutComplete } from "./screens/checkout-complete.js";
import { renderHistory } from "./screens/history.js";
import { renderPacks } from "./screens/packs.js";

const root = document.getElementById("app-main");
const notice = document.getElementById("notice-overlay");
const accept = document.getElementById("notice-accept");
const globalSearch = document.getElementById("global-search-form");
const globalSearchInput = document.getElementById("global-search-input");
const storeManagerMode = document.documentElement.dataset.storeManager === "true";
state.storeManagerMode = storeManagerMode;
const activeBridgeClient = storeManagerMode ? storeManagerBridgeClient : bridgeClient;

const router = new Router({
  catalog: renderCatalog,
  "product-detail": renderProductDetail,
  cart: renderCart,
  "checkout-complete": renderCheckoutComplete,
  history: renderHistory,
  packs: renderPacks,
}, root, activeBridgeClient, { storeManagerMode });

function setNormalUiEnabled(enabled) {
  root.inert = !enabled;
  for (const control of document.querySelectorAll("[data-normal-control]")) control.disabled = !enabled;
}

function acknowledgeNotice() {
  state.noticeAcknowledged = true;
  notice.hidden = true;
  setNormalUiEnabled(true);
  root.focus();
  router.go("catalog");
}

setNormalUiEnabled(false);
accept.addEventListener("click", acknowledgeNotice);
notice.addEventListener("keydown", (event) => {
  if (event.key === "Tab") { event.preventDefault(); accept.focus(); }
  if (event.key === "Escape") event.preventDefault();
});
accept.focus();

document.querySelector("header").addEventListener("click", (event) => {
  if (!state.noticeAcknowledged) return;
  const button = event.target.closest("button[data-route]");
  if (button) router.go(button.dataset.route);
});

globalSearch.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.noticeAcknowledged) return;
  router.go("catalog", { query: globalSearchInput.value });
});

export { acknowledgeNotice, setNormalUiEnabled, router, storeManagerMode };
