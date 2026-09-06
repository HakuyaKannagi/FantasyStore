# FantasyStore Phase 7 Windows UAT再々々差戻し修正 完了記録
# 店長モード（復旧）視認性・安全保守操作

## 1. Overall Judgment

```text
Recovery UI / Safe Maintenance Fix: COMPLETE

Automated Tests:
Python 441 PASS
JavaScript / DOM 40 PASS
Total 481 PASS / 0 FAIL

compileall: PASS

Previous Windows Build: PASS
Previous Windows UAT: FAIL / STOP
Reason:
- 店長モード（復旧）HTMLに明示配色がなく実WebView2で文字が視認困難
- Legacy Version Packのcurrent generationが安全確定済みでも診断専用で退役経路がなかった

Windows Build for this latest source: NOT EXECUTED
Windows UAT for this latest source: NOT EXECUTED / DEFERRED
Remaining Gate B: PRESENT
Release Candidate: HOLD
```

Windows実機再確認なしにRelease Candidate PASSとは判定しない。

---

## 2. Recovery UI暗転の確定原因

直前Sourceの`fantasy_store/runtime/store_manager_recovery.py`はRecovery HTMLを直接生成していたが、CSPは概ね次であった。

```text
style-src 'none'
script-src 'none'
```

HTML自身にもbackground / foreground等の固定CSSがなかった。

Windows実WebView2ではsurface/theme条件により暗い背景とHTML既定文字色の組合せとなり、通常状態では文字がほぼ読めず、文字選択時のみ内容が確認できた。

modal / overlay / hidden dialogではなく、Recovery専用HTML自身の明示style不足が原因である。

---

## 3. CSPを維持したstyle / script適用方式

Recovery画面は引き続き外部resourceを利用しない。

固定CSSと固定JavaScriptをPython module内の定数として保持し、生成時にSHA-256を計算してCSPへexact hashを設定する。

概念:

```text
style-src 'sha256-<fixed-style-hash>'
script-src 'sha256-<fixed-script-hash>'
default-src 'none'
img-src 'none'
connect-src 'none'
object-src 'none'
frame-src 'none'
base-uri 'none'
form-action 'none'
```

禁止事項を維持:

- `unsafe-inline`なし
- `unsafe-eval`なし
- `*`なし
- HTTP / HTTPS external resourceなし
- filesystem URLなし
- remote script/css/imageなし

Recovery操作用JavaScriptは固定sourceであり、Pack由来値をJavaScript sourceへ連結しない。Pack ID / name / VersionはHTML attributeとしてescape後にdataとして渡し、表示更新は`textContent`を使用する。

---

## 4. Recovery UI視認性修正

明示した主なUI規則:

- page background: light gray
- foreground: dark text
- heading: dark blue-gray
- table header: light contrasting surface
- cell border / separator
- warning surface
- Safe Maintenance / Diagnostic Onlyの状態色
- `width:100%`
- `border-collapse:collapse`
- `vertical-align:top`
- `overflow-wrap:anywhere`
- `word-break:break-word`
- narrow windowではtable wrapper内のみhorizontal scroll

長いcategory / UUID / Pack ID / messageが隣セルへ重ならないことをHTML regressionで確認した。

---

## 5. Safety Level / operation gating

Persistent journal stateは追加していない。

startup diagnosticへ観測証拠を追加し、概念的に以下を区別する。

### Diagnostic Only

例:

- `PACK_RECOVERY_REQUIRED`
- `PACK_INSTALLED_DB_MISMATCH`
- unknown generation
- digest observation failure
- unresolved recovery

許可:

```text
状態確認のみ
```

禁止:

```text
disable
uninstall
import
upgrade
reinstall
Cart
Checkout
```

### Safe Maintenance

現在対象はHuman承認済みのLegacy Version incompatibilityのみ。

category名だけでは許可しない。最低限次をすべて観測して初めて許可する。

- `PACK_VERSION_SCHEMA_INCOMPATIBLE`
- Pack ID確定
- installed pathがexpected managed pathと一致
- installed directory存在
- filesystem digest観測成功
- DB content_digest == filesystem digest
- current generation safe
- startup Recovery lineageに未解決`RECOVERY_REQUIRED`なし
- enable state取得可能
- PackAccessCoordinatorが現在RECOVERY_REQUIRED blockでない

さらに操作直前に同じ条件を**再観測**する。

起動時categoryだけを信頼しない。

---

## 6. Legacy Version safety判定の修正

旧実装は`verify_manifest_consistency()`でPack Version parseをDB/FS current-generation一致確認より先に実行していた。

修正後:

```text
installed path identity
-> installed exists
-> filesystem digest observation
-> DB/filesystem digest equality
-> Pack Version parse
```

Legacy Version parseだけが失敗した場合、current generation / DB / filesystem一致を観測済みとして`SAFE_MAINTENANCE`候補を生成する。

ただしbootstrapで`recover_all()`に未解決Recoveryがないことを確認した場合だけ`recovery_lineage_healthy=true`とする。

Legacy Version自体は変換しない。

```text
1 -> 1.0
2 -> 2.0
```

等のauto migrationは実装していない。

---

## 7. Enabled Legacy Pack disable

Safe Maintenance条件を満たし、現在`is_enabled=true`の場合のみRecovery UIで次を許可する。

```text
Packを無効化
```

実処理は既存`PackLifecycleManager.set_pack_enabled()`を使用する。

- Pack Write Lock
- installed digest再確認
- DB enabled state更新
- manifest state backup

を既存境界のまま使用する。

無効化後、同じrestricted runtime内で再観測し、formal uninstallが可能な状態へUIを更新する。

---

## 8. Disabled Legacy Pack uninstall

