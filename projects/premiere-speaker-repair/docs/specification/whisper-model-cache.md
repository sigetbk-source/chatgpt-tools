# Whisperモデルキャッシュ仕様

## 目的

MLX Whisperの同一モデルを案件別・UXP連携別に重複保存せず、ローカルの一つの
キャッシュから共有する。

## 保存先

- 標準保存先はリポジトリ内の `work/model-cache` とする。
- `Project.load()` は案件設定に `model_cache_dir` がない場合も標準保存先を使う。
- UXP連携サーバーが生成する素材単位の `project.json` も標準保存先を明示する。
- 既存案件が `model_cache_dir` を明示する場合、必要に応じて別保存先へ上書きできる。

## 実行時の扱い

`speaker_repair.whisper_transcribe` は、文字起こし開始前に `HF_HOME` を
`Project.model_cache_dir` へ設定する。Hugging Face Hubのスナップショットと
ロックは共通キャッシュ内で管理され、ACMEのCLI処理とPremiere UXP連携処理が
同じ `mlx-community/whisper-large-v3-turbo` を利用する。

## 移行と削除

旧キャッシュを削除する前に、次の条件をすべて満たすこと。

1. 共通キャッシュにモデルの完全なスナップショットが存在する。
2. 全案件設定とUXP生成設定が共通キャッシュを参照する。
3. オフライン状態でモデルを解決できる。
4. 保存済みテスト音声でMLX Whisperの文字起こしが成功する。
5. 旧キャッシュを参照する設定ファイルが残っていない。

案件別の `whisper.json`、修正判断、修正版CSV、Premiere書き戻し用データは
モデルキャッシュではないため、モデルの移行や重複削除の対象に含めない。
