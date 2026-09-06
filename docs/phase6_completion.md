# 利用者向け架空ECシステム MVP Phase 6 完了記録

## 1. 判定

**Phase 6 — Bridge API / UI: PASS（Phase 7実統合を除くPhase 6責務）**

- Phase 1〜5既存機能を維持
- Bridge固定API 15件成立
- Freeze DTO / MoneyDTO成立
- HTML/CSS/JavaScriptによるSCR-01〜SCR-06成立
- 2026-09-05 Human追加要求の起動時Notice成立
- XSS / Error leak / absolute path leak / arbitrary local path / external runtime network依存を防止
- 実pywebview / WebView2 / native dialog実接続 / CSP最終化 / packagingはPhase 7へ持越し
- **正本逸脱なし**

Human追加NoticeはPhase 6実装指示書で明示承認された追加要求として実装し、未承認逸脱とは扱っていない。

---

## 2. 実装概要

### 2.1 Bridge modules

新規:

```text
fantasy_store/bridge/
├─ __init__.py
├─ api.py
├─ response.py
├─ models.py
├─ money_mapper.py
├─ image_resolver.py
└─ file_picker.py
```

主要責務:

- `BridgeApi`
  - 15公開APIをコード上で明示
  - Request DTO型/field/enum/ID/Money/page/quantity validation
  - dynamic dispatchなし
  - JSから任意local pathを受けるmethodなし
- `response.py`
  - 共通envelope
  - Domain Error codeから利用者向け固定messageへ変換
  - 想定外Exceptionを`INTERNAL_ERROR`へ変換
  - raw exception / SQL / path / stackをResponseへ出さない
- `models.py`
  - Freeze DTOへfield固定変換
- `money_mapper.py`
  - canonical exact `MoneyValue.terms`を維持
  - 通常値は3桁区切り表示
  - 巨大値は指数式display
  - display短縮がtermsを変更しない
- `file_picker.py`
  - Phase 7 native picker接続用Protocol
  - deterministic test adapter
  - Phase 6 defaultは`DeferredFilePicker`
- `image_resolver.py`
  - opaque `pack-asset:` / `snapshot:` refを毎回再検証
  - Pack側は既存`AssetResolver`を再利用
  - Snapshot側はOrder ownership + snapshot validation + SHA-256を再確認
  - UI用`fantasy-image://resource/<percent-encoded opaque-ref>`境界
  - OS absolute pathをJSへ渡さない

### 2.2 UI modules

新規:

```text
ui/
├─ index.html
├─ css/app.css
├─ assets/
│  ├─ image-missing.png
│  ├─ image-preparing.png
│  └─ history-image-missing.png
└─ js/
   ├─ app.js
   ├─ bridge-client.js
   ├─ router.js
   ├─ state.js
   ├─ components/
   │  ├─ dom.js
   │  ├─ product-card.js
   │  ├─ empty-state.js
   │  ├─ cart-line.js
   │  └─ pack-row.js
   └─ screens/
      ├─ catalog.js
      ├─ product-detail.js
      ├─ cart.js
      ├─ checkout-complete.js
      ├─ history.js
      └─ packs.js
```

外部frontend frameworkは追加していない。

### 2.3 Test modules

新規:

```text
tests/test_bridge_phase6.py
tests/js/dom-harness.mjs
tests/js/bridge-ui-core.test.mjs
tests/js/screens.test.mjs
tests/js/security-notice.test.mjs
package.json
```

Node側はbuilt-in `node:test`のみで、npm package dependencyは0件。

### 2.4 Phase 1〜5既存コードへの変更

#### `fantasy_store/bootstrap.py`

- `Phase6Runtime`追加
- `bootstrap_phase6()`追加
- `ResourceLocator` import追加

理由:

- Phase 5完了後にBridge / image resolver / UI resourceを構築するPhase 6 runtime境界が必要。
- Phase 4/5 recovery順序は変更していない。

影響:

- `bootstrap_phase1/2/4/5()`の既存責務・順序・public interfaceは変更なし。
- `bootstrap_phase6()`は`bootstrap_phase5()`成功後のみ構築されるため、`RECOVERY_REQUIRED`等のsafe-stopを迂回しない。

#### `fantasy_store/main.py`

- 実行境界をPhase 2 readyからPhase 6 readyへ更新。
- 実pywebview Windowは起動しない。

理由:

- Repositoryの現在実装Phaseと実行smokeを一致させるため。

#### `pyproject.toml`

- descriptionのみPhase 6へ更新。
- dependency変更なし。

Phase 1〜5のMoney / DDL / Repository / Lock / Pack Lifecycle / Checkout / History / Stats実装は変更していない。

---

## 3. Bridge API Matrix

| API | Request DTO | Success DTO | Error Mapping | Application Service | Test |
|---|---|---|---|---|---|
| `get_products` | query/category/min/max/sort/page/page_size | items/total/page/page_size/catalog_state | Validation/INTERNAL | CatalogService | PASS |
| `get_categories` | なし | categories | INTERNAL | CatalogService | PASS |
| `get_product_detail` | pack_id/item_id | product | Validation/PRODUCT_NOT_AVAILABLE/PACK_BUSY | CatalogService | PASS |
| `get_cart` | なし | lines/total_amount/total_quantity/has_unavailable | PACK_BUSY/INTERNAL | CartService | PASS |
| `add_to_cart` | pack_id/item_id/quantity | line/cart_total_quantity | Validation/PRODUCT_NOT_AVAILABLE/CART_QUANTITY_LIMIT/PACK_BUSY | CartService | PASS |
| `update_cart_item` | pack_id/item_id/quantity | line | Validation/CART_QUANTITY_LIMIT/DB系 | CartService | PASS |
| `remove_cart_item` | pack_id/item_id | removed | Validation/DB系 | CartService | PASS |
| `clear_cart` | なし | cleared_count | DB系 | CartService | PASS |
| `checkout` | request_id | order/idempotent_replay | Validation/CHECKOUT_ITEM_UNAVAILABLE/PACK_BUSY/DB/FS | CheckoutService | PASS |
| `get_order_history` | page/page_size | orders/total/page/page_size | Validation | HistoryService | PASS |
| `get_order_detail` | order_id | order | Validation/ORDER_NOT_FOUND | HistoryService | PASS |
| `get_statistics` | なし | total_amount/order_count/total_quantity | INTERNAL | StatsService | PASS |
| `get_packs` | なし | packs/pack_state | INTERNAL | PackService | PASS |
| `import_pack` | **引数なし** | CANCELLED/IMPORTED/UPDATED + pack | Validation/Pack/FS/INTERNAL | Native picker → PackService | PASS |
| `set_pack_enabled` | pack_id/enabled | pack | Validation/PACK_BUSY/PACK_NOT_FOUND/RECOVERY_REQUIRED | PackService | PASS |

### 共通Response

