# FantasyStore Phase 7 Pack Version管理・アンインストール追加修正 完了記録

## 1. Overall Judgment

```text
Pack Version / Uninstall Fix Implementation: COMPLETE
Automated Tests: PASS
Python: 431 PASS
JavaScript / DOM: 40 PASS
Total: 471 PASS / 0 FAIL
compileall: PASS
Windows Build after this fix: NOT EXECUTED (current environment is Linux)
Windows UAT for latest source: DEFERRED / NOT EXECUTED
Remaining Gate B: PRESENT
Release Candidate: HOLD
```

本記録はHuman確定の`Phase 7 Pack Version管理・アンインストール追加修正指示書`に基づく。直前のWindows UAT再差戻し修正版Sourceは、本追加修正が確定した時点でWindows UAT対象として**DEFERRED**へ移行した。Automated Test完了や過去buildの成功事実をWindows UAT完了とは扱わない。

---

## 2. Pack Version Schema

正式Pack Version形式を次へ変更した。

```text
MAJOR.MINOR
```

各component:

```text
0 .. 999
```

canonical pattern:

```text
^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$
```

### VALID Regression

```text
0.0
0.1
1.0
1.1
1.9
1.10
1.999
12.345
999.999
```

### INVALID Regression

```text
1
1.
.1
1.2.3
01.2
1.01
1.001
1.1000
1000.1
v1.2
1-beta
1.2-beta
```

変更箇所:

- `resources/schema/pack-v1.json`
- `fantasy_store/domain/pack_version.py`
- Phase 3 `PackImporter` validation

Invalid VersionはPack Schema / Semantic境界で拒否し、Version比較へ進めない。

---

## 3. Version parser / comparator

`PackVersion`を追加し、Versionを文字列ではなく、

```text
(MAJOR:int, MINOR:int)
```

として比較する。

Regression:

```text
1.9 < 1.10
1.10 < 2.0
2.0 > 1.999
999.998 < 999.999
1.2 == 1.2
```

lexicographical string comparisonは使用していない。

---

## 4. Pack導入分類

`PackImportClassification`:

```text
NEW
UPGRADE
REINSTALL
DOWNGRADE_SKIPPED
```

判定:

| Current installed | Incoming | Classification |
|---|---|---|
| none | 1.2 | NEW |
| 1.1 | 1.2 | UPGRADE |
| 1.2 | 1.2 | REINSTALL |
| 1.2 | 1.1 | DOWNGRADE_SKIPPED |

`.vpack`選択後、Pack Version Policyはoperation journal / staging作成前のread-only preflightで判定する。

preflight:

1. `.vpack` source size / ZIP metadata safety確認
2. root `pack.json`のみread
3. strict JSON parse
4. local Draft 2020-12 pack schema validation
5. Pack ID validation
6. Pack Version canonical validation
7. current `installed_packs` version取得
8. Version classification

Full Phase 3 validation方式そのものは維持している。

外部`.vpack`がpreflight後に書き換えられた場合に備え、full validation後のPack IDがpreflight Pack IDと一致しない場合は、operation-owned staging / journalだけを破棄して拒否するTOCTOU guardも追加した。

---

## 5. Downgrade Skip

`incoming < installed`の場合:

```text
DOWNGRADE_SKIPPED
```

として正常Policy結果を返す。

変更しないことをRegression確認:

- installed Pack filesystem
- `installed_packs`
- `items_master`
- `is_enabled`
- Cart
- History
- current generation journal history
- staging

read-only preflightでDowngradeと確定した通常ケースでは、新operation journalを作成しない。

staging中の競合によりinstalled versionが先へ進む可能性にも備え、Pack Write Lock取得後・物理switch直前にcurrent installed versionを再readして再分類する。そこでDowngradeになった場合もswitchせずoperation-owned staging/journalを片付けて終了する。

強制Downgrade / `--force` / rollback UI等は追加していない。

---

## 6. Same Version再導入

```text
installed: 1.2 / digest A
incoming:  1.2 / digest B
```

を`REINSTALL`として許可する。

Version同一を「変更なし」とせず、既存の完全置換Lifecycleを実行する。

Regressionで、current generationがdigest Bへ更新され、digest AのCompleted journalがHistorical operationとして保持されることを確認した。

---

## 7. VersionとGenerationの分離

Version比較は**Pack導入可否Policy**だけに使用する。

Recovery generation判定はVersion番号へ置換していない。従来どおり:

- operation lineage
- old/new content digest
- DB current digest
- installed filesystem digest
- staging digest
- backup digest

の実観測で判定する。

直前Windows UAT再差戻しで実装したHistorical COMPLETED operation relevance / supersede判定を維持している。

---

## 8. Pack Uninstall Lifecycle

Human追加要件により、店長モードへPackアンインストールを追加した。

