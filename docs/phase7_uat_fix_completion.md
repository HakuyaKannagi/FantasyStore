# FantasyStore Phase 7 Windows UAT差戻し修正 完了記録

## 1. Overall Judgment

```text
UAT Fix Implementation: COMPLETE
Automated Tests: PASS
Windows Build after UAT fix: NOT EXECUTED
Windows UAT Recheck: NOT EXECUTED
Remaining Gate B: PRESENT
Release Candidate: NOT YET APPROVED
```

本修正環境はLinuxであり、Windows PowerShell 5.1 / PyInstaller Windows onedir / 実WebView2 UATを実行できない。そのため、差戻し後のWindows buildおよびWindows UAT再確認を推測でPASSとしていない。

差戻し前Windows UATで成立確認済みだったWindow/Notice/Bridge/Pack import/update/multi-Pack/image/Cart/Checkout/History/enable-disable等は、本修正でRegressionを壊さないよう実装・自動Testを維持した。ただし**修正版Windows buildでの再確認結果ではないため、再UAT結果としてPASSへ流用していない**。

---

## 2. Technical Fix

### 2.1 Windows PowerShell 5.1

`build/build_windows.ps1`のWindows判定を`$IsWindows`依存から以下相当へ変更した。

```powershell
[System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
```

Windows PowerShell 5.1 + `Set-StrictMode`で未定義変数にならない構造とした。

### 2.2 Python 3.11+ Build Guard

build開始前にactive `python`が3.11以上であることを確認し、満たさない場合はdependency install前に停止する。

### 2.3 Build Dependency

build scriptのinstall対象を以下3ファイルへ修正した。

- `requirements.txt`
- `requirements-dev.txt`
- `requirements-build.txt`

clean venvでも`pytest`が利用可能となる。

### 2.4 Node.js / npm Guard

JavaScript regression実行前提として`node`と`npm`の存在をbuild開始前に確認する。Node.js/npmは製品runtime dependencyではなく開発/build dependencyのまま維持した。

### 2.5 Native Exit Code

`Assert-NativeSuccess`を追加し、以下のnative command後に`$LASTEXITCODE`を明示確認する。

- Python version guard
- dependency install
- Python Test
- JavaScript Test
- PyInstaller
- distribution verification

Test failure後やbuild failure後に後続工程へ進まない。

### 2.6 PyInstaller Spec Root

`build/fantasy_store.spec`を以下へ修正した。

```python
project_root = Path(SPECPATH).parent.resolve()
```

旧`parent.parent`はRegression Testでも禁止した。

### 2.7 ManagedBridge Signature

`ManagedBridgeApi`の公開15 APIを`BridgeApi`と同じparameter count / parameter nameへ修正した。

引数なしAPI:

- `get_categories()`
- `get_cart()`
- `clear_cart()`
- `get_statistics()`
- `get_packs()`
- `import_pack()`

について、実呼出しRegression Testも追加した。

### 2.8 Raw pywebview Binding Error秘匿

JavaScript `bridge-client.js`でpywebview Promise自体がrejectした場合、raw Python class/method/TypeErrorを一般UIへそのまま渡さず、安全な`INTERNAL_ERROR`相当messageへ置換する境界を追加した。

固定15 APIは引き続きコード上で明示呼出しし、利用者入力によるdynamic dispatchは追加していない。

---

## 3. UI Fix

### 3.1 EC Visual Redesign

FantasyStore標準UIのみを、一般消費者向けECの共通視覚文法へ刷新した。

- Dark EC header
- FantasyStore brand / 架空ショッピング表示
- Global search bar
- White product cards
- Light page background
- 強調された価格とPrimary CTA
- Secondary / Danger action hierarchy
- desktop最大幅
- resize時のgrid/flex fallback
- horizontal overflow防御

Theme / Skin / Template切替は追加していない。

### 3.2 店長モード

Python起動引数`--store-manager`でUI resourceを選択する方式を実装した。

通常:

```text
FantasyStore.exe
→ ui/index.html
```

管理Mode:

```text
FantasyStore.exe --store-manager
→ ui/index-store-manager.html
```

通常HTMLにはPack管理nav自体が存在しない。Routerもnormal modeで`packs`直接遷移をcatalogへ戻す。

