# FantasyStore Phase 7 Windows UAT再差戻し修正 完了記録

> **Status update — 2026-09-05 Human追加変更**  
> Pack Version管理 / Downgrade防止 / Same Version再導入 / Pack Uninstallが追加確定したため、**本記録対象SourceのWindows UATはDEFERRED**です。Automated Test完了や過去のWindows build事実をUAT完了とは扱いません。次回Windows UAT対象は追加修正を取り込んだ最新版Sourceです。Release CandidateはHOLDのままです。


## 1. Overall Judgment

```text
Windows UAT Refix Implementation: COMPLETE
Automated Tests: PASS
Python: 391 PASS
JavaScript / DOM: 37 PASS
Total: 428 PASS / 0 FAIL
compileall: PASS
Windows Build after refix: NOT EXECUTED (current environment is Linux)
Windows UAT Recheck after refix: NOT EXECUTED
Release Candidate: HOLD
```

本記録は`利用者向け架空ECシステム Phase 7 Windows UAT再差戻し修正指示書`に基づく。Windows実機で確認していない項目はPASSとしていない。

## 2. Recovery generation判定修正

### 2.1 発見原因

従来`PackRecoveryManager.recover_all()`は、保持されている全journalをそれぞれ独立に現在のDB / installed filesystemと再照合していた。

このため正常な、

```text
IMPORT v1: new=A, COMPLETED
UPDATE v1→v2: old=A, new=B, COMPLETED
current DB/filesystem=B
```

に対して、先行IMPORT Aまでcurrent Bと比較し、`new import DB/installed state is not a known generation`として`RECOVERY_REQUIRED`へ誤分類していた。

### 2.2 修正

同一Pack IDのjournalをgeneration historyとして扱い、immutableな`started_at`順とdigest継承を用いてderived relevanceを算出する。

```text
preceding.new_digest == later.old_digest
AND
later.state == COMPLETED
```

を満たす先行operationは、後続generationにsupersedeされたHistorical operationとして現在世代判定から外す。

新しい永続Journal Stateは追加していない。`SUPERSEDED`等のstate machine拡張は行っていない。

### 2.3 Current Relevant Generation

後続operationへdigestが継承されていないfrontier operationだけが、従来どおり、

- journal
- DB digest
- installed digest
- staging digest
- backup digest

の実観測Recovery対象となる。

したがって`COMPLETED`を一律無視していない。最新generationが外部変更等によりunknownになった場合は従来どおり`RECOVERY_REQUIRED`となる。

### 2.4 Historical journal誤mutation防止

正常なHistorical `COMPLETED` journalは`recover_operation()`へ渡さないため、現在generationと不一致という理由で`RECOVERY_REQUIRED`へ書き換えられない。

Windows UATですでに旧buildがHistorical IMPORTを`RECOVERY_REQUIRED`へ誤mutationした状態もTestした。後続COMPLETED UPDATEによるdigest継承が成立していれば、derived relevance上Historicalとして扱い、現在の正常generationを妨げない。

既存の誤mutation済みjournal自体は証跡保持を優先し、勝手に`COMPLETED`へ書き戻していない。

## 3. 店長モード名称変更

Human-visible名称を以下へ変更した。

```text
通常起動:
FantasyStore.exe

店長モード:
FantasyStore.exe --store-manager
```

旧管理用CLIは正規CLIとして維持しない。

変更対象:

- CLI
- UI表示
- README
- Release README
- Windows RC Checklist
- Test
- 現行完了記録

内部の歴史的責務名まで無理な全面renameは行っていない。

## 4. 店長モード（復旧）

### 4.1 通常起動

未解決`RECOVERY_REQUIRED`が存在する場合、

```text
FantasyStore.exe
→ fail closed
→ 通常店舗UIを起動しない
```

を維持する。

Fatal messageには内部例外詳細を出さず、状態確認方法として、

```text
FantasyStore.exe --store-manager
```

を案内する。

### 4.2 店長モード起動

`--store-manager`でも自動Recoveryはskipしない。

```text
AppInstanceLock
→ user_data recovery/migration
→ pack manifest health
→ Pack automatic recovery
```

を先に実行する。

自動Recoveryで安全に確定できた場合は通常の店長モードへ進む。

### 4.3 未解決Recovery時

`--store-manager`かつPack Recovery未解決の場合のみ、Phase 4境界でrestricted startupを許可し、`StoreManagerRecoveryRuntime`へ入る。

このruntimeは通常Bridgeを公開しない。表示可能なのは最低限のRecovery証跡のみ。

- Pack ID
- Operation ID
- Operation Type
- Recovery State
- Old Version
- New Version

表示しないもの:

- stack trace
- Python class名
- arbitrary filesystem path
- raw exception
- DB editor / journal editor

### 4.4 店舗機能停止

店長モード（復旧）では通常Bridgeを公開しないため、以下は実行不能。

- 商品販売
- Cart変更
- Checkout
- Purchase確定
- Pack import
- Pack update
- enable / disable

さらに`PackAccessCoordinator`でもRecovery対象Packをblockedとして保持する。

本修正では、安全性を保証できるRecovery操作UIを新設していないため、状態確認のみ許可する安全側の実装とした。

