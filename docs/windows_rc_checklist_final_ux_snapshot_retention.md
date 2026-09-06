# FantasyStore Phase 7 — Windows RC / UAT Checklist
# Final UX / Shop Management / Snapshot Retention

対象: `phase7_final_ux_store_management_snapshot_retention_fix` 最新Source

本ファイル作成環境はLinux。以下のWindows項目は**NOT EXECUTED**で開始する。
PASS / FAIL / NOT EXECUTEDのいずれかを必ず記録すること。

## A. Latest Final Fix — Priority UAT

| # | Scenario | Result | Notes |
|---:|---|---|---|
| 1 | 最新SourceをWindows PowerShell 5.1からbuild | NOT EXECUTED | `build\\build_windows.ps1` |
| 2 | Distribution static verification | NOT EXECUTED | latest dist only |
| 3 | 通常店舗起動 / Notice / Catalog regression | NOT EXECUTED | |
| 4 | 通常店舗にショップ管理導線がない | NOT EXECUTED | |
| 5 | `--store-manager`で通常店長モード | NOT EXECUTED | |
| 6 | 店長モードに「ショップ管理」表示 | NOT EXECUTED | |
| 7 | ショップ管理 > 商品Pack管理 | NOT EXECUTED | import/version/enable/disable/uninstall |
| 8 | ショップ管理 > 店舗データ管理 | NOT EXECUTED | |
| 9 | 購入履歴Reset confirmation | NOT EXECUTED | irreversible notice |
| 10 | History Reset後 History 0件 | NOT EXECUTED | |
| 11 | History Reset後 Statistics 0 / 0点 / 0円 | NOT EXECUTED | |
| 12 | History Reset後Pack不変 | NOT EXECUTED | |
| 13 | History Reset後Cart不変 | NOT EXECUTED | |
| 14 | History Reset後settings不変 | NOT EXECUTED | |
| 15 | History Reset後snapshot images削除 | NOT EXECUTED | |
| 16 | 店長モード（復旧）にショップ管理なし | NOT EXECUTED | restricted UI only |
| 17 | Cart画像 -> current product detail | NOT EXECUTED | available item |
| 18 | Cart商品名 -> current product detail | NOT EXECUTED | available item |
| 19 | Checkout confirmationで単価 × 数量 / 小計 | NOT EXECUTED | |
| 20 | Purchase Completeで単価 × 数量 / 小計 | NOT EXECUTED | |
| 21 | History list: 1 order = 1 card | NOT EXECUTED | |
| 22 | History list: 各line snapshot画像1枚 | NOT EXECUTED | quantity copiesなし |
| 23 | History list: `点 / 商品` 表記 | NOT EXECUTED | total quantity / line count |
| 24 | History list: representative + `ほかN商品` | NOT EXECUTED | |
| 25 | History snapshot画像 -> current detail when resolvable | NOT EXECUTED | |
| 26 | History snapshot商品名 -> current detail when resolvable | NOT EXECUTED | |
| 27 | current item absent: snapshot維持 / linkなし | NOT EXECUTED | no current Pack fallback |
| 28 | History detail image/name current detail link | NOT EXECUTED | resolvable only |
| 29 | Money normal: 3,980 円 / 12,800 円 | NOT EXECUTED | |
| 30 | exponent-source small value -> normal integer | NOT EXECUTED | e.g. 9e3 -> 9,000 円 |
| 31 | Money >=10^16 -> superscript exponent | NOT EXECUTED | caret表示なし |
| 32 | Money coefficient最大9桁保持 | NOT EXECUTED | |
| 33 | exact exponent display -> `約`なし | NOT EXECUTED | |
| 34 | rounded exponent display -> `約`あり | NOT EXECUTED | |
| 35 | Huge Moneyでhorizontal layout破壊なし | NOT EXECUTED | |
| 36 | Search helper `123e4（= 123 × 10⁴）` | NOT EXECUTED | |
| 37 | malformed/negative price guard | NOT EXECUTED | screen survives |
| 38 | Snapshot retention directory size observation | NOT EXECUTED | target <=512 MiB stable state |
| 39 | retention退役画像 -> history placeholder | NOT EXECUTED | no current Pack fallback |
| 40 | retention後History本文/Statistics保持 | NOT EXECUTED | |

