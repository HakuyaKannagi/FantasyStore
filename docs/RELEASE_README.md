# FantasyStore — 利用者向け架空ECシステム MVP

FantasyStoreは、**架空のショッピング体験を楽しむためのジョーク／デモ・シミュレーションアプリ**です。実際の購入、決済、請求、配送は行われません。

制作者が公式に配布するサンプル商品コンテンツは、AIを利用して生成された架空の商品です。第三者が作成・配布した追加パックについては、その作成者が提供する内容をご確認ください。

## 対応環境

- Windows向け
- Microsoft Edge WebView2 Runtimeが必要です。
- Windows RC検証完了後、一般利用者はPython / Node.jsを別途用意しない配布形態を予定しています。
- WebView2 Runtimeが導入済みなら通常機能はオフライン完結する設計です。

## 通常起動

展開した`FantasyStore`フォルダ内の`FantasyStore.exe`を起動します。exeだけを別の場所へ移動せず、onedirフォルダ全体を保持してください。

通常起動は買い物体験用の店舗UIです。商品一覧、カート、購入履歴が表示され、管理機能は表示されません。

## 店長モード / ショップ管理

管理・保守が必要な場合だけ次の起動引数を付けます。

```text
FantasyStore.exe --store-manager
```

これは認証・権限管理ではなくUI Mode切替です。

正常な店長モードでは**ショップ管理**が表示されます。

- 商品Pack管理
  - Pack導入
  - enable / disable
  - disabled Packのアンインストール
- 店舗データ管理
  - 購入履歴をすべてリセット

購入履歴Resetは個別削除ではなく全Resetのみです。注文履歴、購入統計、購入時の商品情報、履歴用保存画像を削除します。商品Pack、Cart、設定は削除しません。

Pack subsystemを安全確定できない場合、通常店舗はfail closedです。`--store-manager`では店長モード（復旧）へ入り、ショップ管理は表示されません。安全確定済みLegacy Packに対する限定Safe Maintenance以外の販売・Pack操作は停止します。

## 商品Pack Version

Pack Versionは`MAJOR.MINOR`形式です。MAJOR / MINORはそれぞれ0〜999で先頭ゼロは禁止です。

同じPack IDが現在導入済みの場合:

- より新しいVersion → Upgrade
- 同じVersion → Reinstall（完全置換）
- より古いVersion → 導入を安全にSkip

Legacy `1` / `2`等をアプリが自動的に`1.0` / `2.0`へ変換することはありません。

## Pack Uninstall

アンインストールは店長モードのみです。有効Packは直接アンインストールできません。先に無効化してください。

アンインストール後も過去の購入履歴・購入時Snapshot metadata・Purchase Statistics正本は保持されます。

## 購入履歴画像の容量制御

購入履歴用画像Snapshotは最大**512 MiB（536,870,912 bytes）**です。上限超過時は古い注文の画像から順番に画像ファイルだけ退役します。

購入履歴本文、商品名、購入時価格、数量、注文合計、統計は削除されません。画像が退役済みの場合は履歴用placeholderを表示し、現在の商品画像へ置き換えません。

## Money表示 / 価格検索

通常価格は`3,980 円`のように表示します。非常に大きい値は、9桁まで情報を保持した上付き指数表示へ切り替えます。

価格検索ではe記法を使用できます。

```text
123e4 = 123 × 10⁴
```

## データ保存場所

利用者データはプログラムフォルダではなく、次の場所へ保存されます。

```text
%LOCALAPPDATA%\FantasyStore
```

## プログラム本体の更新・削除

MVPにはInstaller/Uninstallerはありません。プログラムを削除する場合は展開した`FantasyStore`フォルダを削除してください。利用者データも完全に削除したい場合のみ、別途`%LOCALAPPDATA%\FantasyStore`を削除してください。

異なる本体Versionのonedirフォルダ同士を上書きマージせず、新しい公式配布物を新しいフォルダへ展開／置換する運用を推奨します。

## WebView2 Runtime

WebView2 Runtimeは本RC成果物へ自動同梱・自動ダウンロードしません。不足時はアプリが安全停止して必要性を案内します。