これは認証/Security Boundaryではなく表示Mode切替であり、Account/Auth/Role等は追加していない。

### 3.3 Empty States

normal modeの店舗文言を以下へ変更した。

- `NO_PACKS` → `現在商品がありません`
- `ALL_PACKS_DISABLED` → `現在購入できる商品がありません`
- `NO_SEARCH_RESULTS` → `条件に一致する商品がありません`

normal modeではPack追加/有効化等の内部管理導線を表示しない。店長モードのみ管理導線を出せる。

### 3.4 Money Unit

MoneyDTO / terms / MoneyValue / DB表現を変更せず、UI表示層で一貫して`円`を付加した。

適用範囲:

- Product list
- Product detail
- Cart unit price
- Cart line total
- Cart total / confirmation
- Purchase complete
- History / order detail
- Statistics total amount

### 3.5 Huge Money

MoneyDTO exact termsは不変のまま、UI `display`が18桁を超えるraw整数の場合のみ文字列演算でcompact指数表示へ変換する。

例:

```text
123456789012345678901234567890
→ 1.2345 × 10^29 円
```

既にBackendが指数表示している値はその表示を保持して`円`のみ付加する。

CSS側でも以下を実装した。

- `.product-card { min-width: 0; overflow: hidden; }`
- `.money { overflow-wrap: anywhere; word-break: break-word; }`
- image `max-width: 100%` / fixed card image area / `object-fit: contain`
- body horizontal overflow防御

### 3.6 Product Card / Product Detail

Cardの視覚順序を以下へ整理した。

1. image
2. product name
3. category
4. price
5. detail CTA

Product Detailもimage / product informationのEC型2-columnへ変更し、Main CTAを`カートに追加`として強調した。

### 3.7 Cart

desktopでは以下の2-column構成へ変更した。

```text
left: Cart items
right: Order Summary
```

Cart lineはimage/name/category/unit price/line total/quantity/removeを表示する。categoryはFreeze Cart DTOを変更せず、available itemのみ既存`get_product_detail()`から補助取得する。

Quantityは`− / number input / + / 数量更新`を提供し、既存1〜999 validationを維持する。

Order Summary:

- 小計
- 架空送料 0円（表示演出のみ）
- total quantity
- exact total

税/送料計算/Discount等のtransaction modelは追加していない。

### 3.8 Checkout Confirmation

Cart Primary CTAを`架空レジに進む`へ変更した。

押下時はDB Checkoutを実行せず、同一SCR-03上のModal相当confirmationを表示する。

確認内容:

- 商品名
- 数量
- line total
- order total
- 実決済なし
- 実請求なし
- 実配送なし

`request_id`はConfirmation内の`架空購入を確定`押下時に初めて生成する。一度Checkout開始後のretryは既存request IDを保持する。Cart mutation成功時は既存仕様どおりattemptを破棄する。

### 3.9 Purchase Complete

SCR-04を以下へ強化した。

- success icon
- `架空購入が完了しました！`
- 実請求/実決済/実配送なしの再明示
- Order ID / Purchase Time
- line cards
- total
- 固定`架空配送ステータス`
- `実際の配送は行われません`の併記
- `買い物を続ける`
- `購入履歴を見る`

架空配送は固定UI文言のみで、DB/state machine/delivery engineを追加していない。

### 3.10 History / Statistics

History一覧で各注文の代表商品名を表示するよう改善した。

Bridge DTOは変更せず、表示中pageの既存`get_order_detail()`を利用してpurchase-time snapshot情報を取得する。Current Pack `items_master` fallbackは行わない。

複数商品:

```text
代表商品名
ほかN商品
```

StatisticsはCard化し、以下を明示する。

- 累積購入金額: `円`
- 注文数: `件`
- 総購入点数: `点`

---

## 4. Automated Test

### Environment

```text
OS: Linux x86_64 (current implementation environment)
Python: 3.13.5
Node.js: 22.16.0
npm: 10.9.2
SQLite: 3.46.1
Pillow: 12.3.0
jsonschema: 4.26.0
```

### Result

```text
Python: 379 PASS
JavaScript / DOM: 37 PASS
Total: 416 PASS
FAIL: 0
```

`python -m compileall -q fantasy_store`: PASS

### UAT Fix新規/更新Regression

