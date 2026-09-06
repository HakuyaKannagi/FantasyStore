# FantasyStore MVP Phase 4 完了記録

- Phase: **Phase 4 — Pack Lifecycle / Generation Lock / Recovery**
- Status: **PASS / Phase 5開始判断待ち**
- 実施日: 2026-09-05
- Authority: Freeze済みプロジェクト仕様書 v1.1 / 基本設計書 v1.1 / 詳細設計書 v1.0 / MVP実装指示書 / Phase 4実装指示書

## 1. 実装概要

Human承認済みPhase 3 Repositoryを基盤に、Phase 3で生成する`PreparedPack`を正式Pack Lifecycleへ接続した。

実装範囲:

- Pack operation journal
- atomic journal write (`.tmp` → flush/fsync → `os.replace()`)
- Phase 4からPhase 3へ同一`operation_id`を供給する互換拡張
- `PackRepository`
- 新規Pack正式導入
- 同一Pack ID完全置換update
- update時の旧`is_enabled`維持
- update時の旧`installed_at`維持
- Pack backup
- Pack単位Read / Write Lock
- writer-preferenceによるwriter starvation抑止
- `PACK_BUSY`
- `set_pack_enabled()`
- `pack_manifest_state.json`
- atomic manifest state backup
- rename / move 3回・初回100ms指数backoff
- journal + installed / staging / backup / DB digestの実観測Recovery
- journal遅延Crash Recovery
- rollback / Recovery途中再Crash Recovery
- digest観測不能時の`RECOVERY_REQUIRED`
- `RECOVERY_REQUIRED` PackのRuntime利用停止境界
- `pack_manifest.db` health check / corruption hold / rebuild
- installed PackのPhase 3相当再検証
- same Pack ID + same digest時だけ`is_enabled`復元
- state backup欠損/破損時のdisabled復旧
- `items_master`再構築
- 起動時Phase 4 Recovery統合
- completed operation残存物とsafe orphan候補のcleanup

Phase 5のCatalog / Cart / Checkout / History / Stats Application Service、`CartCheckoutCoordinator`、snapshot画像処理は実装していない。

## 2. 新規ファイル

### Product code

- `fantasy_store/persistence/pack_repository.py`
- `fantasy_store/pack/access_coordinator.py`
- `fantasy_store/pack/file_ops.py`
- `fantasy_store/pack/journal.py`
- `fantasy_store/pack/manifest_state.py`
- `fantasy_store/pack/recovery.py`
- `fantasy_store/pack/updater.py`

### Test

- `tests/phase4_helpers.py`
- `tests/test_bootstrap_phase4.py`
- `tests/test_pack_access_coordinator.py`
- `tests/test_pack_failure_injection_phase4.py`
- `tests/test_pack_journal_manifest_state.py`
- `tests/test_pack_manifest_rebuild_phase4.py`
- `tests/test_pack_recovery_phase4.py`
- `tests/test_pack_repository_phase4.py`

## 3. 既存ファイル変更

### `fantasy_store/pack/importer.py`

Phase 4 Orchestratorから`operation_id`を供給できるよう互換拡張した。

- `prepare(..., operation_id=None, phase_callback=None)`
- Phase 3 validation方式は変更していない
- staging展開後に`VALIDATING`をjournalへ記録できるcallback境界を追加
- `validate_directory()`を追加し、manifest DB再構築時にinstalled PackへPhase 3相当の再検証を実行可能とした

### `fantasy_store/pack/path_resolver.py`

- Pack backup path生成
- persistent root相対path生成
- journal記録pathの再解決 / containment再確認

既存staging / installed / asset path責務は維持。

### `fantasy_store/runtime/paths.py`

`packs/recovery_hold/` helper / directoryを追加。既存path構成を変更せず、Pack manifest DB破損保持先を追加した。

### `fantasy_store/domain/errors.py`

Phase 4 Domain Errorを追加。

- `PACK_BUSY`
- `PACK_FILE_LOCKED`
- `RECOVERY_REQUIRED`
- `PACK_OPERATION_FAILED`系

### `fantasy_store/bootstrap.py`

`bootstrap_phase4()` / `Phase4Runtime`を追加。

Freeze済み起動順序を維持:

