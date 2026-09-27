# 話者別3 ISOの2分レビュー

`python/multi_iso.py` は同一BWF収録の3本を、既知話者のISOとして扱う追加経路です。既存の1素材経路は変更しません。元WAVは読み取りのみです。

## 入力と保存

1. `sources.json` に `path`、`speaker_id`、`speaker_name` を3件指定します。`speaker_id` は `host`、`guest_en`、`guest_ja`。
2. `prepare --sources sources.json --start-tc 13:32:51 --duration 120 --output clips` でBWFのサンプル基準を照合し、同じ絶対時刻から16 kHzの確認用クリップを作ります。元素材とクリップのidentityをmanifestに保存します。
3. `run --manifest clips/manifest.json --output run-001 --reference terms.md` を実行します。AssemblyAI APIキーは端末で入力し、ファイルには保存しません。各ISOのUUIDディレクトリ `provider/<UUID>/` にjob ID、生応答、元ISOとhash、要求/検出言語を保存します。再開時は保存済み生応答を再送しません。
4. `master.json`、`candidates.json`、`references.json`、`summary.json`、`timings.json` を確認します。資料候補は原文、候補、資料名、根拠行、採用状態を持ち、自動採用しません。資料にはTXT、MD、CSV、DOCX、テキストPDFを指定できます。画像は別途転記した語彙ファイルを指定します。

発話候補は長いAAI utteranceを語間の間隔、ISOの音量優位、日英の持続的な切り替えで分けます。0.2秒窓のRMS、dBFS、peakを記録し、上位ISOとの差が8 dB以上をstrong、3 dB未満をambiguousとします。MFCCとスペクトル特徴は補助スコアです。ISO固定情報とAAI speaker/confidence、音量、周波数の全根拠を各候補に保存します。pyannoteは通常呼ばず、曖昧区間で明示的に渡したフォールバックだけを呼びます。

重複候補は時刻重なりとテキスト類似で束ね、回り込みと判断できる候補だけmaster表示から外します。候補自体は `candidates.json` に保持します。同時発話候補は並列発言のまま保存します。`speaker_confidence` は設定可能な重みから作った相対スコアであり、校正済み確率ではありません。短い相槌、AAIが異なる話者を一発話へ結合した箇所、語の異常に長い時刻は人の音声確認が必要です。

## 既存画面

`python/multilingual_review.py --master master.json --workspace review-workspace --media clips/host.wav --port 8917` で既存の多言語レビューを開きます。カードから3本のうち優位なISOを再生できます。詳細には元ISO、最終話者、AAIと音量・周波数の根拠、重複と同時発話の候補、資料補正の採用状態を表示します。原文、話者、言語、訳文は既存どおり編集可能です。原文の置換で語時刻が合わなくなった場合、整列未解決を表示し、確定出力を止めます。

実素材の生音声、provider応答、資格情報、レビューの手修正はGitへ入れません。2分の確認版で人が候補を選別した場合、その選別結果と自動候補を分けて保存し、全尺自動処理の精度として扱いません。
