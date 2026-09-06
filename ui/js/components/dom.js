export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.type) node.type = options.type;
  if (options.alt !== undefined) node.alt = String(options.alt);
  if (options.src) node.src = options.src;
  if (options.disabled !== undefined) node.disabled = Boolean(options.disabled);
  if (options.value !== undefined) node.value = String(options.value);
  if (options.name) node.name = options.name;
  if (options.id) node.id = options.id;
  if (options.htmlFor) node.htmlFor = options.htmlFor;
  for (const [key, value] of Object.entries(options.attrs ?? {})) node.setAttribute(key, String(value));
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

export function replace(root, ...nodes) { root.replaceChildren(...nodes); }

export function errorPanel(message, retry) {
  const panel = el("section", { className: "error", attrs: { role: "alert" } }, [
    el("strong", { text: "処理できませんでした" }),
    el("p", { text: message || "処理に失敗しました。" }),
  ]);
  if (retry) {
    const button = el("button", { type: "button", text: "再試行" });
    button.addEventListener("click", retry);
    panel.append(button);
  }
  return panel;
}

export function loadingPanel(text = "読み込み中…") {
  return el("div", { className: "loading", text, attrs: { role: "status" } });
}

export function imageUrlFromRef(ref) {
  if (typeof ref !== "string" || ref.length === 0) return null;
  return `fantasy-image://resource/${encodeURIComponent(ref)}`;
}
