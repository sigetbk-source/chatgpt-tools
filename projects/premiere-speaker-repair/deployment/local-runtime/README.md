# ローカル実行環境のビルド部品

書類・デスクトップのiCloud同期に依存しない実行環境を準備するためのソースです。
Python本体、依存ライブラリ、モデル、案件データ、配備時のスナップショットはGitに含めません。

起動・ローカル化・未取得履歴対策のためのビルド部品です。
再配置可能なPython本体と互換ナレーション制作ランタイムは外部入力として別途用意します。
この公開ソースだけにナレーション制作ランタイム一式が含まれているわけではありません。
UXPソース中の `/LOCAL_BUILD` は公開用プレースホルダーです。ビルド時にIPCとCEP出力先を利用者のローカルパスへ設定します。

## ビルド

`build_local_release.py` に次の引数を指定します。

- `--source`: このリポジトリのルート
- `--python`: 再配置可能なPython配布物のルート（`bin/python3` を含む）
- `--narration`: 既存のナレーション制作ランタイムのソース。vendorを含む既存テンプレートを入力に使い、このディレクトリの `narration_jobs.py` で未取得履歴対策を適用します。
- `--stage`: 新規または空のビルド先。稼働環境を指定しません。
- `--install-root`: 最終配備先。既定は `~/Library/Application Support/Premiere制作支援`
- `--asr --requirements-file deployment/local-runtime/requirements-existing.txt`: iCloud上のvenvを読まず、公式PyPIのバイナリwheelで文字起こし用依存部品も構成します。

ビルド後は `release-manifest.json` と、同梱Pythonによるimport・依存整合性を確認します。
モデル重みは別途、ローカルキャッシュに取得します。

## 状態移行と配備

`migrate_state.py --source <元のリポジトリ> --stage <ビルド先>` はジョブ・辞書・話者情報だけを検証してコピーします。
未取得または壊れたJSONがあれば中止し、過去のIPC要求は再実行しません。
クラウドから退避したスナップショットを入力にする場合も、切替前後に元のファイル集合と内容を照合してください。

稼働中の処理がないことを確認し、バックアップ・復旧手順を準備してから配備します。
`cep-and-app/main.js` と `studio-launcher.js` は既存CEP拡張の `js/` に対で配備します。
`cep-and-app/launch` はローカル版ナレーションアプリの起動スクリプトです。
このディレクトリには自動配備スクリプトや利用者固有のパスは含めていません。

UXPはビルド先の `app/uxp-plugin` をAdobe UDTでCCX化し、UPIAで通常インストールします。
CCX化で `start-bridge` の実行ビットが落ちることがあるため、CCX内とインストール後の両方で所有者の実行権限を確認してください。
更新後の最初だけPremiereを再起動して旧版の接続先キャッシュを入れ替えます。
通常利用で毎回Developer ToolsからLoadする必要はありません。

## テスト

リポジトリのルートから、依存部品を導入したPythonで実行します。

```sh
python3 deployment/local-runtime/test_offline_guards.py
node deployment/local-runtime/cep-and-app/test-launcher.js
node deployment/local-runtime/cep-and-app/test-main-state.js
```

未取得ファイルを読まないこと、履歴状態の保持、辞書移行の中止条件、古い起動応答の分離を検証します。