Safe Maintenance条件を満たし、現在`is_enabled=false`の場合のみRecovery UIでアンインストールを許可する。

実処理は既存`PackLifecycleManager.uninstall_pack()`のみを使用する。

簡易`rmtree`等は追加していない。

既存formal lifecycle:

```text
Pack Write Lock
-> installed digest確認
-> operation journal
-> installed -> operation backup
-> pack_manifest.db transaction
-> installed_packs/items_master除去
-> post-state確認
-> COMPLETED
-> cleanup / Recovery
```

をそのまま利用する。

アンインストール前にはHuman confirmationを表示する。

確認内容:

- Pack name
- Pack ID
- current Version text
- 商品データが削除される
- Purchase Historyは保持される
- canonical Pack再導入は再起動後の通常店長モードで行う

---

## 9. Unknown generation / DB-FS mismatchでwrite禁止

Regressionで次を確認した。

### DB / managed install path mismatch

```text
safety_level = DIAGNOSTIC_ONLY
can_disable = false
can_uninstall = false
```

### 起動後filesystem tamper / digest変化

startup時はSafe Maintenanceだった場合でも、操作直前再観測でdigest不一致を検出すると即座にDiagnostic Onlyへ戻す。

uninstallは開始されずPack row / installed generationを保持する。

したがって、`PACK_VERSION_SCHEMA_INCOMPATIBLE`というcategoryだけで保守書込みは許可されない。

---

## 10. Restricted Bridge surface

Recovery Windowへ通常15 API Bridgeは公開しない。

専用`StoreManagerRecoveryApi`が公開するcallableは次の2つだけ。

```text
disable_pack(pack_id)
uninstall_pack(pack_id)
```

公開しない:

- `import_pack`
- `get_products`
- `add_to_cart`
- `checkout`
- generic `set_pack_enabled`
- update/reinstall/downgrade

APIはDomainError / unexpected exceptionをsafe responseへ変換し、raw Python exception / class / pathをUIへ返さない。

---

## 11. History保持

Recovery modeからformal uninstallした場合も`user_data.db`はPack lifecycle transactionの対象外である。

Regressionで購入済みOrderを作成した後に:

```text
Legacy Version
-> disabled
-> Recovery mode uninstall
```

を行い、次を確認した。

```text
installed_packs: removed
items_master: removed
installed filesystem: removed
orders: retained
order_items_snapshot: retained
History detail: retained
Statistics order_count: retained
```

current Pack fallbackは追加していない。

---

## 12. Recovery mode終了

Uninstall成功後もrestricted runtimeを通常店長モードへ自動昇格しない。

UIは:

```text
アンインストール完了
-> アプリ再起動を案内
```

とする。

canonical Pack導入は再起動後の通常bootstrapがPack subsystemをhealthyと判定した後、通常店長モードから行う。

---

## 13. Historical Recovery regression維持

既存の:

```text
IMPORT A
UPDATE A -> B
```

Historical supersede判定は変更していない。

Version comparisonをRecovery generation判定へ使用していない。

digest lineage / DB / filesystem observationによるcurrent generation判定を維持する。

---

## 14. Automated Tests

### Python

```text
441 PASS / 0 FAIL
```

今回追加した主なRegression:

- Recovery HTML固定background / foreground
- hashed CSP style / script
- `unsafe-inline` / external resourceなし
- long diagnostics wrapping
- enabled Legacy -> disable可 / uninstall不可
- disable後 -> uninstall可
- disabled Legacy -> formal uninstall可
- Recovery mode uninstall後History保持
- DB/install mismatch -> Diagnostic Only
- startup後digest変化 -> write拒否
- Recovery API surfaceはdisable/uninstallのみ

既存Phase 1〜7 / UAT差戻しTestは削除・skip・xfailしていない。

### JavaScript / DOM

```text
40 PASS / 0 FAIL
```

通常店舗 / 通常店長モードの既存UI regressionを維持。

### Total

```text
481 PASS / 0 FAIL
```

### compileall

```text
PASS
```

---

## 15. Windows Build / UAT

本最新版SourceはLinux環境で実装・自動試験した。

```text
Previous Windows Build: PASS
Previous Windows UAT: FAIL / STOP
Latest Windows Build: NOT EXECUTED
Latest Windows UAT: NOT EXECUTED / DEFERRED
```

次回Windowsでは既存`%LOCALAPPDATA%\FantasyStore`を保持したまま以下を最優先で確認する。

1. normal boot -> Legacy Versionでfail closed
2. `--store-manager` -> readable Recovery UI
3. enabled Legacy Pack -> disable成功
4. disabled Legacy Pack -> formal uninstall成功
5. History保持
6. restart ->該当restricted condition解消
7. Source runtime / frozen runtime parity

---

## 16. Remaining Gate B

PRESENT。

本修正のWindows再UATに加え、Phase 7 Gate B残項目を継続する。

- WebView2 / CSP実挙動
- Pack画像表示中update file lock
- second instance / `msvcrt.locking`
- Windows rename / replace
- Pack Recovery
- user DB backup / restore
- log rotation
- SQLite STRICT frozen runtime
- Pillow/jsonschema packaged
- offline
- external navigation
- clean shutdown
- Python未導入PC
- WebView2不足safe-stop
- path/cwd/program-folder tests

---

## 17. 正本逸脱

```text
正本逸脱なし
```

本修正のRecovery UI視認性およびSafe Maintenanceは、今回のHuman UAT再々々差戻し指示書で明示承認された追加要求である。

---

## 18. Release Candidate

```text
HOLD
```

Windows実機で本最新版を再build / UATし、残Gate Bを完了するまでRC PASSを宣言しない。
