# FantasyStore

**FantasyStore** は、架空の商品を眺め、カートに入れ、購入した気分を楽しむための  
**Windows向け架空ショッピング・シミュレーションアプリ**です。

> **実際の購入・決済・請求・配送は行われません。**

商品データは `.vpack` 形式の「商品Pack」として追加できます。  
通常利用時はネットワーク接続や外部APIを必要とせず、ローカルPC上で動作します。

---

## 主な機能

- 商品一覧・商品検索
- カテゴリ / 価格条件 / 並び順による絞り込み
- 商品詳細表示
- ショッピングカート
- 架空の購入処理
- 購入履歴・購入統計
- 購入時の商品情報・画像Snapshot
- `.vpack` 商品Packの追加
- 商品Packの有効化 / 無効化
- 商品Packの更新 / 再導入
- 商品Packのアンインストール
- 店長モードによるショップ管理
- 購入履歴の一括リセット
- 巨大な架空価格にも対応したexact Money処理
- 購入履歴画像Snapshotの容量上限（512 MiB）
- Pack / DB異常時の安全停止・復旧用店長モード

---

## 動作環境

### 配布版

- Windows
- Microsoft Edge WebView2 Runtime

PyInstallerの **onedir** 形式で動作するため、配布版を利用する場合は
`FantasyStore.exe` だけを取り出さず、`FantasyStore` フォルダ全体を保持してください。

利用者がPythonやNode.jsを別途導入する必要はありません。

### Sourceからbuildする場合

- Windows
- Python 3.11 以上
- Node.js / npm
- Microsoft Edge WebView2 Runtime

---

## 通常起動

配布版では次を起動します。

```text
FantasyStore.exe
```

通常起動では、商品一覧・カート・購入履歴などの店舗UIを利用できます。

管理機能は通常画面には表示されません。

---

## 店長モード

商品Packの管理や店舗データの保守を行う場合は、次の引数を付けて起動します。

```text
FantasyStore.exe --store-manager
```

店長モードでは「ショップ管理」が利用できます。

### 商品Pack管理

- `.vpack` の導入
- Pack Version / 状態の確認
- 有効化 / 無効化
- 無効化済みPackのアンインストール

### 店舗データ管理

- 購入履歴をすべてリセット

購入履歴のリセットでは、注文履歴・購入統計・購入時の商品Snapshotを削除します。

以下は削除されません。

- 商品Pack
- カート
- 設定

`--store-manager` は認証やアクセス制御ではなく、ローカルアプリのUI Mode切替です。

---

## 商品Pack `.vpack`

FantasyStoreの商品は `.vpack` 形式のPackとして追加できます。

基本構造は次の形式です。

```text
example.vpack
├─ pack.json
├─ items.json
└─ assets/
```

現在のSourceには、Pack validationで使用するJSON Schemaが含まれています。

```text
resources/schema/pack-v1.json
resources/schema/items-v1.json
```

`.vpack` は **Portable Product Pack Format** として仕様を公開しています。

正式仕様:

[`docs/vpack_Specification_v1.0.md`](docs/vpack_Specification_v1.0.md)

`.vpack Specification v1.0` は、FantasyStore本体の内部実装から独立したPack Formatを定義しています。  
Packを自作する場合や、独自のProducer / Consumerを実装する場合は、このSpecificationを参照してください。

Specification本文がv1 FormatのNormative Authorityであり、JSON SchemaやFantasyStoreのValidatorはその検証・実装用artifactです。

### Pack Version

Pack Versionは次の形式です。

```text
MAJOR.MINOR
```

例:

```text
0.0
1.0
1.10
999.999
```

同じPack IDが導入済みの場合:

- 新しいVersion → Upgrade
- 同じVersion → Reinstall
- 古いVersion → 導入をSkip

Legacy形式をアプリが自動変換することはありません。

---

## Packのアンインストール

有効な商品Packは直接アンインストールできません。

```text
有効
↓
無効化
↓
アンインストール
```

の順で操作します。

Packをアンインストールしても、過去の購入履歴本文や購入時の商品情報は保持されます。

---

## 購入履歴

購入履歴は **1注文 = 1カード** で表示されます。

注文内の商品について、

- 購入時の商品名
- 購入時価格
- 数量
- 小計
- 購入時の商品画像

