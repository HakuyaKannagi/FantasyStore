> **Historical Phase 7 source-candidate record.** Windows実機UAT開始後の差戻し修正状態は `phase7_uat_fix_completion.md` を正として参照してください。

# 利用者向け架空ECシステム MVP Phase 7 完了記録

## 1. Overall Judgment

Phase 7 指示書に従い、Phase 6承認済みRepositoryを基盤として Integration / Packaging / Release Candidate 境界を実装した。

| Gate / 判定 | 結果 | 備考 |
|---|---|---|
| Gate A — Integration implementation | COMPLETE | pywebview runtime、WebView2 probe、native picker、image handler、shutdown、CSP、build spec/script、static audit、自動Testを実装済み |
| Gate A — Windows onedir build execution | NOT EXECUTED | 現作業環境はLinux。PyInstallerはcross-compilerではなく、Windows buildはWindows上で実行する必要がある |
| Gate A overall | NOT COMPLETE | 指示書 §4 の「build生成成功」は未確認 |
| Gate B — Windows RC Validation | NOT EXECUTED | Windows実機なし。`docs/windows_rc_checklist.md` 全項目を明示記録 |
| Phase 7 | **HOLD — Windows RC Validation Required** | 未確認事項をPASS扱いしない |
| Release Candidate | **NOT CREATED** | `FantasyStore_RC.zip`は作成しない |

Phase 7を`PASS`とは判定しない。

---

## 2. Dependency

### 2.1 採用・固定

```text
Python (development host): 3.13.5
pywebview==6.2.1
PyInstaller==6.22.2
pyinstaller-hooks-contrib==2026.7
Pillow==12.3.0
jsonschema==4.26.0
SQLite (development host): 3.46.1
```

- `pywebview==6.2.1` はPhase 7 Windows runtime dependencyとしてexact pin。
- `PyInstaller==6.22.2` はbuild dependencyとして分離しexact pin。
- Node.jsはUI Test用のみで製品runtime dependencyではない。
- Windows側の実WebView2 Runtime versionは **NOT EXECUTED / unknown**。

### 2.2 WebView2配布方針

```text
WebView2 installer not bundled
```

アプリ自身による自動Downloadは実装していない。system-installed Evergreen WebView2 Runtimeをformal Probeし、不足時はnative safe-stopする設計とした。

---

## 3. 実装概要

### 3.1 Runtime integration

新規／主要変更:

```text
fantasy_store/runtime/webview2_probe.py
fantasy_store/runtime/webview_runtime.py
fantasy_store/runtime/webview_image_handler.py
fantasy_store/runtime/webview_file_picker.py
fantasy_store/runtime/ui_dispatcher.py
fantasy_store/runtime/managed_bridge.py
fantasy_store/bootstrap.py
fantasy_store/main.py
```

実装内容:

- Phase 6 `BridgeApi` を実pywebview `js_api`へ接続するruntime境界
- fixed 15 public APIと同じsurfaceだけを持つ`ManagedBridgeApi`
- Window close時:
  1. 新規Bridge call受付停止
  2. native picker/UI notification停止
  3. 進行中Bridge worker完了待ち
  4. Phase 1〜6 runtime / DB / AppInstanceLock解放
- Release runtime設定:
  - `ALLOW_FILE_URLS=False`
  - `OPEN_EXTERNAL_LINKS_IN_BROWSER=False`
  - `OPEN_DEVTOOLS_IN_DEBUG=False`
  - `REMOTE_DEBUGGING_PORT=None`
  - `debug=False`
- top-level navigationは最初に確立したlocalhost/127.0.0.1 originだけ許可
- arbitrary external/new-window navigationはnative WebView2 handlerで遮断

### 3.2 Formal WebView2 Probe

Phase 1の`DEFERRED`境界は回帰互換のため残し、Phase 7用formal Probeを追加。

Status:

```text
AVAILABLE
UNAVAILABLE
ERROR
NOT_APPLICABLE
```

