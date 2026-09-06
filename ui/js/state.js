export const state = {
  noticeAcknowledged: false,
  storeManagerMode: false,
  route: "catalog",
  routeParams: {},
  routeGeneration: 0,
  checkoutRequestId: null,
  checkoutOrder: null,
};

export function beginRoute(route, params = {}) {
  state.route = route;
  state.routeParams = params;
  state.routeGeneration += 1;
  return state.routeGeneration;
}

export function isCurrentGeneration(generation) {
  return generation === state.routeGeneration;
}

export function clearCheckoutAttempt() {
  state.checkoutRequestId = null;
}
