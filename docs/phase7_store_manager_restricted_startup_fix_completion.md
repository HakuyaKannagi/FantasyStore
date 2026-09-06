# FantasyStore Phase 7 店長モード（復旧）起動境界 再々差戻し修正 完了記録

## 1. Overall Judgment

```text
Store Manager Restricted Startup Fix Implementation: COMPLETE

Automated Tests:
Python 435 PASS
JavaScript / DOM 40 PASS
Total 475 PASS / 0 FAIL

compileall: PASS

Previous Windows Build: PASS
Previous Windows UAT: FAIL / STOP
Reason: Pack manifest / consistency safety failure was outside the restricted store-manager startup boundary.

Windows Build for this latest source: NOT EXECUTED
Windows UAT for this latest source: NOT EXECUTED / DEFERRED
Remaining Gate B: PRESENT
Release Candidate: HOLD
```

本記録はHuman確定の「Phase 7 Windows UAT再々差戻し修正指示書 — 店長モード（復旧）起動境界拡張」に基づく。Windows実機再確認なしにRelease Candidate PASSとは判定しない。

---

## 2. Windows failure発生箇所 / Source上の確定

Windows UATログではHistorical Recovery自体は次まで進んでいた。

```text
PACK_RECOVERY_HISTORICAL_SUPERSEDED
PACK_RECOVERY_COMPLETE
PACK_RECOVERY_COMPLETE
```

Source上のstartup flowを追跡した結果、停止境界は次であることを確定した。

```text
bootstrap_phase4()
  -> PackRecoveryManager.recover_all()
     -> unresolved RECOVERY_REQUIREDなし
  -> PackRecoveryManager.verify_manifest_consistency()
     -> installed Pack再検証
     -> PackImporter.validate_directory()
     -> PackVersion.parse("2")
     -> PackValidationError
     -> PackRecoveryRequiredError(code="RECOVERY_REQUIRED")
```

旧Windows UATデータのPack Versionは`1` / `2`であり、Human確定の現行Schema `MAJOR.MINOR`へ非適合である。このmanifest consistency failureは、旧実装では`recover_all()`の`RecoveryDecision`に現れないため、`--store-manager`のrestricted startup条件から漏れていた。

修正後は、manifest consistency境界でLegacy Versionを明示検出し、raw validation exceptionではなく次のstructured diagnosticへ変換する。

```text
category: PACK_VERSION_SCHEMA_INCOMPATIBLE
code boundary: RECOVERY_REQUIRED (fail-closed DomainError familyを維持)
pack_id: observed Pack ID
pack_version: observed legacy text (例: "2")
state: MANIFEST_CONSISTENCY_FAILED
```

既存Legacy Versionは自動変換しない。

---

## 3. `recover_all()`後のmanifest consistency failure処理

`bootstrap_phase4(..., allow_recovery_required=True)`のPack startup boundaryを拡張した。

従来:

```text
recover_all() returns RECOVERY_REQUIRED
  -> restricted startup possible

recover_all() healthy
  -> verify_manifest_consistency() raises
  -> process fatal
```

修正後:

```text
recover_all()
  -> unresolved RecoveryDecisionをstructured PackSafetyDiagnostic化

unresolved Recoveryなし
  -> verify_manifest_consistency()
     -> healthy: Phase 4 ready
     -> Pack-only safety failure:
        normal boot: raise / fail closed
        store-manager bootstrap: diagnostic保持してrestricted startup
```

verification自体はskipしていない。不整合を確認したうえでrestricted modeへ渡す。

---

## 4. Pack subsystem safety failure分類

追加した安全な診断構造:

```text
PackSafetyDiagnostic
- category
- pack_id
- pack_version
- operation_id
- operation_type
- state
- message
```

現在の主な分類:

| Category | Meaning |
|---|---|
| `PACK_RECOVERY_REQUIRED` | journal / generation recoveryを安全に確定できない |
| `PACK_VERSION_SCHEMA_INCOMPATIBLE` | 導入済みPack Versionが現行`MAJOR.MINOR`へ非適合 |
| `PACK_INSTALLED_DB_MISMATCH` | manifest DBと安全なinstalled path / digest /実体が一致しない |
| `PACK_MANIFEST_INCONSISTENT` | 導入済みPackを現行Pack validationで安全に再検証できない |