- 非Windows: `NOT_APPLICABLE`
- runtime version取得成功: `AVAILABLE`
- runtime not found: `UNAVAILABLE`
- 判定不能: `ERROR`
- raw COM/HRESULT/registry detailを利用者向け結果へ含めない
- Windowsではpywebview同梱WebView2 interopを使用し、system-installed runtimeを問い合わせる

### 3.3 Native fatal safe-stop

`main.py`で以下を通常EC Window起動前に停止可能とした。

- second instance
- WebView2 missing
- WebView2 probe error
- Domain recovery/schema failure
- unrecoverable startup error

利用者dialogへstack trace / raw OS exceptionを出さない。

### 3.4 Native file picker

`PyWebViewFilePicker`を実装。

- `FileDialog.OPEN`
- single selection
- `.vpack` filter
- absolute pathはPython内部だけ
- Bridge公開`import_pack()`は引数なしのまま
- Window close後は新規dialog処理を拒否
- CancelはPhase 6どおり正常`CANCELLED`

### 3.5 `fantasy-image://` resource handler

`fantasy-image://resource/<encoded opaque ref>`をWebView2へ接続するWindows handlerを実装。

処理:

```text
request URL
→ scheme/host/path validation
→ strict percent decode exactly once
→ LocalImageResolver
→ managed source validation
→ read bytes completely
→ source file handle close
→ Pillow revalidation / MIME determination
→ WebView2 MemoryStream response
```

安全境界:

- `file://` pathをWebViewへ返さない
- absolute OS pathをBridge/JSへ返さない
- malformed percent escape拒否
- double encoded dataを二重decodeしない
- source extensionだけでMIMEを決定しない
- missing/corrupt imageはimage requestだけ404へ倒す
- response作成前にPack/snapshot source fileを閉じる

Windows WebView2でのcustom scheme実描画・file-lock PoCはGate Bへ残る。

### 3.6 Worker / UI notification / shutdown

- pywebview Bridge呼出しは採用versionのthreaded API境界を利用。
- runtime-originated通知用に`UiDispatcher`を追加。
- payloadはJSON serializerを通し、untrusted文字列をquoted JS stringへ直接連結しない。
- close後callbackは拒否。
- `ManagedBridgeApi`によりin-flight call完了前にAppInstanceLock/DBを解放しない。

### 3.7 CSP

`ui/index.html`に最終policyを追加。

```text
default-src 'self';
script-src 'self';
style-src 'self';
img-src 'self' fantasy-image:;
object-src 'none';
frame-src 'none';
base-uri 'none';
form-action 'none';
connect-src 'none';
```

含めていないもの:

- `'unsafe-eval'`
- `'unsafe-inline'`
- `*`
- `http:`
- `https:`

実WebView2上でのCSP positive/negative確認はGate B。

### 3.8 ResourceLocator

Phase 6以前の`ResourceLocator`を継続利用。

- source: repository resource root
- frozen: `sys._MEIPASS`
- CWD非依存
- resource root containment維持

persistent dataは引き続き`%LOCALAPPDATA%\FantasyStore`側であり、bundle directoryへ保存しない。

### 3.9 Packaging files

追加:

```text
build/fantasy_store.spec
build/build_windows.ps1
build/verify_dist.ps1
requirements-build.txt
docs/RELEASE_README.md
docs/windows_rc_checklist.md
docs/third_party_phase7.md
```

PyInstaller構成:

- **onedir only**
- executable: `FantasyStore.exe`
- `console=False`
- `debug=False`
- PyInstaller 6 `_internal` contents layoutを明示
- UI / local JSON Schema / Release READMEをbundle対象へ追加
- test / pytest / persistent DB / sample Packを配布対象にしない
- pywebview EdgeChromium dynamic moduleを明示retain

Build script:

1. Windowsであることを確認
2. `build/work` / `dist` clean
3. exact dependency install
4. Python + JS全Test
5. PyInstaller `--clean` onedir build
6. user-facing README配置
7. static dist verification

---

## 4. Build

### 4.1 Current execution host

```text
OS: Linux 6.18.35 x86_64
Architecture: x86_64
Python: 3.13.5
SQLite: 3.46.1
```

### 4.2 Windows build command