などをSnapshotとして保持します。

現在の商品Packが削除されていても、購入履歴本文は現在の商品データへ置き換えません。

---

## 購入履歴画像の容量制御

購入履歴用画像Snapshotには、合計 **512 MiB** のHard Limitがあります。

```text
536,870,912 bytes
```

容量が必要になった場合は、古い購入履歴画像から順に画像ファイルだけを退役させます。

画像が退役しても、以下は保持されます。

- 注文履歴
- 商品名
- 購入時価格
- 数量
- 注文合計
- 購入統計

画像を安全に追加できない場合も、購入履歴本文は作成され、画像部分にはplaceholderを表示します。

現在の商品画像を「購入時画像」として代用することはありません。

---

## Money

FantasyStoreでは、通常の価格だけでなく非常に大きな架空価格も扱えます。

通常価格:

```text
3,980 円
12,800 円
```

巨大価格:

```text
1 × 10¹⁶ 円
123456789 × 10²⁵ 円
約 123456790 × 10²¹ 円
```

Moneyの内部値・購入計算・統計計算ではJavaScriptの浮動小数点Numberを正本として使用しません。

価格検索ではe記法も使用できます。

```text
123e4 = 123 × 10⁴
```

---

## データ保存場所

利用者データはプログラム本体とは分離され、次の場所へ保存されます。

```text
%LOCALAPPDATA%\FantasyStore
```

主に以下が含まれます。

- 商品Pack管理データ
- カート
- 購入履歴
- 購入時画像Snapshot
- 設定
- ログ

そのため、プログラム本体のフォルダを置き換えても利用者データは別途保持されます。

利用者データも完全に削除したい場合は、FantasyStore終了後に
`%LOCALAPPDATA%\FantasyStore` を削除してください。

---

## Sourceからのbuild

Repository rootでPowerShellを開きます。

```powershell
# このPowerShell processだけExecutionPolicyを緩める
Set-ExecutionPolicy -Scope Process Bypass

# build用venv
py -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1

# Version確認
python --version
node --version
npm.cmd --version

# Windows build
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
```

build scriptは以下を実行します。

1. build成果物のclean
2. Python依存関係の導入
3. Python / JavaScript regression test
4. PyInstaller onedir build
5. distribution shape verification

成功すると次に成果物が生成されます。

```text
dist\FantasyStore\
```

---

## Test

Python:

```powershell
python -m pytest -q
```

JavaScript / DOM:

```powershell
npm.cmd run test:ui
```

compile check:

```powershell
python -m compileall -q fantasy_store tests
```

---

## 技術構成

- Python
- pywebview
- Microsoft Edge WebView2
- SQLite
- HTML / CSS / JavaScript
- Pillow
- jsonschema
- PyInstaller

FantasyStore本体にはAI機能や外部AI API接続はありません。

---

## セキュリティ / 安全設計

`.vpack` 取込では、少なくとも以下を検証します。

- ZIP path safety
- file / expanded size limits
- file count limits
- JSON Schema
- Pack / Item ID
- Pack Version
- duplicate identity
- image validation
- image pixel limits
- content digest

PackやDBの状態を安全に確定できない場合は、通常起動を継続せずfail closedします。

必要な場合のみ店長モード（復旧）へ入り、安全に確認できる範囲の保守操作だけを許可します。

---

## 注意事項

FantasyStoreはジョーク／デモ・シミュレーション用途のアプリです。

**実際の購入・決済・請求・配送は一切行いません。**

制作者が公式に配布するサンプル商品コンテンツには、AIを利用して生成した架空の商品が含まれる場合があります。

第三者が作成・配布する商品Packについては、そのPack作成者が提供する情報をご確認ください。

---

## License

FantasyStoreのSource codeは **MIT License** で公開しています。

詳細は [`LICENSE`](LICENSE) を参照してください。

---

## Repository

このRepositoryはFantasyStore本体のSource codeと、公開Pack Formatである
**`.vpack Specification v1.0`** を公開しています。

- FantasyStore Source code — MIT License
- `.vpack Specification v1.0` — [`docs/vpack_Specification_v1.0.md`](docs/vpack_Specification_v1.0.md)

`.vpack` 作成支援などの関連ツールは、FantasyStore本体および公開Formatとは別に扱います。
