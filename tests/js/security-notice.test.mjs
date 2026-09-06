import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.cwd();
function read(rel) { return fs.readFileSync(path.join(ROOT, rel), "utf8"); }
function allTextFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true })) {
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...allTextFiles(rel));
    else if (/\.(js|html|css)$/.test(entry.name)) out.push(rel);
  }
  return out;
}

test("startup notice contains approved scope and not blanket third-party AI claim", () => {
  const html = read("ui/index.html");
  assert.match(html, /ジョーク／デモ・シミュレーションアプリ/);
  assert.match(html, /実際の購入、決済、請求、配送は行われません/);
  assert.match(html, /制作者が公式に配布するサンプル商品コンテンツ/);
  assert.match(html, /AIを利用して生成された架空の商品/);
  assert.match(html, /第三者が作成・配布した追加パック/);
  assert.doesNotMatch(html, /すべての商品はAI生成/);
  assert.match(html, /role="dialog"/); assert.match(html, /aria-modal="true"/); assert.match(html, /了解して始める/);
});

test("notice blocks normal operation until acknowledge and is process-memory only", () => {
  const app = read("ui/js/app.js");
  assert.match(app, /setNormalUiEnabled\(false\)/);
  assert.match(app, /root\.inert = !enabled/);
  assert.match(app, /control\.disabled = !enabled/);
  assert.match(app, /notice\.hidden = true/);
  assert.match(app, /state\.noticeAcknowledged = true/);
  assert.doesNotMatch(app, /localStorage|sessionStorage|indexedDB/);
});

test("UI static audit has no executable HTML sinks, eval, external runtime URL or Money Number parsing", () => {
  const files = allTextFiles("ui");
  for (const rel of files) {
    const text = read(rel);
    assert.doesNotMatch(text, /innerHTML|outerHTML|insertAdjacentHTML/ , rel);
    assert.doesNotMatch(text, /\beval\s*\(|new Function/, rel);
    assert.doesNotMatch(text, /https?:\/\//, rel);
  }
  for (const rel of files.filter((f) => f.endsWith(".js"))) {
    const text = read(rel);
    assert.doesNotMatch(text, /\b(Number|parseInt|parseFloat)\s*\(/, rel);
  }
});

test("six screens remain and uninstall is confined to store-manager surface", () => {
  const required = ["catalog.js", "product-detail.js", "cart.js", "checkout-complete.js", "history.js", "packs.js"];
  for (const name of required) assert.ok(fs.existsSync(path.join(ROOT, "ui/js/screens", name)));
  const uiText = allTextFiles("ui").map(read).join("\n");
  assert.doesNotMatch(uiText, /SCR-07/);
  assert.match(read("ui/js/screens/packs.js"), /アンインストール/);
  assert.doesNotMatch(read("ui/index.html"), /data-route="packs"|パック管理|アンインストール/);
  assert.match(read("ui/index-store-manager.html"), /data-route="packs"|パック管理/);
  assert.match(read("ui/js/store-manager-bridge-client.js"), /uninstall_pack/);
  assert.doesNotMatch(read("ui/js/bridge-client.js"), /uninstall_pack/);
});

test("Bridge client exposes exactly the fixed named calls and no arbitrary-path import argument", () => {
  const client = read("ui/js/bridge-client.js");
  const expected = ["get_products", "get_categories", "get_product_detail", "get_cart", "add_to_cart", "update_cart_item", "remove_cart_item", "clear_cart", "checkout", "get_order_history", "get_order_detail", "get_statistics", "get_packs", "import_pack", "set_pack_enabled"];
  for (const name of expected) assert.match(client, new RegExp(`\\.${name}\\(`));
  assert.doesNotMatch(client, /search_products|update_cart_quantity|remove_from_cart|get_purchase_history/);
  assert.match(client, /import_pack\(\)/);
});