永続journal stateは追加していない。診断はstartup時のderived stateである。

raw stack trace、raw exception、任意filesystem pathは診断構造へ格納しない。

---

## 5. Normal boot fail-closed維持

通常起動:

```text
FantasyStore.exe
```

Pack manifest / installed consistencyを安全に確定できない場合は従来どおり起動を拒否する。

Regressionで`PACK_INSTALLED_DB_MISMATCH`およびLegacy Version incompatibilityについて通常起動が`PackStartupSafetyError`（`PackRecoveryRequiredError`系）でfail closedになることを確認した。

Recovery安全境界は緩和していない。

---

## 6. `--store-manager` restricted startup

店長モード:

```text
FantasyStore.exe --store-manager
```

Pack subsystem safety failureが残る場合、`StoreManagerRecoveryRuntime`を返す。

restricted UI表示内容:

- failure category
- Pack ID
- 観測可能なPack Version
- operation ID（該当する場合）
- operation type（該当する場合）
- consistency / recovery state
- Human向け安全メッセージ

restricted UIはJavaScript Bridgeを公開しない。

停止する機能:

- 商品販売
- Cart変更
- Checkout
- Pack導入 / Upgrade / Reinstall
- enable / disable
- uninstall

ログには理由を区別可能なeventを残す。

```text
PACK_RECOVERY_RESTRICTED_STARTUP
PACK_MANIFEST_RESTRICTED_STARTUP
STORE_MANAGER_RESTRICTED_STARTUP
DB_PHASE4_RESTRICTED
```

`STORE_MANAGER_RESTRICTED_STARTUP`のlog messageにはdiagnostic categoryを含める。

---

## 7. Legacy Version非互換の扱い

既存Windows UATデータ:

```text
version = 1
version = 2
```

現行Schema:

```text
MAJOR.MINOR
```

結果:

```text
既存UATデータのVersion形式: 1 / 2
新Schema適合: NO
自動変換: NO
自動削除: NO
```

修正後の期待:

```text
normal boot
  -> Pack状態を現行仕様で安全確定できないためfail closed

--store-manager
  -> 店長モード（復旧）
  -> Pack ID / legacy Version / incompatibilityを安全表示
```

Regression fixtureではPack DBとinstalled `pack.json`のVersionを意図的に`"2"`とし、current digest / Historical lineageを整合させた状態を構築した。

その状態で:

- Historical IMPORTは`historical_superseded`
- latest generationはRecovery上healthy
- manifest consistencyだけがLegacy Versionを拒否
- normal boot fail closed
- store-manager restricted startup成功
- DB / `pack.json`の`"2"`が`2.0`等へmutationされない

ことを確認した。

---

## 8. Historical Recovery修正維持

直前再差戻しで実装したHistorical operation relevance判定は変更していない。

維持事項:

```text
IMPORT A
UPDATE A -> B
```

について、先行Aは後続operationがAをold_digestとして継承している場合`historical_superseded`として扱う。

Version番号ではなくdigest lineage / DB / filesystem観測によりcurrent generationを判定する。

Windows前回UATで確認された`PACK_RECOVERY_HISTORICAL_SUPERSEDED`改善を戻していない。

---

## 9. Pack外fatal failureをrestricted modeへ吸収しない

「何でもcatchして店長モード」は実装していない。

新restricted分岐はPack manifest consistencyの`PackStartupSafetyError`および既存Recovery Decisionに限定する。

Regressionとしてcorrupt `user_data.db` + valid backup候補なしを構成し、

```text
--store-manager
-> DatabaseRecoveryError
-> Pack店長モード（復旧）へ入らない
```

ことを確認した。

以下は従来fatal boundaryのまま:

- User DB recovery不能
- user_data schema failure / unsupported schema
- second instance
- WebView2不足 / probe failure
- process-level unexpected exception

---

## 10. 新規Regression Test

新規:

`tests/test_phase7_store_manager_restricted_startup.py`

4件:

1. manifest DB / installed path consistency failure
   - normal fail closed
   - store-manager restricted startup
