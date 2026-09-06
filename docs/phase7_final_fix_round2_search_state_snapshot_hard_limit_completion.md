# 利用者向け架空ECシステム
# Phase 7 Windows UAT 最終修正（第2回）完了記録
# 検索条件保持・価格入力Helper整理・Snapshot Hard Limit確実化

- 作成日: 2026-09-06
- Authority: `利用者向け架空ECシステム_Phase7_Windows_UAT_最終修正指示書_第2回_検索条件保持_SnapshotHardLimit確実化`
- 判定: **Implementation COMPLETE / Automated Tests PASS / Release Candidate HOLD**

## 1. min/max検索条件保持

Catalogの最低価格・最高価格について、検索Request用`MoneyLiteral`とは別にHuman入力文字列を画面内stateとして保持するよう変更した。

例:

```text
Human input: 123e4
Request: {significand:"123", exponent:"4"}
検索後input: 123e4
```

keyword / category / sortの既存保持も維持している。

## 2. Human入力文字列保持方式

`renderCatalog()`内にフォーム表示stateを持ち、検索submit時に以下を保存してからRequest DTOへ変換する。

- query
- category
- minPriceText
- maxPriceText
- sort

結果再描画ではRequestのcanonical Moneyから逆変換せず、`minPriceText / maxPriceText`をそのままinput valueへ戻す。

そのため`123e4`が`1230000`等へ勝手に変換されない。

## 3. clear / invalid時の挙動

- min/maxを空にして再検索 -> Requestは`null`、inputも空
- invalid / negative -> validation policyは従来どおりreject
- validation error時 -> Human入力文字列をinputに残し、画面全体を破壊せず修正可能

アプリ再起動後や別screenを跨ぐ永続保存は追加していない。

## 4. Helper 1箇所化

従来min/maxそれぞれの下に重複していたHelperを削除し、価格条件全体へ1箇所だけ表示する。

```text
指数形式も入力できます。例: 123e4（= 123 × 10⁴）
```

invalid error message内の例示は別扱いとして維持している。

## 5. Snapshot Hard Limit enforcement方式

製品定数は変更していない。

```text
SNAPSHOT_HISTORY_HARD_LIMIT_BYTES = 536,870,912
```

`SnapshotRetentionManager`へCheckout前の`ensure_capacity(required_bytes)`を追加した。

Checkout snapshot flow:

```text
Pack read locks保持
→ purchase-time image source validation
→ 新規Order画像一式のexact source bytes観測
→ managed snapshot current bytes観測
→ max - required をtargetとしてorphan-first / history oldest-first prune
→ capacity available ?
    YES: Order画像一式をpendingへcopyし従来Lifecycle
    NO:  新規Order画像を一切書かずDB本文だけcommit
```

managed capacity観測が不完全な場合も、画像 admissionはfail closedする。

## 6. capacity unavailable時のCheckout挙動

Snapshot容量確保不能だけを理由としてCheckout全体を失敗させない。

保持:

- Order
- OrderLine snapshot metadata（商品名、description、category、attributes等）
- unit price / quantity / line total / order total
- Purchase Statistics
- Checkout idempotency

未保存:

- 新規Orderのpurchase-history Snapshot画像（全line）

DBの`primary_image_snapshot_path / sha256`は全lineとも`NULL`となるためHistoryServiceは通常の`history placeholder`へ倒れる。現在Pack画像へのfallbackはない。

## 7. new Order snapshot skip方式

Order単位でcapacity判定し、必要容量を確保できない場合はそのOrderの全画像をskipする。

`line1だけ保存 / line2以降skip`のような偶然依存の部分保存は行わない。

Pack画像はcopy前にImageValidatorでvalidationし、既存の「購入時source image自体が不正ならCheckoutをreject」の意味論は維持した。Hard Limitだけがimage-only degradation対象である。

## 8. already-over-limit時のgrowth stop

起動時点またはCheckout時点でmanaged bytesが既に上限を超えており、削除I/O failure等で上限未満へ戻せない場合:

```text
既存bytes: そのまま（消せないものを成功扱いしない）
新規Order本文: commit可能
新規Snapshot画像: write禁止
```

したがって異常filesystem下でも新しいSnapshot書込みによる無制限成長を止める。

正常filesystem条件ではstable-state invariant:

```text
managed snapshot bytes <= 536,870,912
```

を維持する。

