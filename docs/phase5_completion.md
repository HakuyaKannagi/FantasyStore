# FantasyStore MVP Phase 5 完了記録

## 1. 判定

**Phase 5 — Application Services / Cart / Checkout / History: PASS候補**

- Test: **315 PASS / 0 FAIL**
- Phase 1〜4 regression: **238 PASS**
- Phase 5 new tests: **77 PASS**
- Python: 3.13.5
- SQLite: 3.46.1
- Phase 6未着手
- **正本逸脱なし**

本PhaseはFreeze済み正本のPhase 5境界のみを実装し、Bridge / UI / pywebview / packagingへ進んでいない。

---

## 2. 実装概要

### 2.1 新規ファイル

```text
fantasy_store/application/
  __init__.py
  models.py
  cart_checkout_coordinator.py
  locks.py
  catalog_service.py
  cart_service.py
  checkout_service.py
  history_service.py
  stats_service.py
  pack_service.py

fantasy_store/snapshot/
  __init__.py
  manager.py
  cleanup.py

tests/
  phase5_helpers.py
  test_bootstrap_phase5.py
  test_phase5_catalog_cart.py
  test_phase5_checkout_history_stats.py
  test_phase5_concurrency_failures_extra.py
```

### 2.2 変更ファイル

- `fantasy_store/domain/errors.py`
  - Freeze済みcodeを表現するApplication向けDomain Error classを追加。
  - 新規codeの独自追加はしていない。
- `fantasy_store/persistence/user_repository.py`
  - DB-only責務のまま、Cart数量加算transaction、Stats用order totals取得、snapshot参照一覧を追加。
  - 既存`commit_order_bundle()`をCheckoutで再利用。
- `fantasy_store/persistence/pack_repository.py`
  - Application読取用にinstalled/enabled件数、item取得、dynamic categories、exact price/search queryを追加。
  - Lifecycle / FS判断は追加していない。
- `fantasy_store/bootstrap.py`
  - `Phase5Runtime` / `bootstrap_phase5()`を追加。
  - Phase 4 bootstrap順序を再実装せず、その完了後にsnapshot orphan cleanupとApplication Service構築を追加。
- `README.md`
  - Phase 5状態へ更新。

### 2.3 Phase 1〜4既存コードへの影響

追加・拡張のみ。以下は変更していない。

- DDL
- Money表現 / 演算
- SQLite connection / PRAGMA
- AppInstanceLock
- user_data Recovery / migration
- `.vpack` validation
- Pack Lifecycle state machine
- Pack Recovery decision rule
- PackAccessCoordinator writer-preference実装
- manifest rebuild方式

既存Phase 1〜4 test 238件は全PASS。

---

## 3. 主要Class / Function

### Catalog

- `CatalogService.get_products()`
- `CatalogService.get_categories()`
- `CatalogService.get_product_detail()`
- `PackRepository.query_enabled_items()`

`get_products()`が検索・category・price range・sort・paginationを兼ね、別`search_products()`は追加していない。