Detailed Design 20.1を優先し、Error時は以下を返す。

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "...",
    "message": "利用者向け安全文",
    "details": null
  }
}
```

Phase 6指示書の簡略例には`details`がないが、Freeze済み詳細設計の追加fieldをAuthority優先順位に従って維持した。

---

## 4. DTO監査

### MoneyDTO

```text
{terms:[{significand:string, exponent:string}], display:string}
```

- PASS
- `terms`は`MoneyValue.to_canonical_obj()`と一致
- zero / normal / huge exponent / multi-termをTest
- JS側でMoneyを`Number/parseInt/parseFloat`へ変換しない

### ProductSummary

- `pack_id`
- `item_id`
- `name`
- `price`
- `category`
- `primary_image_ref`

**PASS**

### ProductDetail

ProductSummary +

- `description`
- `attributes`
- `image_refs`

**PASS**

### CartLine

- Freeze field 9件をexact field setでTest
- nullable Money / name / ref維持
- unavailable reason 3種維持

**PASS**

### PackSummary

- Pack ID / name / version / author / description / enabled / busy

**PASS**

### OrderSummary / OrderLine / OrderDetail

- exact field setをTest
- snapshot refとavailabilityを分離
- History current Pack fallbackなし

**PASS**

余計なinternal path / repository row / digest / DB fieldはBridge DTOへ出していない。

---

## 5. UI Screen Matrix

| Screen | Loading | Ready | Empty | Error | Processing | Main APIs |
|---|---|---|---|---|---|---|
| SCR-01 商品一覧 | ○ | ○ | 3状態 | ○ | 検索submit | get_products/get_categories |
| SCR-02 商品詳細 | ○ | ○ | N/A | ○ + 一覧復帰 | Add button disable | get_product_detail/add_to_cart |
| SCR-03 カート | ○ | ○ | Empty Cart | ○ | update/remove/clear/checkout disable | get_cart/cart CRUD/checkout |
| SCR-04 架空購入完了 | N/A | ○ | N/A | 前画面で処理 | N/A | Checkout結果 |
| SCR-05 履歴・統計 | ○ | ○ | 履歴0件 | ○ | detail load | statistics/history/detail |
| SCR-06 パック管理 | ○ | ○ | Pack 0件 | ○ | import/toggle disable | get_packs/import_pack/set_pack_enabled |

### SCR-01

- keyword
- dynamic category
- min/max MoneyLiteral入力
- sort
- pagination
- 商品詳細遷移
- Cart / Pack管理導線
- 更新中画像=`準備中★`

### SCR-02

- image/name/price/category/description/attributes/quantity/Add
- PRODUCT_NOT_AVAILABLE等で一覧復帰導線
- attributesをHTML解釈しない

### SCR-03

- unavailable 3種を区別
- unavailable lineを自動削除しない
- unavailable存在時Checkout抑止
- 「架空購入を確定」表記

### SCR-04

- 「架空購入完了」
- order ID / local display time / lines / total / quantity
- idempotent replayも同画面へ正常収束可能

### SCR-05

- exact total display / order count / total quantity
- History pagination
- Detail
- snapshot unavailableはlocal placeholder
- current Pack image fallbackなし

### SCR-06

- Pack summary
- import
- enable/disable
- busy Pack局所disable
- physical delete UIなし

---

## 6. Empty State結果

### `NO_PACKS`

表示:

> 商品パックがまだありません

導線:

> パックを追加

Error bannerなし。

### `ALL_PACKS_DISABLED`

表示:

> 商品パックがすべて無効です

導線:

> パックを有効化

Error bannerなし。

### `NO_SEARCH_RESULTS`

表示:

> 条件に一致する商品はありません

導線:

> 検索条件をリセット

Pack追加を主導線にしていない。

---

## 7. 起動時Notice結果

### 実表示文

```text
これは架空のショッピング体験を楽しむための
ジョーク／デモ・シミュレーションアプリです。

このアプリでは実際の購入、決済、請求、配送は行われません。

制作者が公式に配布するサンプル商品コンテンツは、
AIを利用して生成された架空の商品です。

第三者が作成・配布した追加パックについては、
その作成者が提供する内容をご確認ください。