2. Historical lineage healthy + legacy Version `"2"`
   - Recovery healthy
   - normal fail closed
   - store-manager restricted startup
   - no automatic migration
3. unrecoverable user DB failure
   - store-manager restricted Pack modeへ吸収しない
4. healthy Pack state
   - normal店長モード維持

既存Recovery Required store-manager test、Historical supersede test、Version / Uninstall testを削除・skip・xfailしていない。

---

## 11. Automated Test結果

### Python

```text
435 PASS
0 FAIL
0 SKIP
```

直前baseline:

```text
431 PASS
```

追加:

```text
4 PASS
```

### JavaScript / DOM

```text
40 PASS
0 FAIL
```

UI / store-manager / Version / Uninstall regressionを維持。

### Total

```text
475 PASS / 0 FAIL
```

### compileall

```text
PASS
```

Command:

```text
python -m pytest -q
npm run test:ui
python -m compileall -q fantasy_store tests
```

---

## 12. 主な変更ファイル

```text
fantasy_store/domain/errors.py
fantasy_store/pack/recovery.py
fantasy_store/bootstrap.py
fantasy_store/runtime/store_manager_recovery.py
tests/test_phase7_store_manager_restricted_startup.py
README.md
docs/windows_rc_checklist.md
docs/phase7_store_manager_restricted_startup_fix_completion.md
```

Money、Checkout transaction/idempotency、Cart coordinator、History snapshot authority、Pack Version Policy、Downgrade Skip、Same Version Reinstall、Uninstall Lifecycle / Recoveryは変更していない。

---

## 13. Windows Build / UAT Status

本指示書発行時点でHumanから提供された前段実績:

```text
Previous Windows Build: PASS
Previous Windows UAT: FAIL / STOP
```

今回修正版については現在のLinux環境でWindows build / UATを実行できないため:

```text
Windows Build for this latest source: NOT EXECUTED
Windows UAT for this latest source: NOT EXECUTED / DEFERRED
```

Windowsで確認していない事項をPASSとは記録しない。

---

## 14. 次回Windows UAT最優先

現在の`%LOCALAPPDATA%\FantasyStore`を削除・自動Migration・DB/journal手編集せず保持したまま確認する。

1. `FantasyStore.exe`
   - Legacy Version等でPack安全確定不能ならfail closed
2. `FantasyStore.exe --store-manager`
   - 店長モード（復旧）起動
   - Pack ID / Version / consistency category確認
3. `python -m fantasy_store.main --store-manager`
   - Source runtime parity
4. canonical `1.0 / 1.1` state
   - normal店長モード
5. User DB fatal fixture
   - Pack restricted modeへ吸収されないこと

その後、Pack Version / Uninstallおよび残Gate Bを続行する。

---

## 15. Remaining Gate B

PRESENT。

少なくとも:

- 最新Source Windows build
- 上記startup blocker再確認
- Pack Version NEW / UPGRADE / REINSTALL / DOWNGRADE_SKIPPED
- Uninstall / Uninstall Recovery
- WebView2 image displayed during update / Windows file lock
- second instance / `msvcrt.locking`
- Pack rename / replace
- Pack Recovery
- User DB backup restore / recovery_hold
- log rotation
- frozen SQLite STRICT
- packaged Pillow / jsonschema
- offline
- CSP positive / negative
- external navigation
- clean shutdown
- path / cwd variants
- Python未導入PC
- WebView2不足safe-stop

が未完了。

---

## 16. 正本逸脱

```text
正本逸脱なし
```

本修正はWindows UAT再々差戻し指示書でHumanが明示承認した追加startup boundaryであり、未承認逸脱ではない。

Legacy Version Migration、force recovery、DB/journal editor、filesystem explorer、Authentication等は追加していない。

---

## 17. Final Status

```text
Store Manager Restricted Startup Fix Implementation:
COMPLETE

Automated Tests:
475 PASS / 0 FAIL

compileall:
PASS

Windows Build:
NOT EXECUTED (latest source)

Windows UAT:
NOT EXECUTED / DEFERRED (latest source)

Remaining Gate B:
PRESENT

Release Candidate:
HOLD
```

Windows実機再確認が完了するまでRelease Candidate PASSは宣言しない。
