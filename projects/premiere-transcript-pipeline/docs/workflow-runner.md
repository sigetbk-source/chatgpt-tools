# Premiere パネルから確認画面を開く

`ワークフロー連携を開始.command` を、対象の作業フォルダ、UXP と共有する IPC フォルダ、確認画面のポートを指定して先に起動する。実行例:

```bash
PREMIERE_REVIEW_PYTHON=/path/to/whisper-venv/bin/python \
  ./ワークフロー連携を開始.command /path/to/workspace /path/to/ipc 8904
```

これは指定した作業フォルダ専用のローカルサービスを一つだけ起動する。Premiere パネルの「開始／再開」は、その共有フォルダの `launcher-inbox` に要求を置く。サービスは要求の作業フォルダが起動時の指定と一致すること、Premiere の選択素材が実在すること、保存済み作業の素材と一致することを確かめる。確認画面が停止していれば同じ Python 環境で起動し、応答から同一作業フォルダを確認してから URL を返す。起動済みの場合は同じ画面へ戻る。ポートが別の作業に使われていれば接続しない。

日本語の既存作業は、起動時に既存案件 JSON と既存ツールのルートを追加指定した場合だけ対象素材を照合する。設定がない場合、パネルは既存の日本語用ランチャーから案件を選ぶよう案内する。既存案件を推測して起動しない。

要求は `version:1`、32桁の nonce、期限、`status` または `start_or_resume` のみを受け付ける。`status` は作業フォルダだけ、`start_or_resume` は作業フォルダ、`mode`、Premiere の選択対象を必要とする。応答は `launcher-outbox/<nonce>.json` に原子的に書く。返す URL は `127.0.0.1` の設定ポートに固定し、UXP が表示前に再確認する。APIキーは要求にも保存ファイルにも含めず、既存の接続設定と同じく実行中プロセスだけで扱う。

`uxpBridgeConfigured` と `cepBridgeConfigured` は共有フォルダの構成を示すだけで、Premiere パネルの生存確認を意味しない。ローカル Whisper のインストール表示も実素材での完走確認を意味しない。