### 8.1 許可条件

```text
Pack installed
AND Pack disabled
AND Pack lifecycle / recovery state is safe
```

有効Packは`PACK_UNINSTALL_REQUIRES_DISABLED`として拒否する。UIではアンインストールbuttonをdisabledにし、先に無効化が必要な理由を表示する。

### 8.2 正常Lifecycle

```text
Pack Write Lock
↓
installed row確認 / disabled確認
↓
installed filesystem digest == DB digest確認
↓
UNINSTALL journal作成
↓
READY_TO_SWITCH
↓
installed/{pack_id}
→ backup/{operation_id}/{pack_id}
↓
OLD_BACKED_UP / FILES_SWITCHED
↓
pack_manifest.db BEGIN IMMEDIATE
↓
items_master DELETE
installed_packs DELETE
↓
COMMIT
↓
DB_SWITCHED
↓
DB row absent + installed absent確認
↓
COMPLETED
↓
pack_manifest_state.json更新
↓
operation backup best-effort cleanup
```

FSとSQLiteを単一ACID Transactionとは扱っていない。

Journal State enumは増やしていない。既存StateをUninstallにも再利用し、`operation_type="UNINSTALL"`でoperation意味を識別する。

---

## 9. Uninstall Recovery

Observation-based RecoveryをUninstallへ拡張した。

### FS先行 / DB未COMMIT

```text
DB = old
installed = none
backup = old
```

→ backup oldをinstalledへrestoreし、Uninstall前へrollback。

### DB COMMIT済み / FS finalization前

```text
DB = none
installed = none
backup = old
```

→ Uninstall成功側へ収束し、known old backupをcleanup。

### Journal update遅延

- installed→backup move成功 / journalはまだREADY_TO_SWITCH
- DB COMMIT成功 / journalはまだswitch前state

の双方をfixture化し、journal stateではなくDB/filesystem digest観測で正しく収束することを確認した。

### Unknown Generation

backup / installed等がjournal old digestに一致しない場合は推測で削除せず:

```text
RECOVERY_REQUIRED
```

を維持する。

### DB failure

installedをbackupへ退避した後、Pack DB deleteが失敗したfixtureでは、DBがoldのままであることを観測し、backupからold installed generationを復元する。Purchase Historyは変更しない。

---

## 10. Uninstall後の再導入

Completed Uninstall後は`installed_packs`が存在しないため、過去により新しいVersionが存在していても、次の導入は`NEW`として扱う。

Regression:

```text
Pack A 1.2
→ disable
→ uninstall
→ restart
→ Pack A 1.1 import
→ NEW
```

過去journal/historyだけを根拠に現在installed扱いへ戻さない。

Completed Uninstall journalはoperation historyとして保持し、restart RecoveryでもPackを勝手に復活させない。

---

## 11. Purchase History保持

Uninstallは`pack_manifest.db`とPack filesystemのみを対象とする。

変更しない:

- `user_data.db`
- `orders`
- `order_items_snapshot`
- Purchase History
- Purchase Statisticsの正本
- purchase-time snapshot images

Checkout後にPackをdisable→uninstallしたfixtureで、購入時商品名とorder/statisticsがそのまま取得できることを確認した。

---

## 12. 店長モード統合

正式CLI:

```text
FantasyStore.exe --store-manager
```

通常店舗Bridge API 15 namesは変更していない。

Manager modeだけ、専用`StoreManagerBridgeApi` / `ManagedStoreManagerBridgeApi`境界で`uninstall_pack`を追加する。通常`BridgeApi`にはUninstall surfaceを露出しない。

店長モードUI:

- Pack導入CTAを「商品パックを導入」へ変更
- Version classification result表示
- DOWNGRADE_SKIPPEDをErrorではなく安全Policy結果として表示
- enabled PackのUninstall button disabled + 理由表示
- disabled PackでUninstall確認UI
- 確認にPack name / Pack ID / Version / 商品が削除されること / History保持を表示

店長モード（復旧）は従来どおり状態確認専用で通常Bridgeを公開しないため、Recovery Required状態で無条件Uninstallはできない。

---

## 13. Historical Recovery修正 Regression維持

直前のWindows UAT再差戻し修正で実装した:

```text
IMPORT A COMPLETED
UPDATE A→B COMPLETED
current=B
```

で先行IMPORTを現在世代異常と誤判定しないderived relevanceを維持している。

Uninstall lineageも追加:

```text
current generation A
→ UNINSTALL consumes A
→ current installed = none
```

さらに後続IMPORTで新lineageが成立した場合、Completed UninstallはHistorical operationとして扱う。

Version番号はこのRecovery relevance判定へ使用していない。

---

## 14. Automated Test

### Python

```text
431 PASS / 0 FAIL
```

Current changeの主な追加Regression:

