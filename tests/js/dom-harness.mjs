export class FakeElement {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentElement = null;
    this.className = "";
    this._text = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.inert = false;
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.src = "";
    this.alt = "";
    this.type = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent ?? "").join(""); }
  set innerHTML(_) { throw new Error("innerHTML is forbidden in UI tests"); }
  append(...nodes) {
    for (const node of nodes) {
      if (node === null || node === undefined) continue;
      if (typeof node === "string") {
        const text = new FakeElement("#text"); text.textContent = node; text.parentElement = this; this.children.push(text);
      } else { node.parentElement = this; this.children.push(node); }
    }
  }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(fn); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  focus() { globalThis.document.activeElement = this; }
  closest(selector) {
    if (selector === "button[data-route]" && this.tagName === "BUTTON" && this.dataset.route) return this;
    return this.parentElement?.closest?.(selector) ?? null;
  }
  get valueAsNumber() { return Number(this.value); }
  async dispatch(type, extra = {}) {
    const event = { target: this, currentTarget: this, preventDefault() {}, key: undefined, ...extra };
    const results = (this.listeners.get(type) ?? []).map((fn) => fn(event));
    await Promise.all(results.map((r) => Promise.resolve(r)));
  }
}

export function installFakeDom() {
  const document = {
    activeElement: null,
    createElement(tag) { return new FakeElement(tag); },
  };
  globalThis.document = document;
  return document;
}

export function findAll(root, predicate) {
  const out = [];
  function walk(node) {
    if (predicate(node)) out.push(node);
    for (const child of node.children ?? []) walk(child);
  }
  walk(root); return out;
}

export function findByText(root, text) {
  return findAll(root, (node) => node._text === text)[0] ?? null;
}

export function deepText(root) { return root.textContent; }