```text
persistent path
→ bootstrap minimum dirs
→ logging
→ AppInstanceLock
→ Application dirs
→ WebView2 Phase 1 probe boundary
→ Phase 2 user_data recovery / migration
→ pack_manifest.db health / rebuild
→ unfinished Pack journal recovery
→ manifest / installed整合確認
→ Phase 4 ready
```

Phase 5 Application Service / UI起動は行わない。

### `pyproject.toml` / `README.md`

Phase 4到達点へ説明を更新。runtime dependency追加なし。

## 4. 主要Class / Function

- `PackRepository` — `pack_manifest.db` CRUD / `BEGIN IMMEDIATE` transaction境界
- `PackAccessCoordinator` — Pack ID単位writer-preference RW Lock
- `PackJournalStore` — operation journal atomic read/write
- `PackManifestStateStore` — management state backup atomic read/write
- `PackLifecycleManager.import_vpack()` — Phase 3 stagingからNEW / UPDATE正式Lifecycle
- `PackLifecycleManager.set_pack_enabled()` — Pack Write Lock下でenabled状態変更
- `PackRecoveryManager.ensure_manifest_database()` — manifest DB health / rebuild
- `PackRecoveryManager.recover_operation()` — 観測ベースoperation Recovery
- `PackRecoveryManager.recover_all()` — 起動時journal scan
- `PackRecoveryManager.verify_manifest_consistency()` — DB / installed / derived items整合
- `replace_with_retry()` — rename/move有限回retry

## 5. Pack Lifecycle

### 5.1 新規Import

```text
STAGING journal
→ Phase 3 safe extraction
→ VALIDATING
→ PreparedPack
→ VALIDATED
→ READY_TO_SWITCH
→ target Pack Write Lock
→ staging → installed
→ FILES_SWITCHED
→ pack_manifest.db BEGIN IMMEDIATE
→ installed_packs INSERT
→ items_master bulk INSERT
→ COMMIT
→ DB_SWITCHED
→ DB digest == installed digest == new_digest
→ COMPLETED
→ pack_manifest_state backup
```

### 5.2 完全置換Update

```text
READY_TO_SWITCH
→ target Pack Write Lock
→ DB old_digest / installed old_digest一致確認
→ old installed → backup
→ OLD_BACKED_UP
→ new staging → installed
→ FILES_SWITCHED
→ BEGIN IMMEDIATE
→ old items_master DELETE
→ new items_master INSERT
→ installed_packs metadata UPDATE
→ COMMIT
→ DB_SWITCHED
→ digest整合確認
→ COMPLETED
→ manifest state backup
→ old backup best-effort cleanup
```

`is_enabled`と`installed_at`はUPDATE SQLの更新対象に含めず旧値を維持する。

## 6. Test結果

Test framework:

- `pytest==9.0.2`

実行:

```bash
python -m pytest -q
```

最終結果:

- **238 PASS**
- **0 FAIL**
- Phase 1〜3 regression: **176 PASS**
- Phase 4 new tests: **62 PASS**

既存Phase 1〜3 testは削除・緩和していない。

## 7. Crash Recovery Matrix

| Crash Point | Observed Journal | Observed installed | Observed staging | Observed backup | Observed DB digest | Recovery Decision | Final State |
|---|---|---|---|---|---|---|---|
| staging途中 | `STAGING` / no pack_id | old or none | partial | none | old/none | operation-owned staging破棄 | pre-state / rolled back |
| validation中 | `VALIDATING` | old | partial/new | none | old | staging破棄 | old |
| validation完了 | `VALIDATED` | old | new | none | old | staging破棄 | old |
| switch直前 | `READY_TO_SWITCH` | old | new | none | old | staging破棄 | old |
| old rename後 / journal遅延 | `READY_TO_SWITCH` | none | new | old | old | backup digest=old確認後restore | old |
| OLD_BACKED_UP後 | `OLD_BACKED_UP` | none | new | old | old | old restore | old |
| new rename後 / journal遅延 | `OLD_BACKED_UP` | new | none | old | old | installed=new / backup=old確認後rollback | old |
| FILES_SWITCHED後 / DB未切替 | `FILES_SWITCHED` | new | none | old | old | rollback | old |
| DB transaction途中 | `FILES_SWITCHED` | new | none | old | old (SQLite rollback) | rollback | old |
| DB COMMIT後 / journal遅延 | `FILES_SWITCHED` | new | none | old | new | success側へ`DB_SWITCHED`→`COMPLETED` | new |
| DB_SWITCHED後 | `DB_SWITCHED` | new | none | old | new | digest一致確認 | new / COMPLETED |
| COMPLETED直後 / backup残存 | `COMPLETED` | new | none | old | new | new維持・backup cleanup | new |
| backup cleanup済 | `COMPLETED` | new | none | none | new | 正常完了 | new |
| ROLLING_BACK / new退避後Crash | `ROLLING_BACK` | none | new | old | old | backup→installed | old |
| ROLLING_BACK / old restore後Crash | `ROLLING_BACK` | old | new | none | old | old確認・new staging cleanup | old |