- Version valid 9 cases
- Version invalid 12 cases
- numeric tuple comparison
- NEW / UPGRADE / REINSTALL / DOWNGRADE_SKIPPED
- legacy single-component Version rejection
- Downgrade Skip no mutation
- Same Version reinstall new digest generation
- legacy installed Versionを自動変換しない
- enabled Pack uninstall reject
- disabled Pack uninstall + History保持
- FS-first uninstall Recovery
- DB-first uninstall Recovery
- old move journal-delay Recovery
- DB commit journal-delay Recovery
- unknown Uninstall generation → RECOVERY_REQUIRED
- DB uninstall failure → old generation rollback
- Completed Uninstall journal history / restart no resurrection
- Uninstall後のolder Version import → NEW
- normal BridgeにUninstall非露出 / manager Bridgeのみ露出
- Bridge Version classification result
- preflight/full-validation間Pack ID change rejection

既存Testを削除・skip・xfailしていない。

### JavaScript / DOM

```text
40 PASS / 0 FAIL
```

追加確認:

- manager-only Uninstall surface
- enabled Pack button disabled + reason
- disabled Pack confirmation before call
- History保持メッセージ
- DOWNGRADE_SKIPPEDをpolicy resultとして表示
- normal storefrontへUninstall/Pack管理surface非露出

### compileall

```text
PASS
```

---

## 15. Existing Windows UAT Data Compatibility

User追加確認に基づき明示評価した。

Windows UAT証跡の既存Pack Version:

```text
1
2
```

新Schema:

```text
MAJOR.MINOR
```

評価:

```text
既存UATデータのVersion形式: 1 / 2
新Schema適合: NO
自動変換: NO
自動削除: NO
次回Windows UATで既存データをそのまま「最新版Version Policy全項目」の対象として使用可能: NO
```

### 15.1 影響

Recovery generation判定はVersion比較ではなくdigest/journal/DB/filesystem観測を使用するため、旧`1` / `2`のjournal/DBはHistorical Recovery不具合の再現証跡として保持できる。

一方:

- 新Schemaによる`.vpack`再validation
- manifest DB再構築時のPack再validation
- currently installed PackとのVersion comparisonを伴う導入

では旧`1` / `2`はcanonical Versionではない。

実装はlegacy Versionを黙って`1.0` / `2.0`へ変換しない。legacy installed rowに対するVersion Policy importはValidation Errorとして停止し、DB/journalを変更しないRegressionを追加した。

### 15.2 次回Windows UATでの扱い

既存`%LOCALAPPDATA%\FantasyStore`をHuman確認なしに削除・変換しない。

次回UATでは用途を分ける必要がある。

1. **Historical Recovery再確認**: 現在のlegacy dataを証跡として保持し、そのまま通常起動判定を確認可能。
2. **新Version Policy / Upgrade / Reinstall / Downgrade / Uninstall UAT**: 新Schemaの`1.0` / `1.1`等を使用する別テスト状態が必要。

Humanが既存dataを変更することを選択する場合は、店長モードでlegacy Packを明示的にdisable→uninstallしてからcanonical PackをNEW導入する方法等があるが、**本実装は自動実行しない**。

Repository内Test fixture / RC用fixtureは必要箇所を`1.0` / `1.1`等へ更新済み。旧`1`は非互換性確認Testだけに残している。

---

## 16. Build / Windows UAT Status

Current environment:

```text
OS: Linux
Windows Build after this fix: NOT EXECUTED
Windows UAT for this latest source: DEFERRED / NOT EXECUTED
```

直前Sourceについても、本追加Human変更が確定したため:

```text
Windows UAT: DEFERRED
Release Candidate: HOLD
```

とする。

次回Windows対象は**本修正を含む最新版Source**のみ。

---

## 17. Remaining Gate B

最低限、次回Windowsで優先確認:

1. Historical journal誤判定Regression
2. normal boot / `--store-manager` / 店長モード（復旧）
3. canonical Version Pack NEW
4. UPGRADE
5. REINSTALL
6. DOWNGRADE_SKIPPED no mutation
7. disable
8. enabled Uninstall不可
9. disabled Uninstall
10. Uninstall restart
11. Uninstall後reinstall
12. History不変
13. Multi-Pack
14. Pack Recovery including Uninstall interruption
15. 残Gate B（WebView2/file lock/SQLite STRICT/offline/backup restore/etc.）

---

## 18. 正本逸脱

```text
正本逸脱なし
```

本指示書で明示承認されたPack Version比較・Downgrade防止・Pack UninstallはHuman追加変更として実装した。未承認の意味論変更はない。

---

## 19. Release Candidate Status

```text
Release Candidate: HOLD
```

最新版SourceのWindows build / UAT / Remaining Gate B完了前にPASSとはしない。