```powershell
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

### 4.3 Build execution result

```text
Windows onedir build: NOT EXECUTED
FantasyStore.exe: NOT GENERATED
FantasyStore_RC.zip: NOT CREATED
Size: N/A
RC SHA-256: N/A
```

理由:

- 現環境はLinuxでWindows実機を提供しない。
- PyInstallerはWindows artifactをLinuxからcross-buildする方式ではない。
- 現sandboxには`pywebview` / `PyInstaller`自体も導入されていない。
- Freeze安全機能を除外して場当たり的にbuildを通すことは行っていない。

これはコード要件の変更理由ではなく、Gate A build execution / Gate Bの環境依存未実施事項である。

---

## 5. Automated Test

### 5.1 Result

```text
Python:     376 PASS / 0 FAIL / 0 SKIP
JavaScript:  28 PASS / 0 FAIL / 0 SKIP
Total:      404 PASS / 0 FAIL / 0 SKIP
```

Baseline:

```text
Phase 1〜6: 387 PASS
Phase 7 new Python tests: 17 PASS
```

既存Testは削除・skip・xfail化していない。

### 5.2 Phase 7 new automated coverage

- WebView2 formal Probe:
  - non-Windows
  - available
  - blank/missing
  - wrapped runtime-not-found
  - unexpected error without raw leak
- native file picker:
  - cancel
  - single selection
  - shutdown rejection
- `ManagedBridgeApi`:
  - public surface exactly 15 APIs
  - shutdown rejects new calls
  - waits for in-flight call
- navigation restriction:
  - exact local origin
  - alternate port reject
  - `https://` reject
  - `file://` reject
  - `javascript:` reject
- release pywebview settings
- UI dispatcher JSON serialization / close
- image URL strict single decode
- malformed percent/scheme reject
- image bytes/MIME validation
- missing/corrupt image safe 404
- image response後にsource file rename可能（development-host file handle close proof）
- frozen `_MEIPASS` ResourceLocator
- PyInstaller spec static validation
- `_internal` dist verification layout
- CSP static validation

### 5.3 Windows-only / Manual

**NOT EXECUTED**。

詳細は`docs/windows_rc_checklist.md`。

---

## 6. Windows PoC

| Item | Result |
|---|---|
| `msvcrt.locking` process exclusion | NOT EXECUTED |
| Windows directory rename / replace | NOT EXECUTED |
| WebView2 image displayed during Pack update | NOT EXECUTED |
| WebView2 file-handle interference | NOT EXECUTED |
| formal WebView2 Probe on real Windows | NOT EXECUTED |
| pywebview Bridge roundtrip | NOT EXECUTED |
| native file picker real dialog | NOT EXECUTED |
| worker notification on actual Window | NOT EXECUTED |
| SQLite STRICT in PyInstaller runtime | NOT EXECUTED |
| log rotation in dist runtime | NOT EXECUTED |

Development-hostで確認できた範囲:

- image bytes読切り後source handleを保持しないこと
- pack/file resourceのopaque ref boundary
- shutdown/in-flight worker ordering
- same-process static navigation/CSP policy

POSIX結果をWindows PoCの代用にはしていない。

---

## 7. RC Scenario

以下は全てGate Bで実施する。

| Scenario | Result |
|---|---|
| first run | NOT EXECUTED |
| Notice first run / restart / bypass prevention | NOT EXECUTED |
| Pack 0 | NOT EXECUTED |
| all disabled | NOT EXECUTED |
| search 0 | NOT EXECUTED |
| import | NOT EXECUTED |
| update | NOT EXECUTED |
| image displayed during update | NOT EXECUTED |
| Checkout | NOT EXECUTED |
| History | NOT EXECUTED |
| offline | NOT EXECUTED |
| Pack Recovery | NOT EXECUTED |
| user DB backup restore | NOT EXECUTED |
| second instance | NOT EXECUTED |
| clean shutdown / no zombie | NOT EXECUTED |
| Python absent | NOT EXECUTED |

---

## 8. Security

### 8.1 Static / automated result

PASS:

- Phase 6 XSS tests regression
- fixed Bridge surface 15 APIs
- Managed Bridge surface 15 APIs
- dynamic dispatchなし
- JS arbitrary local path importなし
- absolute pathをBridge DTOへ追加していない
- image custom scheme strict parse
- image managed resolver再validation
- `file://` access disabled in release settings
- top-level external navigation guard
- new-window blocked
- external browser open setting disabled
- remote debug disabled
- debug mode false
- CSP no unsafe-eval / unsafe-inline
- UI runtime external HTTP(S) resourceなし
- `fetch` / XMLHttpRequest / WebSocket / EventSource runtime依存なし
- Money JS Number化なし
- History current Pack image fallbackなし

### 8.2 Gate B required security confirmation

NOT EXECUTED:

- WebView2 CSP actual enforcement
- custom scheme actual WebView2 behavior
- WebView2 new-window/native navigation actual behavior
- Pack image file-lock interaction
- PyInstaller artifact path leak / hidden import audit at runtime

重大Security項目を推測でPASSにしていない。

---

## 9. WebView2 Distribution

```text
Runtime requirement: system-installed Microsoft Edge WebView2 Runtime
Missing behavior: formal probe -> native safe-stop; normal UI is not started
Installer bundled: no
Auto-download: no
```

Standalone/Bootstrapperそのものは今回のsource/distributionへ含めていない。

---

## 10. Third-party inventory

Phase 7で追加:

- pywebview 6.2.1 — BSD 3-Clause
- PyInstaller 6.22.2 — GPLv2-or-later + PyInstaller bootloader/distribution exception
- pyinstaller-hooks-contrib 2026.7 — build support dependency

既存:

- Pillow 12.3.0
- jsonschema 4.26.0

最終Windows RC配布時の実bundle license notice completenessはGate B/build artifactに対して再確認する。

---

## 11. Phase 1〜6変更箇所

既存業務意味論を変更していない。

変更はPhase 7接続上必要な最小範囲:

- `bootstrap.py`: Phase7 runtime boundary追加、Phase6 bootstrap failure logging追加
- `main.py`: Phase7 startup / fatal safe-stop / pywebview起動
- `ui/index.html`: CSP meta追加
- `requirements.txt`: `pywebview==6.2.1`追加
- `pyproject.toml`: Phase7 runtime dependency反映

変更していない主要Freeze事項:

- Money表現・演算
- 2DB DDL
- User Repository / Backup / Recovery
- Pack Validation
- Pack Lifecycle / observation-based Recovery
- Lock順序
- Checkout transaction / idempotency
- History snapshot正本
- Bridge 15 API名 / DTO意味
- Phase 6 Notice意味

---

## 12. Human追加Notice

Phase 6承認済みNoticeをそのまま維持。

- 架空ショッピング体験
- ジョーク／デモ・シミュレーション
- 実購入なし
- 実決済なし
- 実請求なし
- 実配送なし
- 公式サンプル商品コンテンツのみAI生成scope
- 第三者PackをAI生成と断定しない
- Window起動ごとに通常操作前Notice

Gate Bの実Window上確認はNOT EXECUTED。

---

## 13. Known Issue / Remaining Work

Phase 7をRelease Candidateへ進めるにはWindowsで次を実行する必要がある。

1. `build/build_windows.ps1`
2. PyInstaller onedir生成成功
3. `build/verify_dist.ps1`
4. `docs/windows_rc_checklist.md`全項目
5. Gate B必須項目の`NOT EXECUTED`を0件にする
6. 実distのsize / SQLite / WebView2 version / SHA-256を記録
7. PASS時のみ`FantasyStore_RC.zip`を作成

重大問題をKnown Issueへ降格していない。

---

## 14. 正本逸脱

**正本逸脱なし**

Phase 7指示書どおり、Windows未確認事項を確認済みと報告していない。

---

## 15. Final

```text
Gate A implementation: COMPLETE
Gate A Windows build execution: NOT EXECUTED
Gate A overall: NOT COMPLETE
Gate B: NOT EXECUTED
Phase 7: HOLD — Windows RC Validation Required
Release Candidate: NOT CREATED
```

次の判断は、Windows環境でGate A build executionおよびGate Bを実施するHumanへ返す。
