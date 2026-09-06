# 利用者向け架空ECシステム
# Phase 7 最終UX・ショップ管理・Snapshot容量制御 修正完了記録

- 作成日: 2026-09-06
- 対象: Phase 7 Windows UAT 最終修正
- Authority: `利用者向け架空ECシステム_Phase7_Windows_UAT_最終修正指示書_ショップ管理_履歴UI_Money_Snapshot容量制御`
- 判定: **Implementation COMPLETE / Automated Tests PASS / Release Candidate HOLD**

## 1. ショップ管理化

通常店長モードの管理導線名を `Pack管理` から **`ショップ管理`** へ変更した。

- 通常店舗: ショップ管理導線なし
- `--store-manager` 健全起動: ショップ管理あり
- 店長モード（復旧）: 復旧専用UIのみ。ショップ管理なし

ショップ管理画面を以下の2セクションへ整理した。

1. `商品Pack管理`
   - Pack導入
   - Version / state表示
   - enable / disable
   - formal uninstall
   - Version classification
2. `店舗データ管理`
   - `購入履歴をすべてリセット`

Pack Lifecycle / Version Policy / Uninstall Recoveryの既存意味論は変更していない。

## 2. 購入履歴全Reset

店長モード専用Bridgeへ `reset_purchase_history()` を追加した。通常店舗のFreeze済みBridge 15 APIには追加していない。

Reset対象:

- `orders`
- FK cascadeによる `order_items_snapshot`
- FK cascadeによる `purchase_requests`
- Purchase Statisticsの算出元注文データ
- `user_data/snapshots/images/{order_id}` のmanaged snapshot directory

Reset対象外:

- Cart
- user settings
- `installed_packs`
- `items_master`
- Pack journal / recovery evidence
- Pack enable / disable state

個別注文削除API/UIは実装していない。

## 3. Reset transaction / concurrency boundary

`HistoryMaintenanceService.reset_all()` を追加し、Checkoutと同じ `CartCheckoutCoordinator.exclusive()` を使用する。

処理境界:

```text
CartCheckoutCoordinator
→ user_data.db BEGIN IMMEDIATE
→ DELETE FROM orders
→ COMMIT
→ snapshot images best-effort filesystem cleanup
→ coordinator release
```

DBとfilesystemは単一ACID transactionとして扱っていない。

実thread RegressionでCheckoutとResetを同時実行し、相互に同時進行しないことを確認した。

このRegression追加時、CheckoutがDB COMMIT後にCoordinatorを解放してからOrder responseを生成していたため、Resetがその間に新規Orderを削除できる競合を検出した。Checkoutを、**COMMIT済みOrderのresponse materialize完了までCartCheckoutCoordinator内に保持**するよう修正した。DB transaction/idempotencyの意味論は変更していない。

## 4. 商品画像・商品名 detail link

現在の商品が安全に解決可能な場合に限り、`Pack ID + Item ID`を正式Identityとして以下を現在の商品詳細へリンクするよう変更した。

- Cartの商品画像
- Cartの商品名
- History listのpurchase snapshot画像 / 商品名
- History detailのpurchase snapshot画像 / 商品名

商品名文字列一致による推測は行わない。

## 5. History current-product unresolved時の扱い

History表示正本は従来どおり購入時Snapshotである。

現在Packがuninstall / disabled、Itemが存在しない等で現在商品詳細を安全に解決できない場合:

- 購入時snapshot画像 / 商品名は表示維持
- 商品詳細リンクなし
- 現在Pack画像へfallbackしない
- 購入時本文を現在値へ置換しない

を維持した。

## 6. Checkout confirmation単価表示

SCR-03上の最終確認dialogで各lineを以下へ統一した。

```text
商品名
単価 <Money> × <quantity>
小計 <Money>
```

Total計算はPhase 5のexact Money結果をそのまま使用する。

## 7. Purchase complete単価表示

SCR-04でも各lineを以下へ統一した。

```text
商品名
単価 <Money> × <quantity>
小計 <Money>
```

購入完了の架空購入・実請求/決済/配送なし表示は維持している。

## 8. History list画像表示 / `点 / 商品`

History listは**1 Order = 1 Card**を維持したまま、Order内の全lineをgallery表示する。

- 各lineの購入時Snapshot画像を1回表示
- quantity分の画像複製なし
- snapshot missing / retired時はhistory placeholder
- representative item name + `ほかN商品`
- 数量表記は `<total_quantity>点 / <line_count>商品`

例: `座布団×3 / 延長コード×2 / 月曜日×25` は `30点 / 3商品`。

## 9. Money指数formatter

Python Bridge側の共通 `money_display()` を更新した。

表示Policy:

- `value < 10^16`: exact integer + 3桁区切り
- `value >= 10^16`: human-readable exponent
- exponentはUnicode superscript (`10¹⁶`) を使用
- JSは `money.display` のみ表示し、Number / parseInt / parseFloatによるMoney正本化を行わない

例:

```text
3980     -> 3,980 円
12800    -> 12,800 円
9e3相当  -> 9,000 円
10^16    -> 1 × 10¹⁶ 円
```

## 10. 9桁係数 / 「約」条件

指数表示の係数は最大9桁。

- 9桁以内でexact表現可能: `約`なし
- 下位桁を9桁へ丸める必要がある場合のみ: `約`あり
- 丸めはdecimal leading digitによる決定的half-up表示丸め
- carry発生時は指数を正規化

Regression:

```text
123456789 × 10²⁵ -> exact / 約なし
1234567894 × 10²⁰ -> 約 123456789 × 10²¹
1234567896 × 10²⁰ -> 約 123456790 × 10²¹
```

MoneyValue / canonical terms / DB値 / Checkout / Statistics計算値は変更していない。