## 5. Bootstrap / Runtime変更

### 5.1 `bootstrap_phase4`

内部引数`allow_recovery_required=False`を追加。

- default: 従来どおりfail closed
- `True`: unresolved decisionを保持したrestricted runtime構築を許可

未解決時は`verify_manifest_consistency()`を成功扱いで通過させず、対象Packを`PackAccessCoordinator.mark_recovery_required()`でblockedにする。

### 5.2 `bootstrap_phase5` / `bootstrap_phase6`

店長モードの正常起動時にPhase 4を二重bootstrapしてAppInstanceLockを再取得しないよう、既存runtimeを内部注入できる最小拡張を追加した。

既存責務・DTO・Repository・Checkout意味論は変更していない。

### 5.3 `bootstrap_phase7`

内部起動選択を`store_manager: bool`へ変更。

- normal + healthy → 通常店舗
- store-manager + healthy → 店長モード
- normal + recovery required → fail closed
- store-manager + recovery required → 店長モード（復旧）

## 6. UI / Resource変更

通常店長UI resourceを、

```text
ui/index-store-manager.html
```

へ変更。

Human-visible badgeは`店長モード`。

通常`ui/index.html`にはPack管理導線を持たせず、Routerもnormal modeから`packs` routeを許可しない既存境界を維持。

店長モード（復旧）は専用のPython生成static HTMLで、script / Bridge / filesystem pathを持たない。

## 7. Automated Test

### 7.1 Python

```text
391 PASS / 0 FAIL
```

既存baselineを削除・skip・xfailしていない。

新規Regression主項目:

1. IMPORT v1 → restart
2. IMPORT v1 → UPDATE v2 → restart
3. IMPORT v1 → UPDATE v2 → UPDATE v3 → restart
4. Multi-Pack generation histories
5. Historical COMPLETED journalをRECOVERY_REQUIREDへ誤mutationしない
6. 旧buildでHistorical journalがすでにRECOVERY_REQUIREDでも後続正常generationを妨げない
7. current unknown generation XはRECOVERY_REQUIRED維持
8. normal bootstrapはRecovery Requiredでfail closed
9. explicit restricted Phase 4 startupでは対象Pack blocked
10. healthy `--store-manager`は店長UI
11. Recovery Required + `--store-manager`は`StoreManagerRecoveryRuntime`
12. Recovery UIにPack/operation情報を表示し、通常Bridgeを公開しない
13. CLIは`--store-manager`のみ正規Manager flag
14. normal fatal messageに店長モード案内、raw internal detail非露出
15. Windows UAT実データ相当: Historical IMPORT誤mutation + current UPDATE正常で通常bootstrap成功

既存interrupted import/update、Crash Matrix、journal delay、rollback、digest unknown等も全Regression PASS。

### 7.2 JavaScript / DOM

```text
37 PASS / 0 FAIL
```

既存UAT Fix Testを維持し、名称・resourceを`店長モード` / `--store-manager`へ更新した。

### 7.3 compileall

```text
PASS
```

## 8. Windows Build

```text
NOT EXECUTED
```

現在の実装環境はLinux。前回Windows UAT差戻しSourceがWindowsでbuildできた事実を、本再差戻し修正版のbuild PASSへ流用していない。

Windowsでは以下を再実行する必要がある。

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

## 9. Windows UAT Recheck

```text
NOT EXECUTED
```

優先確認:

1. 既存`%LOCALAPPDATA%\FantasyStore`を手編集せず通常起動
2. Historical IMPORTを理由にRecovery Requiredにならない
3. current v2 generation正常認識
4. `FantasyStore.exe --store-manager` healthy起動
5. genuine Recovery Required + normal boot → fail closed
6. 同状態 + `--store-manager` → 店長モード（復旧）
7. Recovery情報表示
8. 店舗機能・Pack lifecycle writeが停止していること

## 10. Remaining Gate B

今回BLOCKER再確認後、既存Phase 7 Gate B残項目を継続する。

- WebView2 image displayed during Pack update
- Windows file lock
- second instance / `msvcrt.locking`
- Pack rename / replace
- Pack Recovery
- User DB backup restore
- `recovery_hold`
- log rotation
- frozen SQLite STRICT
- packaged Pillow
- packaged jsonschema
- offline
- CSP positive / negative
- external navigation
- clean shutdown / no zombie
- working directory variants
- Unicode / space path
- read-only program folder
- program folder replacement
- Python未導入PC
- WebView2不足safe-stop

## 11. 正本逸脱

```text
正本逸脱なし
```

本Windows UAT再差戻し指示書で明示されたHuman要求のみを追加実装した。

Money、Checkout transaction/idempotency、Cart coordinator、Pack完全置換、journal durability、Pack backup、fail-safe Recovery、user DB Backup/Recovery、History snapshot authority、Bridge公開15 API、offline原則は変更していない。

## 12. Final Status

```text
Windows UAT Refix Implementation: COMPLETE
Automated Tests: 428 PASS / 0 FAIL
Windows Build: NOT EXECUTED
Windows UAT Recheck: NOT EXECUTED
Release Candidate: HOLD
```

=== DOCUMENT END ===