- spec repository root
- Windows PowerShell 5.1 guard static check
- Python 3.11 guard static check
- dev requirements / Node guard / native exit check
- ManagedBridge全15 API signature一致
- zero-arg API実呼出し
- Python `--store-manager`判定
- normal/admin UI resource選択
- normal route packs block
- normal header Pack admin hidden
- Money currency display
- huge Money compact display + CSS width guard
- Cart checkout confirmation
- confirmation前Checkout未実行
- confirmation後request_id生成
- retry same request_id
- Purchase Complete disclosure
- fixed fictional delivery message
- History representative item name
- NO_PACKS / ALL_PACKS_DISABLED user wording
- normal mode management term/direct action non-leak
- raw pywebview binding error UI秘匿

### Security Static Audit

PASS:

- no `innerHTML` / `outerHTML` / `insertAdjacentHTML`
- no `eval` / `new Function`
- no external HTTP(S) runtime UI resource
- no Money `Number()` / `parseInt()` / `parseFloat()` in UI
- normal `index.html` has no `packs` navigation
- XSS-safe textContent-based DOM preserved

---

## 5. Windows Build

### Pre-fix UAT evidence

Human Windows UAT採録上、差戻し前には以下が成立済み。

- PyInstaller onedir build
- Distribution static verification
- `FantasyStore.exe`起動
- pywebview Window / Notice
- Bridge roundtrip
- Pack 0
- native picker / cancel
- import / update / multi-Pack
- actual WebView2 product image
- Cart / Checkout / History
- enable / disable

### After UAT Fix

```text
Windows PowerShell 5.1 build: NOT EXECUTED
PyInstaller onedir after fix: NOT EXECUTED
Distribution verify after fix: NOT EXECUTED
```

理由: 本実装環境はLinuxであり、Windows PyInstaller cross-buildを実施して確認済み扱いにできないため。

修正版Sourceは以下でWindows build可能な構成まで準備済み。

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

---

## 6. Windows UAT Recheck

```text
Result: NOT EXECUTED in current environment
```

差戻し修正版build後、以下を最初から再確認する必要がある。

1. Window起動
2. Notice
3. Normal Mode Header
4. 店長モード非表示
5. `--store-manager`
6. Pack import
7. Product list
8. Product image
9. Currency display
10. Huge Money
11. Cart
12. Checkout confirmation
13. Purchase Complete
14. Fictional delivery
15. History summary
16. Pack disable state
17. Multi-Pack

詳細は`docs/windows_rc_checklist.md`参照。

---

## 7. Remaining Gate B

UAT UI再確認後も以下のTechnical Gate Bが残る。

- WebView2 image displayed during Pack update
- Windows image file lock
- second instance / `msvcrt.locking`
- Pack rename / replace
- Pack Recovery
- User DB backup restore
- recovery_hold
- log rotation
- SQLite STRICT in frozen runtime
- Pillow packaged
- jsonschema packaged
- offline
- CSP positive / negative
- external navigation
- clean shutdown / no zombie
- working directory variants
- Unicode / space path
- read-only program folder
- program folder replacement / data continuity
- Python未導入PC
- WebView2不足safe-stop

---

## 8. Phase 1〜7 Authority Preservation

以下は変更していない。

- exact Money internal representation
- MoneyDTO terms
- Checkout DB transaction
- Checkout idempotency
- Pack Lifecycle / complete replace
- Pack Recovery
- User DB Backup / Recovery
- Snapshot authority
- History snapshot authority
- Bridge API 15 names
- physical Pack delete policy
- offline principle
- real payment prohibition
- external API prohibition

Theme機能、Auth、Pack Store、delivery engine、tax/discount/inventory等も追加していない。

---

## 9. 正本逸脱

```text
正本逸脱なし
```

本Windows UAT差戻し指示書で明示されたHuman追加要求のみを正式反映した。

---

## 10. Required Next Step

```text
UAT Fix Implementation: COMPLETE
Automated Tests: PASS
Windows Build: NOT EXECUTED
Windows UAT Recheck: NOT EXECUTED
Remaining Gate B: PRESENT
```

Windows上で修正版Sourceをbuildし、`windows_rc_checklist.md`を再実施する。

**Release Candidate PASSはまだ宣言しない。**
