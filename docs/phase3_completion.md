# FantasyStore MVP Phase 3 完了記録

- Phase: **Phase 3 — `.vpack` Validation / Import Foundation**
- Status: **PASS / Phase 4開始判断待ち**
- 実施日: 2026-09-05
- Authority: Freeze済みプロジェクト仕様書 v1.1 / 基本設計書 v1.1 / 詳細設計書 v1.0 / MVP実装指示書 / Phase 3実装指示書

## 1. 実装概要

Phase 2承認済みRepositoryを基盤とし、外部入力`.vpack`を正式導入状態へ触れずに安全展開・検証し、Phase 4へ渡せる`PreparedPack`を生成するPhase 3境界を実装した。

実装範囲:

- ZIP source / metadata事前検証
- ZIP Slip / Windows path / Unicode / case collision / duplicate / special entry検証
- ZIP bomb上限検証
- `extractall()`を使わない1 memberずつのsafe extraction
- stream copy中の実書込みbyte再計測
- UTF-8 / UTF-8 BOM strict JSON loading
- duplicate JSON key / NaN / Infinity拒否
- Draft 2020-12 local JSON Schema検証
- Schema後Semantic validation
- Phase 1 `MoneyLiteral` / `price_sort_key()`再利用
- Pillowによる画像実体検証
- `ImageReferenceValidator`
- `PackPathResolver`
- `AssetResolver`
- Freeze済み規則による`content_digest`
- `items_master`投入用派生record準備（DB INSERTなし）
- `PreparedPack` / `ImportKind`生成
- failure時staging best-effort cleanup

Phase 4のinstalled切替、operation journal、Pack DB更新、Pack recovery、有効化/無効化は実装していない。

## 2. 新規ファイル

### Product code

- `fantasy_store/pack/__init__.py`
- `fantasy_store/pack/zip_safety.py`
- `fantasy_store/pack/json_loader.py`
- `fantasy_store/pack/schema_validator.py`
- `fantasy_store/pack/validator.py`
- `fantasy_store/pack/image_validator.py`
- `fantasy_store/pack/image_reference_validator.py`
- `fantasy_store/pack/path_resolver.py`
- `fantasy_store/pack/asset_resolver.py`
- `fantasy_store/pack/digest.py`
- `fantasy_store/pack/importer.py`
- `resources/schema/pack-v1.json`
- `resources/schema/items-v1.json`
- `requirements.txt`

### Test

- `tests/phase3_helpers.py`
- `tests/test_pack_json_schema.py`
- `tests/test_zip_safety.py`
- `tests/test_pack_image_reference.py`
- `tests/test_pack_digest_resolver.py`
- `tests/test_pack_semantic.py`
- `tests/test_pack_importer.py`

## 3. 既存ファイル変更

### `fantasy_store/config.py`

Phase 3のFreeze済み`.vpack`安全上限定数のみ追加。

- 512 MiB source
- 1 GiB expanded total
- 5,000 files
- 64 MiB single file
- 256 KiB `pack.json`
- 16 MiB `items.json`
- 100:1 compression ratio境界
- 40 MP image
- 64 KiB attributes JSON

Phase 1既存定数・Money設計は変更していない。

### `fantasy_store/domain/errors.py`

Phase 3 validation失敗を表す`PackValidationError`を追加。既存Error code/責務は変更していない。

### `pyproject.toml`

Phase 3 runtime dependencyを固定し、descriptionをPhase 3到達点へ更新。

### Phase 1 / 2既存責務への影響

- Bootstrap順序: **変更なし**
- AppInstanceLock: **変更なし**
- SQLite DDL: **変更なし**
- SQLite connection / PRAGMA: **変更なし**
- Schema Version / migration: **変更なし**
- MoneyLiteral / MoneyValue: **変更なし**
- UserRepository: **変更なし**
- Backup / Recovery: **変更なし**
- Path構成: **変更なし**

## 4. 主要Class / Function

- `PackImporter.prepare()` — Phase 3 orchestration。正式導入処理は行わない。
- `prevalidate_archive()` / `validate_zip_metadata()` — ZIP source/metadata validation。
- `safe_extract()` — 検証済みmemberのstream extraction。
- `load_strict_json()` — UTF-8/BOM + strict JSON。
- `LocalSchemaValidator` — ローカルDraft 2020-12 Schema。
- `PackSemanticValidator` — Money / ID / strings / attributes / image references / items_master派生。
- `ImageValidator` — Pillow verify / reopen / format / 40 MP。
- `ImageReferenceValidator` — JSON image reference独立再検証。
- `PackPathResolver` — staging / installed / asset path containment。
- `AssetResolver` — 将来runtime時のDB由来reference再防御。
- `compute_content_digest()` — Freeze済みdigest規則。
- `PreparedPack` / `PreparedItemRecord` — Phase 4 handoff structure。

## 5. Dependency

再現可能なversionとして固定:

- `Pillow==12.3.0`
- `jsonschema==4.26.0`
- Test: `pytest==9.0.2`（既存`requirements-dev.txt`）

Schemaは`resources/schema/`のローカルresourceだけを読み、remote `$id` fetchやonline resolverを使用しない。

## 6. Test結果

実行コマンド:

```bash
python -m pytest -q
```

結果:

- **176 PASS**
- **0 FAIL**
- Phase 1 / Phase 2 regression: **87 PASS**
- Phase 3 new tests: **89 PASS**

`pytest` executable直呼びではこの実行環境のscript import path差によりpackage importが成立しなかったため、Repository READMEで既定としている`python -m pytest`を使用した。製品コード障害ではない。

