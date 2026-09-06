import { el } from "./dom.js";
export function emptyState(message, actionLabel, action) {
  const box = el("section", { className: "empty" }, [el("p", { text: message })]);
  if (actionLabel && action) {
    const button = el("button", { type: "button", text: actionLabel });
    button.addEventListener("click", action);
    box.append(button);
  }
  return box;
}