## 9. History placeholder / no current Pack fallback

容量Policyで新規Snapshot画像を保存しなかった場合、既存History境界により:

```text
snapshot_image_available = false
snapshot_image_ref = null
```

となりbundled history placeholderを使用する。

Current Pack imageを購入時画像としてfallbackする処理は追加していない。

## 10. Logging

以下を区別可能にした。

- `SNAPSHOT_RETENTION_PRUNE` — 個別image退役
- `SNAPSHOT_RETENTION_PRUNE_COMPLETE` — prune pass成功
- `SNAPSHOT_RETENTION_DELETE_FAILED` — filesystem削除失敗
- `SNAPSHOT_RETENTION_ALREADY_OVER_LIMIT` — admission前から上限超過
- `SNAPSHOT_RETENTION_CAPACITY_UNAVAILABLE` — 新規Order容量確保不能
- `SNAPSHOT_RETENTION_NEW_ORDER_SKIPPED` — 新規Order snapshot全画像skip
- `SNAPSHOT_RETENTION_OBSERVE_FAILED` — 容量観測不完全

## 11. Python Test

最終実行:

```text
458 PASS / 0 FAIL
```

直前453件を全維持し、5件追加した。

追加Regression:

- normal capacity -> whole-order snapshot保存
- capacity pressure -> oldest-first prune後に新Order画像保存
- delete I/O failure -> Checkout成功 / new snapshot writeなし / History placeholder
- already over limit + prune failure -> new snapshot growthなし
- capacity observation incomplete -> image admission fail closed / Checkout本文成功

既存Testのdelete / skip / xfailなし。

## 12. JavaScript / DOM Test

最終実行:

```text
51 PASS / 0 FAIL
```

直前47件を全維持し、4件追加した。

追加Regression:

- keyword/category/sort + raw min/maxの再描画後保持
- min/max clear後Request/input双方が空
- invalid price入力を消さず修正可能
- e notation通常Helperが1箇所だけ

## 13. Total Test算術

```text
Python: 458
JavaScript / DOM: 51
Total: 509 PASS / 0 FAIL
```

`458 + 51 = 509`をREADME / 本Completion / Source summaryへ同期した。

## 14. compileall

```text
python -m compileall -q fantasy_store tests
PASS
```

## 15. README等文書Test count同期

最新版READMEの冒頭・Automated Tests節を以下へ統一した。

```text
Python 458 PASS
JavaScript / DOM 51 PASS
Total 509 PASS / 0 FAIL
```

直前READMEに存在した`Total 499`の算術不整合は解消した。

旧Phase完了記録はhistorical recordとして当時の実績を保持し、本最新版のcurrent summaryとして扱わない。

## 16. Windows Build status

```text
Windows Build for this latest source: NOT EXECUTED
```

Linux実装環境のため実行していない。過去build PASSを最新版へ読み替えない。

## 17. Windows UAT status

```text
Windows UAT for this latest source: NOT EXECUTED / DEFERRED
```

次回Windows build後、本指示書第32節の優先UATから継続する。

## 18. Remaining Gate B

```text
PRESENT
```

既存ChecklistのRemaining Gate Bを省略しない。

## 19. Release Candidate status

```text
Release Candidate: HOLD
```

Windows Build / Windows UAT / Remaining Gate B / Independent Review完了前にPASS扱いしない。

## 20. 正本逸脱

```text
正本逸脱なし
```

本指示書で明示承認された限定追加要求のみを実装した。

Human確認済みの以下は変更していない。

- History Gallery / 商品画像＋商品名 / `点 / 商品`
- ショップ管理構成 / History全Reset
- Pack enable/disable / formal uninstall
- 店長モード（復旧） / Safe Maintenance
- Pack Version Policy
- Money 9桁係数 / superscript / `約`
- Checkout exactness / idempotency
- History snapshot authority / no current Pack fallback
- Freeze Bridge 15 API
- normal storefront管理導線なし
- `--store-manager`

## Overall Judgment

```text
Phase 7 Final Fix Round 2 Implementation: COMPLETE
Automated Tests: 509 PASS / 0 FAIL
compileall: PASS
Windows Build: NOT EXECUTED
Windows UAT: NOT EXECUTED / DEFERRED
Remaining Gate B: PRESENT
Independent Review: NOT EXECUTED
Release Candidate: HOLD
```

=== DOCUMENT END ===