## 7. Security Validation結果

| 項目 | 結果 | 主な確認 |
|---|---|---|
| ZIP Slip / Path | PASS | `../`, absolute, drive, UNC, dot segment, empty segment, mixed slash, control, trailing dot/space, containment |
| Windows reserved | PASS | `CON`, `con.txt`, `NUL.png`, `COM1.webp`, `LPT9.jpg` 等 |
| Duplicate / Unicode | PASS | exact duplicate, NFC collision, Windows case-insensitive collision |
| Special entry | PASS | symlink, char/block device, FIFO, socket, Windows device/reparse metadata |
| ZIP Bomb | PASS | 512 MiB source, 1 GiB expanded, 5,000 files, 64 MiB member, pack/items individual limits, >100:1, compressed_size=0 |
| 実展開byte再計測 | PASS | metadataとは独立してsingle/total上限をstream側で再確認 |
| JSON | PASS | UTF-8, BOM, invalid UTF-8, UTF-16/32 BOM, malformed, duplicate key, NaN/Infinity |
| JSON Schema | PASS | schema_version, additionalProperties, required等。Draft 2020-12 local resource |
| Money | PASS | canonical zero, huge exponent, noncanonical significand, exponent超過, trailing zero。Phase 1 validator再利用 |
| Image | PASS | PNG/JPEG/WebP, extension偽装, corrupt, truncated, format mismatch, 40 MP, Pillow bomb protection |
| Image reference | PASS | assets外、missing、absolute/traversal/reserved、validated member実在、NFC ambiguity |
| Digest | PASS | deterministic, ZIP order independence, separator/NFC normalization, content/path change, lowercase 64 hex |
| AssetResolver | PASS | normal asset, traversal, absolute, other/missing Pack, packs root外 |

Item IDと`images[]`はFreeze Schema自体がASCII canonical patternを要求するため、Unicode NFC衝突は通常Schema段階より前後で成立しない入力もある。実装はSemantic / ImageReference境界にもNFC collision guardを保持し、ImageReference側はSchemaを迂回した直接境界Testでも確認した。

## 8. Safe Extraction

`zipfile.extractall()`はProduct codeで**不使用**。

処理:

```text
source size validation
→ ZIP metadata validation
→ normalized member validation
→ staging root作成
→ destination resolve containment
→ member単位stream read/write
→ actual single/total byte再計測
```

ZIP metadata上の`file_size`だけを成功根拠にしない。

## 9. Search Text派生

Freeze済み詳細設計は`search_text`を「商品名、カテゴリ、説明、検索対象となる単純attribute値を正規化して連結」と規定しているが、case foldingや互換分解等の追加変換は固定していない。

Phase 3では意味を変更する追加仕様を導入せず、以下の最小決定的処理とした。

- 各文字列をUnicode NFC
- attributeはkey順で決定化
- scalar値をcanonical representation化
- LFで決定的に連結

SQL検索自体はPhase 3では未実装。

## 10. Failure Injection結果

| 故障 | 結果 | staging | installed / DB |
|---|---|---|---|
| staging作成失敗 | PASS | 作成されない | 不変 |
| extraction write failure | PASS | cleanup | 不変 |
| stream read failure | PASS | cleanup | 不変 |
| JSON read failure | PASS | cleanup | 不変 |
| image decode failure | PASS | cleanup | 不変 |
| digest read failure | PASS | cleanup | 不変 |
| staging cleanup failure | PASS | 残存を許容し技術ログ | **元failureを成功化せず**、正式状態不変 |

既存Pack/user状態をsentinelで保持したvalidation failure Testでも、`packs/installed/`・Pack DB相当・user_data sentinelが変化しないことを確認した。

## 11. PoC / 実環境確認

環境:

- Python 3.13.5
- SQLite 3.46.1
- Linux x86_64 test container
- Pillow 12.3.0
- jsonschema 4.26.0

確認:

- Pillowの独自40 MP上限を実画像（6400 x 6400, 40.96 MP）で拒否。
- Pillow `MAX_IMAGE_PIXELS`閾値をTest内で縮小してdecompression bomb例外経路も拒否。
- 正常`.vpack` smokeで`PreparedPack`生成、digest 64 hex、staging保持、installed 0件を確認。

既存持越し:

- Windows実機`msvcrt.locking`
- PyInstaller成果物上のSQLite `STRICT`
- WebView2正式検出

Phase 3追加のWindows device/reparse ZIP metadataはsynthetic `ZipInfo`で境界検証した。Windows実機由来archiveの追加PoCはPhase 3完了の必須条件としては残していない。

## 12. 正本逸脱

**正本逸脱なし**

Phase 3で正本変更を必要とする不成立要件は発見していない。

## 13. 残課題 / 次Phase

Phase 4へ持ち越し:

- operation journal正式実装
- `STAGING / VALIDATED / FILES_SWITCHED / DB_SWITCHED`等のstate遷移
- `packs/installed/{pack_id}`正式切替
- Pack backup / same Pack ID完全置換
- `installed_packs` / `items_master`正式DB transaction
- Pack recovery
- `pack_manifest_state.json`
- `PackAccessCoordinator`
- enable / disable
- 起動時Pack Recovery

Phase 5以降のApplication Service、Bridge、UI、pywebview、packagingにも進んでいない。

## 14. 停止条件

Phase 3実装・Test・完了記録の提出時点で停止する。

> **Phase 4開始判断をHumanへ返す。**