## B. Pack / Recovery Regression

| Scenario | Result | Notes |
|---|---|---|
| Historical IMPORT -> UPDATE supersede | NOT EXECUTED | old journal誤RECOVERY_REQUIREDなし |
| Pack Version canonical NEW | NOT EXECUTED | e.g. 1.0 |
| Upgrade | NOT EXECUTED | |
| Same Version Reinstall | NOT EXECUTED | |
| Downgrade Skip no mutation | NOT EXECUTED | |
| Multi-Pack | NOT EXECUTED | |
| Enable / Disable | NOT EXECUTED | |
| Enabled Pack uninstall disabled | NOT EXECUTED | |
| Disabled Pack formal uninstall | NOT EXECUTED | |
| Uninstall restart no resurrection | NOT EXECUTED | |
| Uninstall Recovery | NOT EXECUTED | |
| Legacy Version normal boot fail closed | NOT EXECUTED | existing UAT data if retained |
| Legacy Version `--store-manager` restricted startup | NOT EXECUTED | if applicable |
| Recovery Safe Maintenance gating | NOT EXECUTED | safe generation only |
| unknown generation Diagnostic Only | NOT EXECUTED | no write |
| DB/FS mismatch Diagnostic Only | NOT EXECUTED | no write |

## C. Remaining Gate B — Runtime / Packaging

| Scenario | Result | Notes |
|---|---|---|
| pywebview Window | NOT EXECUTED | |
| WebView2 formal Probe AVAILABLE | NOT EXECUTED | |
| WebView2 missing safe-stop | NOT EXECUTED | no normal UI |
| Notice startup / restart / bypass prevention | NOT EXECUTED | |
| Bridge roundtrip / fixed DTO | NOT EXECUTED | |
| fantasy-image resource handler | NOT EXECUTED | no OS path leak |
| Pack image displayed during same-Pack update | NOT EXECUTED | file lock PoC |
| Windows directory rename / replace | NOT EXECUTED | staging/backup/installed |
| native `.vpack` picker + cancel | NOT EXECUTED | |
| second instance / `msvcrt.locking` | NOT EXECUTED | |
| first process close -> next launch possible | NOT EXECUTED | |
| Checkout E2E | NOT EXECUTED | Catalog -> detail -> cart -> fake purchase -> history |
| Checkout request_id retry idempotency | NOT EXECUTED | |
| User DB backup after checkout | NOT EXECUTED | |
| User DB backup restore | NOT EXECUTED | |
| recovery_hold / no silent empty DB | NOT EXECUTED | |
| Pack unfinished operation Recovery | NOT EXECUTED | |
| SQLite STRICT in frozen runtime | NOT EXECUTED | record SQLite version |
| Pillow packaged image validation | NOT EXECUTED | |
| jsonschema Draft 2020-12 packaged | NOT EXECUTED | |
| log rotation 5 MiB / 5 generations | NOT EXECUTED | `%LOCALAPPDATA%` |
| offline all normal functions | NOT EXECUTED | |
| CSP positive | NOT EXECUTED | UI/modules/bridge/images |
| CSP negative inline/external/unauthorized scheme | NOT EXECUTED | |
| external navigation blocked | NOT EXECUTED | http/https/file/javascript/unknown |
| clean shutdown / no zombie process | NOT EXECUTED | |
| close during Pack operation remains recoverable | NOT EXECUTED | |
| cwd Desktop | NOT EXECUTED | |
| cwd Downloads / unrelated dir | NOT EXECUTED | |
| Unicode path | NOT EXECUTED | |
| space in release path | NOT EXECUTED | |
| read-only program folder | NOT EXECUTED | persistent writes outside bundle |
| program folder replacement / data continuity | NOT EXECUTED | `%LOCALAPPDATA%` preserved |
| Python未導入PC | NOT EXECUTED | if available |
| Node.js absent on release PC | NOT EXECUTED | runtime must not require Node |

## D. Release Gate

```text
Windows Build (latest source): NOT EXECUTED
Windows UAT (latest source): NOT EXECUTED
Remaining Gate B: PRESENT
Independent Review: NOT EXECUTED
Release Candidate: HOLD
```

=== DOCUMENT END ===