［了解して始める］
```

### 表示タイミング

- UI module起動直後
- 通常screen render前
- 同一UI process/module lifetimeでは一度確認後に再表示しない
- localStorage/sessionStorage等へ「次回非表示」を保存しないため、次回Application UI起動では再表示可能

### 操作block

Notice前:

- main area `inert`
- header navigation disabled
- 商品/Cart/Checkout/Pack API screen自体をまだ起動しない
- modalは`role=dialog`, `aria-modal=true`
- Tab focusをNotice buttonへ閉じ込める
- Escapeで閉じない

### AI生成Scope

- 公式サンプルに限定してAI生成を明示
- 「すべての商品はAI生成」と表示しない
- Third-party Packは作成者情報を確認する旨だけ表示

**PASS**

---

## 8. Security Validation

### XSS

Test fixture:

```text
<script>alert(1)</script>
<img src=x onerror=alert(1)>
"><svg onload=alert(1)>
```

結果:

- Product name/category/attributes/Pack name等で文字列として描画
- executable DOM nodeへ変化しない
- Fake DOM harnessは`innerHTML`使用時に即Failする構成

**PASS**

### HTML sink static audit

対象:

- `innerHTML`
- `outerHTML`
- `insertAdjacentHTML`

結果: **0件**

### JS execution sink

対象:

- `eval`
- `new Function`

結果: **0件**

### Python dynamic dispatch

Bridge対象:

- `getattr(user_input)`
- `eval`
- `exec`
- `__import__`

結果: **0件**

### SQL / stack / path leak

全非picker公開APIへDomain Error / unexpected Exceptionを故障注入。

Responseに以下が出ないことを確認:

- raw SQL
- `/tmp/...`
- raw exception type
- stack trace相当

**PASS**

### Absolute path leak

DTOはopaque image refのみ。
LocalImageResolverはOS `Path`を内部で返すがBridge DTOへserializeしない。

**PASS**

### Image resolver escape

拒否確認:

- `../`
- absolute
- drive path相当
- network URL
- malformed ref
- wrong Pack
- snapshot invalid UUID
- unknown scheme
- `file://`
- `https://`

**PASS**

### Arbitrary local path

`BridgeApi.import_pack()` signatureは`self`のみ。
JS Bridge Clientも`import_pack()`引数なし。

**PASS**

### External network dependency

HTML/CSS/JS sourceへ`http://` / `https://` runtime resourceなし。
CDN/font/analytics/telemetry/external imageなし。

**PASS**

### Money JS Number非依存

UI JS sourceへ以下なし:

- `Number(`
- `parseInt(`
- `parseFloat(`

Money表示は`money.display`。

**PASS**

---

## 9. File Picker Boundary

Phase 6:

- `FilePicker` Protocol
- `DeterministicFilePicker`
- `DeferredFilePicker`

Test:

- cancel → `ok=true,status=CANCELLED,pack=null`
- valid `.vpack` → `IMPORTED`
- same Pack ID → `UPDATED`
- invalid extension/path → Validation failure
- adapter exception → `INTERNAL_ERROR`, path leakなし

実pywebview native dialogは**未実装／Phase 7**。

---

## 10. Local Image Resolver

Pack image:

```text
opaque ref
→ validate pack_id/ref
→ existing AssetResolver
→ managed installed asset
```

Snapshot:

```text
opaque ref
→ validate order UUID / filename
→ order snapshot ownership確認
→ managed snapshot root
→ image validate + SHA-256
```

UI側:

```text
opaque ref
→ fantasy-image://resource/<encoded-ref>
```

Phase 6ではresolver/URL boundaryまで。
実WebView2 resource handlerへの登録はPhase 7。

---

## 11. Test結果

### Python

Framework:

```text
pytest 9.0.2
```

最終:

```text
359 PASS
0 FAIL
```

内訳:

- Phase 1〜5 regression: **315 PASS**
- Phase 6 Python new: **44 PASS**

Phase 6 Python主項目:

- 全15公開API success
- exact DTO field sets
- MoneyDTO zero/normal/huge/multi-term
- Request Validation
- 全非picker API Domain Error変換
- 全非picker API unexpected Exception秘匿
- import cancel/import/update/invalid picker/failure
- arbitrary path非公開signature
- pack/snapshot image resolver
- resolver escape/network/file scheme拒否
- `bootstrap_phase6()` explicit/default ResourceLocator

### JavaScript / DOM

Runtime:

```text
Node.js 22.16.0
npm 10.9.2
node:test
```

最終:

```text
28 PASS
0 FAIL
```

外部npm dependency: **0**

### 合計

```text
387 PASS
0 FAIL
```

---

## 12. Failure Injection

