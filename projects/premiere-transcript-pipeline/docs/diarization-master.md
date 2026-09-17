# 自動話者区間と独立masterの接続

## 処理と入力

`python/diarization_master.py` はWhisperの単語JSONと話者区間JSONを読み、既存のmaster修正・Premiere変換経路へ接続する。追加パッケージ不要。モデル推論は行わない。

話者区間入力は `segments: [{start, end, speaker}]` と任意の `metadata`。両入力は同じ音声・同じ先頭基準の秒であることを呼び出し側で確認する。別素材・時刻オフセットの自動照合は未実装。出力に入力JSONのSHA-256を記録するが、これは音声一致の証明ではない。

```sh
python3 python/diarization_master.py --whisper /local/whisper.json \
  --diarization /local/diarization.json --source-id source.mov \
  --language ja-jp --output /local/master.json
```

既存ファイルへの上書きを拒否する。masterは `python/master_review.py` へ渡せる（[操作手順](master-review.md)）。

## 再利用元と割当規則

既存 `premiere-speaker-repair/speaker_repair/independent_input.py` の `assign_word` を移植。各話者との重なり時間の最大値で割り当て、同じ話者の重複区間は和集合にして二重加算を防ぐ。従来の同点時のラベル順選択は採用せず、同点は「不明」にする。重なりなし・長さ0の単語も不明。単語の文字・開始・終了は変更せず、話者切替と元ASR区間の境界でutteranceを分ける。

根拠スコア、重複発話、同点、初期の自動割当は単語の `diarization` 注釈に保持する。手動修正後の有効な話者はutterance.speakerであり、注釈は初期推定の履歴。現行画面は不明話者を絞り込み可能だが、重複発話注釈の専用表示は未実装。これらは認識確率・話者精度を表す値ではない。

## 2026-09-17 検証

- 既存48分36.58秒素材のMLX Whisper出力とsherpa-onnx 1.13.7の自動出力を再利用。今回モデル推論は再実行していない。
- 既存話者処理はpyannote segmentation-3.0 ONNX＋3dspeaker ERes2Net＋clustering threshold 1.2。pyannote.audioライブラリ経由ではない。
- 6241語を1194区間、5自動話者群＋不明へ統合。全単語の文字・開始・終了がWhisper入力と完全一致。
- 不明797語（従来の重なりなし241語＋同点556語）。重複発話の可能性856語。これらは重複する集計であり、合計人数・誤り率ではない。
- master修正用workspaceの保存・再読込で全内容一致。Premiere書き出しでも全6241語が保持され、終了時刻再構成誤差は1e-9秒未満。
- ブラウザーで1194区間、自動話者1〜5・不明の表示を確認。
- Python全15試験成功。最初の画面API試験5件はsandboxのlocalhost bind制限で失敗し、ローカル通信可能な実行で全件成功。
- 既存の手動修正済み案件を上書きせず、別workspaceを作成。音声・実データ・モデルはリポジトリ外に保持。

## 未完了

自動話者の人物対応・精度は未確認。既存のCommunity-1全尺比較にも女性2話者の混同が記録されているため、モデル変更だけで解決済みとしない。自然音声の代表区間の正解ラベルに基づく評価・改善を次の工程とする。今回の自動話者付き長尺JSONのPremiere実機Importは未実施。過去の45秒・仮話者の実機成功とは別の検証である。