## 11. Search e notation説明

Catalog価格入力のmin/max付近へ固定helperを追加した。

```text
例: 123e4（= 123 × 10⁴）
```

invalid input messageも同じ意味説明を含む。

既存validation policyは緩和していない。

## 12. Snapshot 512 MiB Hard Limit

`SnapshotRetentionManager` を追加した。

製品定数:

```text
SNAPSHOT_HISTORY_HARD_LIMIT_BYTES = 536,870,912 bytes
```

対象は `user_data/snapshots/images/<order UUID>/` 配下のmanaged実ファイル総量。容量観測はDB参照済み画像だけに限定せず、Rollback cleanup失敗等で残ったmanaged orphanも実byteへ含める。

容量圧迫時は、購入履歴正本ではないmanaged orphanを先に退役し、その後DB参照済み履歴画像を注文時系列oldest-firstで退役する。これによりorphan維持のために正本履歴画像を先に犠牲にしない。

Retention実行:

1. Phase 5 startupの既存orphan cleanup後
2. Checkout DB COMMIT成功後

Testでは `max_bytes` のみ小値を注入し、製品定数は512 MiBのまま検証した。

## 13. Retention oldest-first

age authorityはfilesystem mtimeではなく、`orders.purchased_at`を使用する。

決定順序:

```text
purchased_at ASC
order_id ASC
line_no ASC
```

実file sizeを観測し、上限を超える場合にoldest-firstで**画像ファイルだけ**退役する。

同一Order内の複数lineもline orderで決定的に処理する。

## 14. Retention後placeholder / no Pack fallback

Retentionでsnapshot image fileが退役しても、DBのOrder / OrderLine snapshot metadataは保持する。

HistoryService既存境界により:

- image missing -> `snapshot_image_available=false`
- UI -> bundled history placeholder
- current Pack image fallbackなし

となる。

History本文・商品名・価格・description・attributes・quantity・Statisticsは保持する。

## 15. History Reset時Snapshot cleanup

全履歴ResetのDB COMMIT後に、managed UUID order snapshot directoryをbest-effort削除する。

- 正常時: snapshot image使用量0
- 削除失敗: DB履歴を巻き戻さない
- 残存artifactは後続のsnapshot cleanupで回収可能

## 16. Python Test

最終実行:

```text
453 PASS / 0 FAIL
```

Phase 1〜7既存Testを削除・skip・xfailしていない。

追加Testには以下を含む。

- Money threshold / superscript / 9 digit / rounding
- Money exact terms非変更
- 512 MiB製品定数
- retention oldest-first
- 同一Order line order
- managed orphanもHard Limit実byteへ算入し、正本画像より先に容量回収
- image-only retirement / history保持
- History Reset DB atomicity / Cart・settings保持
- Reset / Checkout coordinator待機
- Checkout後retention
- manager-only Reset Bridge
- startup retention
- 実Checkout vs Reset thread serialization

## 17. JavaScript / DOM Test

最終実行:

```text
47 PASS / 0 FAIL
```

追加/更新Test:

- ショップ管理mode境界
- 店舗データ管理 / Reset confirmation
- Cart image/name detail link
- Checkout confirmation unit price
- Purchase Complete unit price
- History order gallery / `点 / 商品`
- History current-item conditional link
- History placeholder no current fallback
- search e notation helper
- Money unit / giant layout policy

## 18. compileall

```text
PYTHONPATH=. python -m compileall -q fantasy_store tests
PASS
```

## 19. Windows Build status

**本最終修正版Source: NOT EXECUTED**

直前Source以前のWindows build実績を、本Sourceのbuild PASSへ読み替えていない。

## 20. Windows UAT status

**本最終修正版Source: NOT EXECUTED / DEFERRED**

本SourceをWindows buildした後に、指示書第48節の優先UATを最初から確認する。

## 21. Remaining Gate B

**PRESENT**

少なくとも以下を継続確認する。

- WebView2 formal Probe / missing safe-stop
- second instance / `msvcrt.locking`
- Pack update中画像file lock
- Windows rename / replace
- Pack Recovery / Uninstall Recovery
- User DB backup restore / recovery_hold
- log rotation
- SQLite STRICT frozen runtime
- Pillow / jsonschema packaged
- offline
- CSP positive / negative
- external navigation block
- clean shutdown / zombie process
- cwd variants
- Unicode / space path
- read-only program folder
- program folder replacement / persistent data continuity
- Python未導入PC（実施可能環境）

## 22. Release Candidate status

```text
Release Candidate: HOLD
```

Windows build / UAT / Remaining Gate B / 独立レビュー完了前にPASS扱いしない。

## 23. 正本逸脱

```text
正本逸脱なし
```

本指示書で明示されたショップ管理、履歴全Reset、UI導線、Money表示、Snapshot 512 MiB RetentionはHuman承認済み追加要求として実装した。

既存の以下は維持している。

- Pack Version / classification
- Historical generation relevance
- formal Uninstall / Recovery
- 店長モード（復旧）のSafety Gating
- normal boot fail closed
- Checkout transaction / idempotency
- Money exactness
- History snapshot authority
- User DB backup / recovery_hold
- Freeze Bridge API 15 names
- normal storefrontから管理機能を隠す境界
- `--store-manager`

## Overall Judgment

```text
Phase 7 Final UX / Store Management / Snapshot Retention Fix Implementation: COMPLETE
Automated Tests: 500 PASS / 0 FAIL
compileall: PASS
Windows Build: NOT EXECUTED
Windows UAT: NOT EXECUTED / DEFERRED
Remaining Gate B: PRESENT
Release Candidate: HOLD
```

=== DOCUMENT END ===
