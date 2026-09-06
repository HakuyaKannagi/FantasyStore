# 利用者向け架空ECシステム MVP — Phase 2 完了記録

- 実施日: 2026-09-05
- 対象Phase: Phase 2 — User Data / Backup / Repository
- 実装基盤: Human承認済み Phase 1 Repository
- Authority:
  1. `架空ショッピングシステム_プロジェクト仕様書_v1.1.md`
  2. `利用者向け架空ECシステム_基本設計書_v1.1.md`
  3. `利用者向け架空ECシステム_詳細設計書_v1.0.md`
  4. `利用者向け架空ECシステム MVP実装指示書.md`
  5. `利用者向け架空ECシステム MVP Phase 2 実装指示書`

## 1. 実装概要

### 1.1 User Repository

新規 `fantasy_store/persistence/user_repository.py` を実装した。

実装内容:

- `cart_items`
  - 全件取得
  - `(pack_id,item_id)`指定取得
  - INSERT
  - quantity UPDATE
  - DELETE
  - 全DELETE
  - line count / total quantity
- `orders`
  - INSERT / SELECT
  - history paging (`LIMIT/OFFSET`, page size最大100)
  - order count
  - purchased `total_quantity` aggregate
- `order_items_snapshot`
  - INSERT / SELECT
  - order detail基盤
- `purchase_requests`
  - INSERT / request_id lookup
- `user_settings`
  - get / list / upsert / delete
  - setting key allow-listは後続Application責務のまま保持
- DB-only atomic order bundle helper
  - `orders`
  - `order_items_snapshot`
  - `purchase_requests`
  - `cart_items DELETE`
  - を1つの `BEGIN IMMEDIATE` transactionで処理可能
  - Pack検証、Money計算、画像copy、Lock、ID生成、Bridge処理は実装していない

### 1.2 Money persistence boundary

Freeze済みDDLの以下へ canonical `MoneyValue` JSONを保存する。

- `orders.total_amount_json`
- `order_items_snapshot.unit_price_json`
- `order_items_snapshot.line_total_json`

Repository境界で `MoneyValue.to_canonical_json()` / `MoneyValue.from_canonical_json()` を使用し、非canonical JSONを読込時に拒否する。

SQLite INTEGER / Python float / JavaScript Numberへの金額変換は行っていない。

### 1.3 UserDataBackupManager

新規 `fantasy_store/persistence/user_backup.py` を実装した。

実装内容:

- SQLite `Connection.backup()`による論理backup
- `user_data.<timestamp>.<uuid>.tmp.db`
- temp DB close後のvalidation
- `os.replace()`によるfinal化
- final: `user_data.<timestamp>.<uuid>.db`
- 通常最大3世代保持
- migration前backupの `.migration-protected` sidecarによる保持保護
- cleanup failure時も新backupを有効のまま維持
- temp DBをbackup候補として列挙しない
- closed backup / restore-temp validation専用 `immutable=1` read-only接続
  - backup横へ不要な `-wal` / `-shm` を生成しない
  - live current DBには使用しない

Backup validation:

- SQLite open
- `PRAGMA integrity_check == ok`
- `schema_meta`
- `schema_version`
- `PRAGMA user_version`
- version整合
- required tables
- unknown future schema reject
- incomplete migration state reject

Backup trigger API:

- migration直前: `backup_before_migration()`
- Checkout COMMIT後用: `backup_after_checkout()`
- migration成功後用: `backup_after_migration()`
- 正常終了用: `backup_on_shutdown(dirty_since_last_backup=...)`
- 初回健全DBかつvalid backup 0件: `ensure_initial_backup()`

Checkout本体・正常終了dirty trackingのApplication統合は先行実装していない。

### 1.4 Current DB recovery

実装内容:

- current DB healthy → backupへrollbackしない
- current DB invalid → DB / WAL / SHMを `recovery_hold/<timestamp>/`へ可能な範囲で退避
- backupを新しい順にvalidation
- latest corrupt → 次のvalid generationへfallback
- candidate → restore temp DB → revalidation → `os.replace()` → current revalidation
- candidateなし → `DB_RECOVERY_FAILED`
- candidateなしで空DBを自動生成しない
- current DBがunknown future schemaの場合はcorruption扱いせず `DB_VERSION_UNSUPPORTED` のまま安全停止
- failed recoveryでcurrent DBが`recovery_hold`へ移動済みの次回起動も、初回起動と誤認せず再度安全停止
- current DB欠損かつvalid backupが存在する場合は空DBを作らずbackupから復旧

Current DB validationはWAL-aware read-only接続を維持し、未checkpoint WAL上のschema変更も確認する。`immutable=1`はclosed backup / restore-tempだけへ限定した。