検索は`LIKE ? ESCAPE '\'`のparameter bindingを使用し、`%`, `_`, `\`をliteral escapeする。ORDER BYは固定whitelist。Money price range/sortは`price_magnitude + price_sort_digits`のみを使用し、INTEGER/REAL価格へ縮退していない。

### Cart / Lock

- `CartCheckoutCoordinator.exclusive()`
- `multi_pack_read_locks()`
- `CartService.add_to_cart()`
- `update_cart_item()`
- `remove_cart_item()`
- `clear_cart()`
- `get_cart()`

CheckoutおよびCart writeのLock順序は、

```text
CartCheckoutCoordinator
→ PackAccessCoordinator
→ SQLite transaction
```

を維持する。

### Checkout

- `CheckoutService.checkout()`
- `UserRepository.commit_order_bundle()`（Phase 2実装を再利用）

新規Checkoutは、Cart lock取得後に`purchase_requests`を再確認し、Pack ID昇順ですべてのRead Lockを取得してから商品状態を再取得する。Snapshot copy / validation / SHA-256 / final切替まで同一Pack Read Lock内で行い、注文DB COMMITまで保持する。

### Snapshot

- `SnapshotManager.prepare_pending()`
- `SnapshotManager.finalize()`
- `SnapshotManager.validate_history_image()`
- `SnapshotOrphanCleaner.cleanup()`

DBへ保存するpathは、

```text
snapshots/images/{order_id}/{line_no}.{validated_ext}
```

という`user_data`相対path。Application結果は`pack-asset:` / `snapshot:`のopaque internal referenceを使い、絶対OS pathを持たせない。

### History / Stats

- `HistoryService.get_order_history()`
- `HistoryService.get_order_detail()`
- `StatsService.get_statistics()`

履歴は`orders / order_items_snapshot`だけから構築し、`items_master`へJOINしない。StatsのMoneyとtotal quantityはPython側でaggregateする。

### Pack

- `PackService.get_packs()`
- `PackService.set_pack_enabled()`
- `PackService.import_pack_from_native_path()`

Enable/DisableはPhase 4 `PackLifecycleManager`へdelegateし、Lock / manifest state backup境界を迂回しない。import path境界は内部Python APIのみで、Bridgeやfile dialogは未実装。

---

## 4. Test結果

Framework: `pytest`

```text
TOTAL                315 PASS / 0 FAIL
Phase 1〜4 regression 238 PASS
Phase 5 new            77 PASS
```

実行:

```bash
python -m pytest -q
```

既存238件は別実行でも全PASS。

### 4.1 Catalog

PASS:

- `NO_PACKS`
- `ALL_PACKS_DISABLED`
- `READY`
- `NO_SEARCH_RESULTS`
- Categories 0件 / DISTINCT / dynamic
- `%`, `_`, `\` literal search
- category filter
- exact Money min/max
- huge exponent
- name / price asc / desc
- pagination
- validation
- 更新中Packだけ`primary_image_ref=null`
- 他Pack継続取得
- Product Detail normal / disabled / missing / busy

### 4.2 Cart

PASS:

- add / quantity add
- 999境界
- 1000以上拒否・clampなし
- update / remove / clear
- nonexistent remove=false
- empty clear
- disabled add拒否
- Pack writer競合`PACK_BUSY`
- `PACK_DISABLED`
- `ITEM_NOT_FOUND`
- `PACK_NOT_INSTALLED`
- unavailable line保持
- available lineのみMoney totalへ加算

### 4.3 Money

PASS:

- zero / ordinary / huge exponent
- large exponent gap
- multiple line exact addition
- quantity multiplication
- sparse block count維持
- Catalog exact filter/sort
- Checkout exact total
- Statistics exact aggregate

---

## 5. Checkout Concurrency結果

| Scenario | Cart Lock | Pack Lock(s) | DB Transaction | Result |
|---|---|---|---|---|
| add vs checkout | Checkoutがexclusive保持。後着addは待機 | Checkout対象Pack Read | Checkoutのみ先行 | Checkout後addが実行され、新規Cart rowはDELETEに巻き込まれない |
| update/remove/clear vs checkout | 後着Cart writeは待機 | update/remove/clearは不要 | Checkout COMMIT後に後着処理 | Checkout途中のCart stateを変更しない |
| same request checkout | 2threadをexclusiveで直列化 | 先行だけ新規処理 | 先行1回のみ | order 1件、request 1件、snapshot 1世代。後着は同order replay |
| Pack update vs checkout（Checkout先行） | Checkout保持 | Checkout Read → update Write待機 | Checkout COMMITまでRead保持 | Checkoutは旧世代全体、updateはその後新版へ切替 |
| Pack update vs checkout（Update先行） | CheckoutはCart lockを取るがPack Readで待機 | Update Write先行 | Update後Checkout DB | Checkoutは新版テキスト・価格・画像の同一世代 |
| multi-Pack checkout | exclusive | Pack ID昇順Read取得、逆順release | 全Lock取得後のみ開始 | deterministic order。中間timeout時は取得済みLock全解放・DB未開始 |
| unrelated Pack update | Checkout対象PackのみRead | 別Pack Writeは独立 | 相互に不要な待機なし | Pack B updateはPack A checkout中でも完了 |

Lock tracerで`add_to_cart`とCheckout双方について、SQLite transaction開始がPack Read取得後であることを確認した。

---

## 6. Snapshot Failure Matrix

| Failure Point | Pending State | Final State | DB State | Cart State | Recovery / Cleanup |
|---|---|---|---|---|---|
| pending mkdir failure | none | none | order none | keep | `FS_ACCESS_DENIED` / `FS_DISK_FULL`等 |
| source image missing/corrupt | cleanup対象 | none | order none | keep | `CHECKOUT_ITEM_UNAVAILABLE` |
| copy/read failure | partial possible | none | order none | keep | pending best-effort cleanup |
| copied image validation failure | partial possible | none | order none | keep | pending best-effort cleanup |
| SHA-256 failure | complete/partial possible | none | order none | keep | pending best-effort cleanup |
| pending→final failure | pending possible | none | order none | keep | pending best-effort cleanup |
| DB BEGIN failure | none | final exists then cleanup | no order | keep | final best-effort cleanup |
| order INSERT後failure | none | final exists then cleanup | ROLLBACK | keep | final cleanup |
| order items後failure | none | final exists then cleanup | ROLLBACK | keep | final cleanup |
| purchase_request後failure | none | final exists then cleanup | ROLLBACK | keep | final cleanup |
| cart DELETE後failure | none | final exists then cleanup | ROLLBACK | keep | final cleanup |
| rollback後snapshot cleanup failure | none | orphan残存 | no order | keep | 24h後orphan cleanup候補 |
| DB COMMIT後response loss | none | final valid | order/requestあり | cleared | same requestで同order replay |
| Checkout後backup failure | none | final valid | order/requestあり | cleared | Checkout success維持、`DB_BACKUP_FAILED` log |

---

## 7. Idempotency結果

### Sequential replay

```text
checkout(X) -> order A / idempotent=false
checkout(X) -> order A / idempotent=true
```

PASS。2回目はsnapshot再生成・Cart再削除・新order生成なし。

### Concurrent replay

2threadで同じ`request_id`を同時呼出し。

結果:

```text
orders             1
purchase_requests   1
final snapshot dir  1
returned order_id   same
```

PASS。

### DB COMMIT後response loss

`after_db_commit`故障注入で呼出し側だけ失敗させた後、同一`request_id`を再送。

- DB order維持
- request維持
- final snapshot維持
- Cart clear維持
- 再送は既存order
- 追加orderなし

PASS。

---

## 8. History / Stats結果

### History

PASS:

- 0件正常
- pagination
- unknown order=`ORDER_NOT_FOUND`
- Pack update後も旧商品名・旧価格
- Pack disable後も履歴不変
- `items_master`非参照
- snapshot missing / unreadable / corrupt / digest mismatchでも本文維持
- 現在Pack画像へのfallbackなし

### Statistics

PASS:

- order 0件 -> Money zero / count 0 / quantity 0
- ordinary multiple order
- huge exponent
- exponent差が巨大なMoney exact sum
- SQLite signed 64bit SUMならoverflowし得る`total_quantity`組合せをPython任意精度で正しく集計
- MoneyをSQLite `SUM()`へ渡していない

---

## 9. Snapshot Orphan Cleanup

PASS:

- `.pending` <24h keep
- `.pending` >=24h / DB参照なし delete
- final <24h keep
- final >=24h / DB参照なし delete
- DB参照final keep
- DB参照画像がcorruptでもdirectory keep
- cleanup delete failureはwarning + artifact remain
- UUID v4 managed directory以外は自動削除しない

起動順序は、

```text
Phase 4 bootstrap
  AppInstanceLock
  user_data recovery/migration
  pack manifest health/rebuild
  unfinished Pack Recovery
  manifest consistency
