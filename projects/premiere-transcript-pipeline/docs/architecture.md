# 既存処理の再利用調査

2026-09-07。再利用元はMacの既存 `premiere-speaker-repair`。この調査では既存システムを変更していない。

## 確認した実装

- `speaker_repair/whisper_transcribe.py`: `mlx_whisper.transcribe` を使用。日本語、`word_timestamps=True`、用語リストを `initial_prompt` に指定。モデルは Project の設定から取得。
- `speaker_repair/project.py`: 既定モデルは `mlx-community/whisper-large-v3-turbo`。`model_cache_dir` は共通キャッシュを利用できる。音声stream/channel選択の設定がある。現状 `transcript_json` を必須参照するため、独立ASRに Project 全体をそのまま使うとPremiere依存を持ち込む。
- `speaker_repair/transcript_server.py`: correction candidates、decisions、edit stateを読み、テキスト修正・話者名変更・区間分割/結合を扱う。独自masterとの接続は未実装。
- ローカル `premiere-transcript-roundtrip` スキル: 公式UXP経由のexport/import、対象照合sidecar、バックアップを持つ。Phase 1は要件に従いUI Importを先に試す。既存Premiere JSONをタイミング入力には使わない。

## 再利用方針

Phase 2では既存のMLX呼び出し・用語prompt・音声抽出・モデルキャッシュを優先する。独立した入力設定からこれらを呼び、Whisperのword出力をmasterへ正規化する薄いadapterを設ける。既存修正UI全体の移植や既存案件の上書きはしない。

Phase 1のspeaker keyと時刻は手動作成可能。これを自動ASR・自動話者分離の検証と混同しない。pyannote導入・動作検証は未実施。

## 実行状態

コードの読み取りは確認済み。既存Python環境からのimportとFFmpeg起動は応答待ちとなり、このセッションでは再実行成功を確認できていない。ファイルの一部はmacOSのdataless状態だった。キャッシュが列挙できることはモデルの実行成功を意味しない。

### 同日再開後

FFmpegの実行成功を確認。既存MLXプログラムとモデルのdataless状態による起動待ちは解消しなかったため、同期対象外の一時環境に `mlx-whisper 0.4.3` と `mlx 0.32.2` を導入し、同じ `whisper-large-v3-turbo` を取得した。既存環境の更新・削除はしていない。

GPUアクセスを許可した実行で、45秒音声を約13.06秒で処理。16 segments / 118 word entriesを得た。モデル取得後のASR実行時は `HF_HUB_OFFLINE=1`。モデルrevisionは `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb`。

文字精度は未評価。「Q1」等の要確認表記、カタカナ1文字単位への分割がある。UIの話者表示試験用に手動の仮ラベルを付けたが、自動話者分離も割当正確性の確認も未実施。新環境の実行成功を既存環境の復旧成功とは扱わない。
