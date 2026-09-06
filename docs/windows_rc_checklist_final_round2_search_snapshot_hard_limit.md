# FantasyStore Phase 7 — Windows RC / UAT Checklist
# Final Fix Round 2 — Search State / Snapshot Hard Limit

対象: `phase7_final_fix_round2_search_state_snapshot_hard_limit` 最新Source

本ファイル作成環境はLinux。Windows項目は**NOT EXECUTED**で開始する。
PASS / FAIL / NOT EXECUTEDのいずれかを必ず記録すること。

## A. Round 2 Priority UAT

| # | Scenario | Result | Notes |
|---:|---|---|---|
| 1 | 最新SourceをWindows PowerShell 5.1からbuild | NOT EXECUTED | `build\\build_windows.ps1` |
| 2 | Distribution static verification | NOT EXECUTED | latest dist only |
| 3 | keyword検索後input保持 | NOT EXECUTED | |
| 4 | category検索後select保持 | NOT EXECUTED | regression |
| 5 | sort検索後select保持 | NOT EXECUTED | regression |
| 6 | min `123e4`検索後inputが`123e4`のまま | NOT EXECUTED | canonical再format禁止 |
| 7 | max検索後Human入力保持 | NOT EXECUTED | |
| 8 | min/max同時検索後両方保持 | NOT EXECUTED | |
| 9 | min/max clear後input空・条件なし | NOT EXECUTED | |
| 10 | malformed/negative price guard + input修正可能 | NOT EXECUTED | screen survives |
| 11 | e notation Helperが通常時1箇所のみ | NOT EXECUTED | `123e4（= 123 × 10⁴）` |
| 12 | 購入履歴Gallery regression | NOT EXECUTED | Human確認済みUI維持 |
| 13 | ショップ管理 regression | NOT EXECUTED | Human確認済みUI維持 |
| 14 | enabled/disabled Pack uninstall gating regression | NOT EXECUTED | |
| 15 | Money display regression | NOT EXECUTED | 9桁 / superscript / 約 |
| 16 | Snapshot directory actual size observation | NOT EXECUTED | stable <=512 MiB in normal FS |

Snapshot delete failure / over-limit fault injectionはAutomated Testで検証済み。Windows UATでfilesystemを故意破損する必要はない。

## B. Final UX / Store Management Regression

| Scenario | Result | Notes |
|---|---|---|
| 通常店舗にショップ管理なし | NOT EXECUTED | |
| `--store-manager`にショップ管理 | NOT EXECUTED | |
| 店長モード（復旧）にショップ管理なし | NOT EXECUTED | |
| History全Reset confirmation | NOT EXECUTED | |
| Reset後 History / Statistics 0 | NOT EXECUTED | |
| Reset後 Pack / Cart / settings保持 | NOT EXECUTED | |
| Cart image/name -> current detail | NOT EXECUTED | |
| History snapshot image/name -> current detail if resolvable | NOT EXECUTED | |
| current item absent -> snapshot維持 / linkなし | NOT EXECUTED | no current Pack fallback |
| Checkout confirmation unit price × qty / subtotal | NOT EXECUTED | |
| Purchase Complete unit price × qty / subtotal | NOT EXECUTED | |
| History 1 order = 1 card / gallery | NOT EXECUTED | |
| `点 / 商品` semantics | NOT EXECUTED | |

## C. Pack / Recovery Regression

| Scenario | Result | Notes |
|---|---|---|
| Historical IMPORT -> UPDATE supersede | NOT EXECUTED | |
| canonical NEW / Upgrade / Reinstall / Downgrade Skip | NOT EXECUTED | |
| Multi-Pack | NOT EXECUTED | |
| Enable / Disable | NOT EXECUTED | |
| Enabled Pack uninstall disabled | NOT EXECUTED | |
| Disabled Pack formal uninstall | NOT EXECUTED | |
| Uninstall restart / Recovery | NOT EXECUTED | |
| Legacy Version normal fail closed | NOT EXECUTED | if retained UAT data |
| Legacy Version store-manager restricted startup | NOT EXECUTED | if applicable |
| Recovery Safe Maintenance gating | NOT EXECUTED | |
| unknown generation / DB-FS mismatch diagnostic only | NOT EXECUTED | no write |

## D. Remaining Gate B — Runtime / Packaging

| Scenario | Result | Notes |
|---|---|---|
| pywebview Window | NOT EXECUTED | |
| WebView2 formal Probe AVAILABLE | NOT EXECUTED | |
| WebView2 missing safe-stop | NOT EXECUTED | |
| Notice startup/restart/bypass prevention | NOT EXECUTED | |
| Bridge roundtrip / fixed DTO | NOT EXECUTED | |
| fantasy-image handler / no OS path leak | NOT EXECUTED | |
| image displayed during same-Pack update | NOT EXECUTED | Windows file-lock PoC |
| Windows rename / replace | NOT EXECUTED | |
| native `.vpack` picker + cancel | NOT EXECUTED | |
| second instance / `msvcrt.locking` | NOT EXECUTED | |
| Checkout E2E / request_id idempotency | NOT EXECUTED | |
| User DB backup after Checkout / restore | NOT EXECUTED | |
| recovery_hold / no silent empty DB | NOT EXECUTED | |
| Pack unfinished operation Recovery | NOT EXECUTED | |
| SQLite STRICT frozen runtime | NOT EXECUTED | record SQLite version |
| Pillow / jsonschema packaged | NOT EXECUTED | |
| log rotation 5 MiB / 5 generations | NOT EXECUTED | |
| offline normal functions | NOT EXECUTED | |
| CSP positive / negative | NOT EXECUTED | |
| external navigation blocked | NOT EXECUTED | |
| clean shutdown / no zombie | NOT EXECUTED | |
| cwd variants | NOT EXECUTED | |
| Unicode / space path | NOT EXECUTED | |
| read-only program folder | NOT EXECUTED | |
| program folder replacement / data continuity | NOT EXECUTED | |
| Python未導入PC | NOT EXECUTED | if available |
| Node.js absent on release PC | NOT EXECUTED | runtime must not require Node |

## E. Release Gate

```text
Automated Tests: Python 458 + JavaScript 51 = 509 PASS / 0 FAIL
compileall: PASS
Windows Build (latest source): NOT EXECUTED
Windows UAT (latest source): NOT EXECUTED
Remaining Gate B: PRESENT
Independent Review: NOT EXECUTED
Release Candidate: HOLD
```

=== DOCUMENT END ===