↓
snapshot orphan cleanup
↓
Phase 5 Application Services ready
```

として成立。

---

## 10. PoC結果

開発環境: Linux / Python 3.13.5 / SQLite 3.46.1。

### 10.1 MoneyValue / multi-Pack Checkout

3 Pack・3 line、うち1価格を`exponent=9000000000000000000`として実Checkout。

観測:

```text
checkout elapsed     約0.0098 sec（参考値、保証値ではない）
order Money blocks   3
statistics blocks    3
snapshot files       3
```

巨大指数を10進0列へ展開せず疎表現を維持。

### 10.2 PackAccessCoordinator

Read Lock保持中に同Pack writer threadを開始。

```text
writer_waited_while_reader   true
writer_completed_after_release true
observed wait                約0.0506 sec（試験で50ms保持）
```

timeout / deterministic multi-lock / reverse releaseは自動Testで確認。

### 10.3 Snapshot filesystem

現開発環境で、

- pending copy
- image validation
- SHA-256
- pending directory → final directory rename
- history digest再確認
- orphan cleanup

が成立。

### 10.4 環境依存持越し

- Windows実機でWebView2がPack画像を開いた状態のfile lock挙動: Phase 6〜7へDEFERRED
- Windows実機`msvcrt.locking`: 既存持越し
- PyInstaller成果物SQLite STRICT: Phase 7
- WebView2 Runtime probe正式化 / worker thread通知: Phase 6〜7

---

## 11. Security / Boundary監査

静的確認:

- Phase 5コードに`float`/`Decimal` Money変換なし
- StatsにMoney SQLite SUMなし
- Historyに`items_master` JOIN / current Pack image fallbackなし
- absolute pathをApplication結果modelへ格納しない
- SQL search値はparameter binding
- ORDER BYはwhitelist
- Bridge / HTML / CSS / JS / pywebview未実装
- 実決済 / 税 / 送料 / 割引 / ポイント / 在庫 / network / AI / Wishlist未追加

---

## 12. 未実施項目

Phase 5の正本上必須の自動Test/PoCで、現開発環境内で実施可能な項目は実施済み。

環境依存のみ後続Phaseへ持越し:

- Windows + WebView2実画像file lock
- Windows packaging環境

これらはPhase 5完了を阻害しないと指示書で明示された範囲。

---

## 13. 正本逸脱

**正本逸脱なし**

実装不能・正本変更が必要な事項は発見していない。

専用エラーコードがFreezeされていない空Cart Checkoutは、新規codeを追加せず既存`CHECKOUT_ITEM_UNAVAILABLE`へ収束させた。

---

## 14. 残課題

### Phase 6へ持越し

- Bridge公開API
- `{ok,data,error}` envelope
- Freeze済みDTOへのJSON変換
- Money display派生文字列
- opaque image referenceのBridge resolver
- native file dialog
- HTML / CSS / JS UI
- Empty State実画面
- 「準備中★」placeholder実画面

### Phase 7へ持越し

- pywebview / WebView2実統合
- CSP
- PyInstaller onedir
- Windows実機 / Python未導入PC
- offline Release Candidate試験

### Known Issue / 技術的負債

Phase 5完了判定を妨げるKnown Issueなし。

---

## 15. 停止条件

Phase 6へは進んでいない。

**Phase 6開始判断をHumanへ返す。**