| Failure | Expected | Result |
|---|---|---|
| Bridge DTO/service Domain Error | safe code/message | PASS |
| unexpected Service exception | INTERNAL_ERROR / no raw detail | PASS |
| file picker failure | INTERNAL_ERROR / no path leak | PASS |
| malformed Bridge request | VALIDATION_INVALID_ARGUMENT | PASS |
| malformed Bridge response | JS safe failure | PASS |
| malformed UI render data | screen ERRORへ移行 | PASS |
| image resolver invalid/escape | resolver reject | PASS |
| import invalid selection | no import / validation error | PASS |
| Checkout first attempt failure | request_id保持 | PASS |
| Checkout retry | same request_id | PASS |

Cart mutation成功時は古いfailed Checkout attempt IDを破棄し、変更済みCartが既存request_id replayへ誤収束しないようにした。

---

## 13. UI State / Concurrency-facing behavior

- Loading: screen data取得中にLoading panel
- Processing:
  - Add to Cart button disabled
  - Cart update/remove/clear disabled at operation boundary
  - Checkout button disabled
  - Pack import中はPack add + enable + disableを同時disable
  - Pack busyは対象Pack toggleのみdisable
- Checkout:
  - button押下時にUUID v4生成
  - failure時ID保持
  - retryで同ID
  - successでID破棄
  - Cart内容変更成功時は旧attempt ID破棄
- stale route:
  - route generation tokenで旧async resultを別screenへ描画しない

---

## 14. PoC / Smoke

### Python compile

```text
python -m compileall -q fantasy_store
```

PASS。

### Phase 6 process boundary

一時persistent rootで:

```text
python -m fantasy_store.main
```

最終結果:

```text
exit 0
```

Bridge/UI resource readinessまで到達し、実pywebview Windowは起動しない。

このsmokeで一度、default `ResourceLocator`使用時のimport不足を検出したため修正し、専用regression testを追加した。

### UI test environment

Node built-in test runnerでlocal ES modules / deterministic Fake DOMを実行。
実WebView2ではないため、WebView2特有挙動はPhase 7へ持越し。

---

## 15. Phase 7持越し

以下は指示書どおり未実装／未完了扱い。

1. 実pywebview Window起動
2. WebView2 Runtime正式Probe
3. Windows実WebView2確認
4. `fantasy-image://` custom-resource handler実接続
5. WebView2で画像を開いた状態のPack file lock確認
6. worker thread → UI通知実統合
7. CSP最終統合
8. pywebview native file dialog adapter実接続
9. PyInstaller onedir
10. Python未導入Windows実機
11. offline Release Candidate実機確認
12. WebView2 offline installer再配布条件確認

Phase 6 test adapter/browser harnessをPhase 7実統合完了とは扱わない。

---

## 16. Known Issue / 技術的負債

### Phase 6内Known Issue

重大Known Issueなし。

### 意図的なPhase 7境界

- UIの`fantasy-image://` URLはPhase 6 resolver contractであり、実WebViewへのprotocol/resource handler接続前は一般browserで画像表示できない。
- Default `DeferredFilePicker`は実dialogを開かず、実native adapterはPhase 7で接続する。
- `DB_BACKUP_FAILED`はPhase 5で注文成功後の非致命warningとしてlogされる。Freeze Checkout Bridge DTOへwarning fieldは存在しないため、Phase 6ではDTOを拡張してUI warningを追加していない。Phase 6指示書第69節は「必要なら」表示可能としており、未承認DTO変更を避ける方を選択した。

---

## 17. 正本逸脱

**正本逸脱なし**

- Freeze DTOを変更していない。
- MoneyをNumber化していない。
- Bridgeへ任意path APIを追加していない。
- Pack削除UIを追加していない。
- external network/frontend frameworkを追加していない。
- starter/official sample Packを必須同梱・特権化していない。
- Human追加NoticeはPhase 6指示書で正式に承認された追加実装要件として実装した。

---

## 18. 停止

**Phase 7へ自動進行していない。**

Phase 6実装一式、Bridge/API Test、UI Test、Screen Matrix、Security監査、本完了記録を提出し、

> **Phase 7開始判断をHumanへ返す**

ところで停止する。

=== DOCUMENT END ===