Recovery判定はjournal stateだけでは行わない。

## 8. Digest異常Test

以下を自動Testした。

- installed digestがold/newどちらでもない
- backup digestがold/newどちらでもない
- staging digestが観測不能
- DB digestがold/newどちらでもない
- journal old/new双方と不一致
- digest read `PermissionError`
- digest read failure retry上限到達

判断不能時:

```text
RECOVERY_REQUIRED
→ destructive operation停止
→ 対象Pack Runtime access block
→ evidence保持
```

`RECOVERY_REQUIRED` journalは次回起動でも勝手に初期化・削除せず再観測する。

## 9. Failure Injection結果

| 故障 | File State | DB State | Journal / Recovery | 結果 |
|---|---|---|---|---|
| journal write failure | finalは旧state、tmp残存可能 | 不変 | atomic final非置換 | PASS |
| journal `os.replace()` failure | final保持 | 不変 | tmpを正式扱いしない | PASS |
| old→backup rename failure | old installed維持 | old | READY_TO_SWITCH | `PACK_FILE_LOCKED` / PASS |
| rename retry 2回失敗→3回成功 | 最終move成功 | 不変 | retry log可能 | PASS |
| rename retry全失敗 | source維持 | 不変 | fail | PASS |
| new→installed failure | backup oldからrollback | old | old復元 | PASS |
| rollback failure | evidence維持 | old | `RECOVERY_REQUIRED` | PASS |
| DB BEGIN failure | files oldへrollback | old | journal cleanup/recovery | PASS |
| items INSERT途中failure | SQLite rollback | old | files oldへrollback | PASS |
| DB COMMIT後Crash | installed=new | new | journal遅延を観測Recovery | PASS |
| digest read failure | 破壊操作なし | 観測値維持 | `RECOVERY_REQUIRED` | PASS |
| manifest state write failure | installed/DB確定状態維持 | commit維持 | `BACKUP_WRITE_FAILED` logging | PASS |
| backup cleanup failure | new維持、backup残存 | new | completed operationとして次回cleanup可能 | PASS |
| Recovery rename failure | evidence維持 | old/new既知値維持 | `RECOVERY_REQUIRED` | PASS |
| operation中journal failure after old rename | backup=old / staging=newを実観測 | old | immediate observation rollback | PASS |

通常例外発生時、対象Pack Write Lockを保持したままdurable journal + physical state + DBを再観測し、安全にrollback / complete / `RECOVERY_REQUIRED`へ収束させる。

## 10. PackAccessCoordinator Test / PoC

自動Test:

- multiple reader
- reader中writer待機
- writer中reader timeout → `PACK_BUSY`
- writer exclusive
- context exception後release
- Pack A writerがPack B readerを停止しない
- writer待機中の新readerを抑止
- `RECOVERY_REQUIRED` Pack access block
- Phase 3 staging / validation中はWrite Lock未取得

実環境簡易負荷PoC:

- 8 reader threadを連続実行
- writer 1回を同時投入
- writer取得待ち: **約0.001086秒**（当該Linux test containerでの一回の観測値）
- 全thread正常停止

これは性能保証値としてFreezeしない。writer-preference構造により、writer待機後の新規reader流入を止める実装とした。

## 11. rename / `os.replace` PoC

環境:

- Python 3.13.5
- SQLite 3.46.1
- Linux 6.18.35 test container

確認結果:

- same volume directory rename: **成立**
- destination不存在directoryへの`os.replace`: **成立**
- destination既存・空directoryへの置換: **成立（現Linux環境）**
- destination既存・非空directoryへの置換: **`OSError`（現Linux環境）**
- retry path: 2回`PermissionError`後、3回目成功を自動Testで確認

製品Lifecycleではdestinationが存在しないことを事前確認し、old→backup / staging→installedを行うため、既存非空directoryへの上書き置換には依存しない。

Windows実機file lock / directory rename挙動は**DEFERRED**。設計変更は行っていない。

## 12. `pack_manifest_state.json`

自動Test:

- 正常生成 / load
- atomic replace
- replace failureで既存final維持
- `.tmp`残存
- parse failure
- unknown format version
- same Pack ID + same digest
- same Pack ID + different digest
- backup欠損
- invalid backup
- enable / disable後write failure nonfatal

`is_enabled`復元はsame Pack ID + same digestの場合だけ行う。

## 13. `pack_manifest.db`再構築

自動Test:

- DB open不能相当の破損
- logical/schema health failure
- corrupt DB保持
- installed Pack正常再検証
- state backup正常
- state backup欠損
- state backup破損
- digest mismatch
- invalid installed Packを未登録のまま保持
- `items_master`再構築
- same generation `is_enabled`復元
- unknown generation disabled
- future Schema Versionをcorruptionと誤認してv1再構築しない
- `user_data.db`不変

state backupから元管理状態を確定できない場合は全Pack disabledで登録する。

## 14. Orphan Cleanup

自動削除するのは安全にoperationとの対応を確定できるものに限定した。

- completed operationのknown backup/staging: best-effort cleanup
- final journalが存在する同名`.json.tmp`: cleanup可能
- final journalなしの`.tmp`: **不明のため残す**
- journalに対応しないstaging / backup directory: **不明のため残しwarning**
- installed directoryのみ存在しDB rowがない場合: **自動登録・自動削除しない**

## 15. Bootstrap

`bootstrap_phase4()`自動Test:

- Pack 0件fresh起動正常
- old rename成功 / journal未更新状態を起動時Recovery
- `RECOVERY_REQUIRED`解消不能時はPhase 4 readyへ進まず安全停止
- failure時`AppInstanceLock` release

## 16. Phase 1〜3 Regression

既存176 TestすべてPASS。

変更していない主要Freeze境界:

- Money設計
- 2DB DDL
- SQLite接続 / PRAGMA
- user_data Backup / Recovery
- migration
- `.vpack` security validation
- image validation
- content digest規則
- AssetResolver

Phase 3変更は`operation_id`供給・directory再検証の互換拡張のみ。

## 17. Dependency

Phase 4追加runtime dependency: **なし**

既存:

- `Pillow==12.3.0`
- `jsonschema==4.26.0`

Test:

- `pytest==9.0.2`

## 18. 正本逸脱

**正本逸脱なし**

正本変更を必要とする実装不能・内部矛盾は発見していない。

## 19. 残課題 / 次Phase

Phase 5へ持ち越し:

- `CatalogService`
- `CartService`
- `CheckoutService`
- `HistoryService`
- `StatsService`
- `PackService` Application boundary
- `CartCheckoutCoordinator`
- Checkout時の複数Pack Read Lock順序
- snapshot画像copy / pending→final
- Checkout transaction / request_id idempotency

Phase 6以降:

- Bridge API
- native file dialog
- UI
- pywebview
- PyInstaller packaging

環境依存持越し:

- Windows実機`msvcrt.locking`
- Windows実機Pack file lock / rename挙動
- PyInstaller成果物上のSQLite `STRICT`
- WebView2正式検出
- WebView2画像参照とWindows file lock

Known Issue:

- **製品機能上のKnown Issueなし**（Phase 4完了条件範囲）。
- Windows固有挙動は上記PoC持越しであり、現時点で正本変更提案ではない。

## 20. 停止条件

Phase 4実装・Test・Recovery Matrix・完了記録の提出時点で停止する。

> **Phase 5開始判断をHumanへ返す。**