### 1.5 Migration framework

`fantasy_store/persistence/migration.py`を拡張した。

- current schema validation
- sequential `MigrationStep`
- 1 step = 1 `BEGIN IMMEDIATE` transaction
- migration開始前のprotected backup必須
- backup failure → migration未開始
- step failure → rollback
- rollback後current DB validation
- post-step validation
- post-migration invalid DB → pre-migration backupから復旧
- migration成功後、新schema backup作成後にpre-migration protection解除
- failed stepの無限自動再試行なし

製品Schema Versionは1のため、製品用の架空v1→v2 migrationは追加していない。Framework試験はtest-only migration fixtureで行った。

### 1.6 Snapshot Directory

Phase 1でFreeze path helperとして存在していた以下をそのまま利用し、Phase 2 bootstrap testで生成を確認した。

```text
user_data/
└─ snapshots/
   ├─ images/
   └─ .pending/
```

商品画像copy、SHA-256、pending→final、snapshot孤児掃除、Checkout画像transactionは未実装。

### 1.7 Bootstrap integration

既存 `bootstrap_phase1()` の責務・順序は変更せず、`bootstrap_phase2()`を追加した。

Phase 2で追加した起動処理:

1. AppInstanceLock取得後にApplication directory作成
2. `user_data.db`存在・健全性確認
3. 必要時Recovery
4. 必要時Migration frameworkへ接続
5. 初回健全DBでvalid backup 0件ならinitial backup作成
6. `pack_manifest.db` Phase 1初期化
7. Phase 2 readyで停止

Pack journal、Pack整合、snapshot孤児掃除、UI起動へは進んでいない。

## 2. 新規／変更ファイル

### 新規

- `fantasy_store/persistence/user_repository.py`
- `fantasy_store/persistence/user_backup.py`
- `tests/test_user_repository.py`
- `tests/test_user_backup.py`
- `tests/test_migration_phase2.py`
- `tests/test_bootstrap_phase2.py`
- `docs/phase2_completion.md`

### 変更

- `fantasy_store/persistence/migration.py`
- `fantasy_store/persistence/connection.py`
- `fantasy_store/domain/errors.py`
- `fantasy_store/bootstrap.py`
- `fantasy_store/main.py`
- `README.md`
- `pyproject.toml`

## 3. Phase 1コードへの変更

Phase 1既存責務の置換・DDL変更・Money設計変更・Bootstrap順序変更は行っていない。

| 対象 | 変更理由 | 正本上の根拠 | Phase 1既存機能への影響 | Test |
|---|---|---|---|---|
| `persistence/migration.py` | Phase 2 migration / DB validation framework追加 | 詳細設計 7.4, 25.3, 25.4 / Phase 2指示 7〜9 | 既存DDL・`initialize_database()`を維持。追加APIのみ | 全87 PASS、Phase 1 40件含む |
| `persistence/connection.py` | closed backup検証でWAL/SHM sidecarを生成しないimmutable read-only接続追加 | 詳細設計 25.3.2〜25.3.4 | `connect()` / `connect_readonly()`既存動作は変更なし | backup artifact / WAL-aware current回帰PASS |
| `domain/errors.py` | Backup / Recovery / Migration安全停止コード追加 | 詳細設計 23, 25 / Phase 2指示 8〜9 | 既存例外変更なし。追加classのみ | 全87 PASS |
| `bootstrap.py` | 起動時user DB recovery / initial backupをPhase 2へ統合 | 詳細設計 22, 25 / Phase 2指示 8 | `bootstrap_phase1()`は維持。`bootstrap_phase2()`追加 | Phase 1 bootstrap回帰 + Phase 2 bootstrap PASS |
| `main.py` | 現在のsmoke targetをPhase 2へ更新 | Phase 2提出物・統合確認 | Foundation起動の到達点だけ更新。Phase 3以降は起動しない | isolated smoke PASS |
| `README.md`, `pyproject.toml` | 実装到達Phaseの表示更新 | 提出物整理 | Runtime責務へ影響なし | N/A |

## 4. Test結果

### 4.1 Test framework

- pytest
- Python: 3.13.5
- SQLite: 3.46.1
- 実行環境: Linux container（Windows release targetではない）

### 4.2 結果

```text
87 passed in 0.92s
```

- PASS: **87**
- FAIL: **0**
- Phase 1既存test: **40件すべて回帰PASS**

### 4.3 主なTest内容

Repository:

- cart CRUD
- quantity CHECK 1〜999
- 0件正常
- orders INSERT / read
- order items INSERT / read
- purchase request lookup
- history paging
- order detail
- user settings CRUD
- Foreign Key
- atomic transaction rollback
- successful DB-only order bundle

Money:

- zero
- huge exponent
- multiple sparse blocks
- 64桁significand相当
- DB canonical JSON round-trip
- noncanonical DB JSON reject

Backup:

- normal backup
- WAL connection open中のbackup
- temp→final
- validation
- normal 3-generation retention
- migration protected generation
- cleanup failure
- latest corrupt fallback
- all corrupt
- required table missing
- integrity failure
- schema/user_version mismatch
- unknown future schema
- incomplete migration state
- backup artifactに不要な WAL/SHM を生成しない

Recovery:

- current healthy → no rollback
- current corrupt
- recovery_hold
- WAL / SHM存在時退避
- restore temp
- restore後validation
- candidateなし → `DB_RECOVERY_FAILED`
- candidateなしでempty DBを生成しない
- failed recovery後の再起動でもempty DBを生成しない
- current DB missing + valid backup → restore
- unknown future current schemaを古いbackupへrollbackしない
- uncheckpointed WAL上のfuture schemaを見落とさない

Migration:

- pre-backup成功後のみ開始
- pre-backup failure →未開始
- 1-step transaction rollback
- post-validation
- invalid migrated DB → pre-backup restore
- failed stepの再試行なし
- success後new-schema backup + protection release

Snapshot directory:

- `snapshots/images`
- `snapshots/.pending`

### 4.4 未実施項目

Phase 3以降の機能試験は実施していない。これはPhase 2停止条件どおり。

Windows実機の `msvcrt.locking`、PyInstaller成果物のSQLite STRICT、WebView2正式検出PoCはPhase 1からの持越しのまま。

## 5. Failure Injection結果

以下を自動Testで故障注入した。

| 故障 | 結果 |
|---|---|
| SQLite Backup API途中例外 | final generationを公開せず、current DB維持 |
| backup validation失敗 | tempを破棄しfinal化しない |
| backup final `os.replace()`失敗 | final generationを公開せず安全失敗 |
| `recovery_hold` move失敗 | restoreへ進まず `DB_RECOVERY_FAILED` |
| restore temp作成失敗 | held currentを維持して安全停止 |
| restore後final validation失敗 | 正常起動扱いせず `DB_RECOVERY_FAILED` |
| old backup cleanup失敗 | 新backupはvalidのまま維持 |
| migration step途中例外 | transaction rollback、元Schema維持、1回で停止 |
| migration後required table欠損 | invalid DBをholdしpre-migration backupから復旧 |

不明状態を推測で正常化する経路は実装していない。

## 6. PoC結果

Phase 2で新たな外部環境依存PoCは発生していない。

実装中にSQLite backup validation方式の挙動確認を行い、closed backupへ通常のWAL-aware read-only接続を使うとsidecarが生成され得ることを確認した。このためclosed backup専用に`mode=ro&immutable=1`を採用し、live current DBはWAL-aware read-onlyのまま分離した。両経路は自動Testで検証済み。

Phase 1持越しPoC:

- Windows実機 `msvcrt.locking`
- PyInstaller成果物 SQLite STRICT
- WebView2 Runtime正式検出

## 7. 正本逸脱

**正本逸脱なし**

補足:

- Phase 1 DDLは変更していない。
- Money設計は変更していない。
- BootstrapのLock取得前後のdirectory作成順序は変更していない。
- Path構成は変更していない。
- 既存`connect()` / `connect_readonly()`の方式は変更していない。backup専用immutable read-only APIを追加しただけである。
- product Schema Versionは1のままであり、架空v1→v2 migrationを製品コードへ追加していない。
- DB欠損時でも`recovery_hold`/backup成果物が存在するdata areaを初回起動扱いしない。これは「復旧不能時に空DBへ逃げない」を再起動後も維持するための安全判定である。

## 8. 残課題

### 次Phaseへ持ち越す事項

Phase 3:

- `.vpack` ZIP validation
- JSON Schema validation
- image validation
- import
- PackPathResolver
- AssetResolver

Phase 4以降:

- Pack update / journal / recovery
- PackAccessCoordinator
- CartService / CheckoutService / HistoryService / StatsService
- snapshot画像copy / digest / pending→final / orphan cleanup
- Bridge API
- UI / pywebview
- PyInstaller packaging

### Known Issue

Phase 3以降は未実装。成功stubで実装済み扱いしていない。

### 技術的負債

Phase 2時点で未承認の新規技術的負債なし。

### 環境依存確認

- Windows実機 `msvcrt.locking`
- PyInstaller成果物 SQLite STRICT
- WebView2正式検出API

## 9. Phase 2 判定

**PASS（現開発環境）**

Phase 2完了条件を満たし、Phase 3以降へは進んでいない。

> Phase 3開始判断をHumanへ返す

=== DOCUMENT END ===
