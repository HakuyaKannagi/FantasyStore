# 利用者向け架空ECシステム MVP — Phase 1 完了記録

- 実施日: 2026-09-05
- 対象Phase: Phase 1 — Foundation / Bootstrap / Money / DB基盤
- 実装正本:
  1. `架空ショッピングシステム_プロジェクト仕様書_v1.1.md`
  2. `利用者向け架空ECシステム_基本設計書_v1.1.md`
  3. `利用者向け架空ECシステム_詳細設計書_v1.0.md`
  4. `利用者向け架空ECシステム MVP実装指示書.md`

## 1. 実装概要

### 実装した機能

- Project / Python package skeleton
- `main.py`
- `bootstrap.py`
  - 永続領域root / `logs/` の最小作成
  - logging初期化
  - `AppInstanceLock`取得
  - Lock取得成功後のみApplication領域作成
  - Phase 1 DB初期化
- `config.py`
- `logging_setup.py`
  - UTF-8 `logs/app.log`
  - 5 MiB / 5世代 RotatingFileHandler
  - event code / operation_id / request_id のログ項目基盤
- `runtime/`
  - `app_lock.py`: Windows `msvcrt.locking`、開発試験用POSIX `flock` fallback
  - `paths.py`: Freeze済み永続領域構成
  - `resource_locator.py`: source / PyInstaller resource root境界
  - `webview2_probe.py`: Phase 1のprobe境界のみ。可用性を成功扱いするstubにはしていない
- `domain/`
  - `errors.py`
  - `ids.py`
  - `money.py`
  - `pack_state.py`
- `persistence/`
  - `connection.py`
  - `migration.py`
- 初期DDL
  - `user_data.db`
  - `pack_manifest.db`
- DB Schema Version = 1
  - `schema_meta.schema_version`
  - `PRAGMA user_version`
- `MoneyLiteral`
  - canonical validation
  - 1〜64桁 significand
  - exponent 0〜9,000,000,000,000,000,000
  - zeroの一意表現
- `MoneyValue`
  - 疎なbase-10^9 block表現
  - 正確な加算
  - 比較
  - 数量整数倍
  - canonical JSON serialize / restore
- SQLite価格比較用派生キー
  - `price_magnitude`
  - 64桁 `price_sort_digits`
- Pack IDのWindows予約basename検証基盤

### 主要class / module

- `fantasy_store.bootstrap.Phase1Runtime`
- `fantasy_store.runtime.app_lock.AppInstanceLock`
- `fantasy_store.runtime.paths.AppPaths`
- `fantasy_store.runtime.resource_locator.ResourceLocator`
- `fantasy_store.domain.money.MoneyLiteral`
- `fantasy_store.domain.money.MoneyValue`
- `fantasy_store.persistence.connection.connect`
- `fantasy_store.persistence.connection.connect_readonly`
- `fantasy_store.persistence.migration.initialize_database`

## 2. Test結果

### Test framework

- `pytest 9.0.2`
- Repositoryへ `requirements-dev.txt` として記録

### 実行結果

```text
40 passed in 0.67s
```

### 主な確認内容

- Package import / compile成立
- 新規2DB DDL実行
- STRICT table作成
- `foreign_keys=ON`
- `busy_timeout=5000`
- `journal_mode=WAL`
- `synchronous=FULL`
- schema version取得
- 欠損tableを初回DBとして黙って再作成しない
- 未知の将来schema versionを拒否
- 未知将来版の検査時にjournal modeを書き換えない read-only gate
- Bootstrap directory作成順序
- 二重起動時にApplication directory初期化へ進まない
- OS file lockを別processから取得できない
- Lock解放後の再取得
- `MoneyLiteral` canonical境界
- significand 64桁境界
- exponent上限
- 非canonical leading/trailing zero拒否
- `10^1000000 + 1`を2 blockのまま正確保持
- carryを伴う加算
- 比較
- 数量999倍
- 複数Money合計
- canonical JSON保存・再読込
- SQLite価格派生キーによる正確なORDER BY
- Resource root外参照拒否

### 未実施Test

Phase 2以降の機能試験は指示書第10章に従い未実施。

Windows実機での`msvcrt.locking`分岐は現在の実行環境がWindowsではないため未実施。別process排他自体はPOSIX fallbackで自動試験済み。Windows release-targetでの再確認を環境依存確認として残す。

## 3. PoC結果

### SQLite STRICT table

- 実行環境 Python: `3.13.5`
- Python同梱SQLite: `3.46.1`
- `CREATE TABLE ... STRICT`: 成功
- 判定: **現開発環境では設計どおり成立**
- 留保: PyInstaller build成果物に同梱されるSQLiteでの最終確認はPhase 7で再実施する。

### MoneyValue疎block性能

試験入力:

- `10^1000000`
- `1`
- 上記2値の加算を10,000回反復

結果:

```text
elapsed: 0.015388 sec
result block count: 2
```

- 1,000,000桁相当の0列を展開していない。
- 判定: **現開発環境のPhase 1 PoCでは設計どおり成立**
- この数値は性能保証値ではない。

### WebView2 Runtime検出

- Phase 1ではFreeze詳細設計どおり、具体的な安定検出APIの確定を行っていない。
- 現環境はWindowsではないためWebView2可用性実測は対象外。
- `webview2_probe.py`は可用性を偽って成功返却せず、Windowsでは`DEFERRED`、非Windowsでは`NOT_APPLICABLE`を返すPhase 1境界として実装。
- pywebview採用版での正式PoCはPhase 7へ持ち越し。

## 4. 正本逸脱

**正本逸脱なし**

補足:

- 開発・CI試験を可能にするため、`AppInstanceLock`にはWindows `msvcrt.locking`に加えて非Windows用`flock` fallbackを実装している。Windows release-targetの方式はFreezeどおり`msvcrt.locking`であり、製品方式の置換ではない。
- 既存DBのschema version互換確認にはread-only接続を使用する。これは「未知の将来バージョンを現在アプリで開く場合は書込みを行わない」というFreeze要件を守るための実装である。

## 5. 残課題

### 次Phaseへ持ち越す事項

Phase 2の指示対象をそのまま持ち越す。

- `user_repository.py`
- `user_backup.py`
- migration実装拡張
- CRUD / transaction
- SQLite Backup API
- backup候補検証
- `recovery_hold`
- migration前backup
- snapshotディレクトリ運用

### 環境依存の再確認

- Windows実機での`msvcrt.locking`
- PyInstaller成果物のSQLite STRICT table
- WebView2 Runtime正式検出API

後二者は詳細設計第31.2節に従い該当後続Phaseで確認する。

### Known Issue

- Phase 2〜7は未実装。これは今回の実行範囲どおりであり、未実装処理を成功扱いしていない。
- `main.py`はPhase 1 foundation smoke runのみを行い、Bridge/UI/MVP完成を示すものではない。

### 技術的負債

Phase 1時点で新規の未承認技術的負債なし。

## 6. Phase 1 判定

**PASS（現開発環境）**

Phase 1の実装・自動試験・該当PoC記録を完了した。Phase 2以降へは自動進行せず、Human判断へ返す。

=== DOCUMENT END ===
