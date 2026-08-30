# 要件定義 v0.1

## 1. 目的

Premiere Pro の内蔵文字起こし・話者分離を入力の正本にせず、Mac ローカルの独自処理で素材から文字起こし・単語タイムスタンプ・話者情報を生成し、修正後の結果を Premiere 互換 Transcript JSON として素材へ適用できる基盤を作る。

この基盤はフルテロップ生成だけでなく、Text-Based Editing、意味ベース編集、LLM による構成支援、ラッシュ支援の中間データとして利用できることを目標とする。

## 2. MVP の完成条件

次の一連が、Premiere の内蔵文字起こしを一度も実行せずに成立すること。

1. 任意の MOV / WAV を入力する
2. Mac ローカルで ASR を実行する
3. 単語ごとの `text / start / end` を生成する
4. Premiere 非依存で話者区間と話者 ID を生成する
5. ASR と話者区間を統合して独自 master JSON を作る
6. 文字列・話者名・話者区間を修正できる
7. master JSON から Premiere 公式 Transcript JSON を生成する
8. Premiere の対象素材へ Static Transcript として読み込める
9. 話者表示・全文検索・Text-Based Editing が正常に機能する

## 3. 入力

### 必須

- MOV
- WAV

### 将来候補

- MP4
- MXF
- その他 Premiere が扱う一般的な音声付きメディア

### 前提

- 元素材は書き換えない
- 開発用素材は GitHub に保存しない
- 初期検証は2人以上の会話を含む任意素材を使用する

## 4. ASR

### MVP

- 現在 Mac ローカルで動いている Whisper 系処理をまず再利用する
- 日本語を主対象とする
- 出力は独自 master JSON へ正規化する

### 必須出力

- 認識文字列
- 単語開始時刻
- 単語終了時刻または duration
- 可能なら confidence

### 検討事項

- 現行 Whisper 実装の種類を確認する
- 現行単語タイムスタンプ精度を Premiere Text-Based Editing 用途で評価する
- 不足する場合は forced alignment を追加する
- WhisperX は一括置換ではなく、必要な処理要素の参照実装として評価する

## 5. 話者分離

### MVP

Premiere の `speakers` / `segment.speaker` を入力に使わず、元素材音声から独自に生成する。

### 第一候補

- pyannote 系 speaker diarization

### 必須出力

- speaker key（例 `spk_001`）
- 発話開始時刻
- 発話終了時刻

### 修正

- 自動話者 ID を人間が実名・役割名へ変更できる
- 誤った話者境界を手動修正できる
- 例: `spk_001 -> MC`, `spk_002 -> ゲストA`

### 評価

- Premiere 内蔵話者分離との比較を行う
- 短い相槌、同時発話、声質の近い人物を重点確認する

## 6. master JSON

Premiere JSON を内部の正本にしない。

### 最低限の構造

```json
{
  "schema": "bk-master-transcript/v0.1",
  "source_id": "A001.mov",
  "language": "ja-jp",
  "speakers": [
    {"key": "spk_001", "name": "MC"}
  ],
  "utterances": [
    {
      "speaker": "spk_001",
      "words": [
        {
          "text": "今日は",
          "start": 12.42,
          "end": 12.81,
          "confidence": 0.98,
          "eos": false
        }
      ]
    }
  ]
}
```

### 方針

- speaker ID は Premiere UUID に依存しない
- Premiere 出力時に必要な UUID を生成する
- 内部注釈は Premiere の仕様に合わせて削らない
- 将来、以下を追加できる拡張性を持たせる
  - visual tags
  - edit tags
  - first impression
  - use case
  - voice annotation
  - semantic summary / topic

## 7. 文字起こし修正

### MVP

既存の文字起こし修正システムを可能な限り流用する。

### 修正対象

- 誤認識
- 固有名詞
- 句読点
- 文区切り
- 話者名
- 話者区間

### 方針

- 元の単語タイムスタンプを必要以上に破壊しない
- 文字列変更と話者変更を独立して扱えるようにする
- 修正履歴を master JSON または別ログに残せる構成を将来検討する

## 8. Premiere 出力

### MVP

Adobe の Transcript JSON 仕様に合わせて生成する。

必要要素:

- `language`
- `speakers[]`
  - UUID
  - name
- `segments[]`
  - start
  - duration
  - language
  - speaker UUID
  - words[]
- `words[]`
  - text
  - start
  - duration
  - confidence
  - eos
  - tags
  - type

### 初期運用

- JSON ファイルを書き出す
- Premiere の UI から手動 Import する

### 次段階

- UXP から対象クリップへ自動適用する
- 素材パスまたはクリップ ID と master JSON を対応付ける
- 解析済み / 修正済み / Premiere 反映済みの状態を表示する

## 9. `.prmi` / `.prin`

MVP では依存しない。

別テーマとして次を検討する。

- read-only で内部形式を解析できるか
- Media Intelligence の時間レンジ・意味情報を独自 JSON へ抽出できるか
- ラッシュ中の解析内容表示に利用できるか
- 自動素材ログ・LLM 検索へ利用できるか
- 書き込み・改変は行わない
- Adobe が公式 UXP / API を公開した場合は公式経路へ移行する

## 10. AME Analysis との関係

独自文字起こし基盤と AME Analysis は競合させず、並列前処理として扱う。

```text
素材
├─ AME Analysis
│  └─ .prmi: Visual / Audio Media Intelligence
│
└─ 独自 Transcript Pipeline
   └─ master JSON: ASR / word TC / speaker
```

Premiere 読み込み時に、映像・音の Media Intelligence と修正済み Transcript の両方が準備済みの状態を目指す。

## 11. 非機能要件

- Mac ローカルを基本とする
- 元素材を変更しない
- 外部クラウド送信を必須にしない
- モジュール交換可能な構成にする
- ASR / alignment / diarization / Premiere exporter を分離する
- 中間生成物とキャッシュは GitHub にコミットしない
- 個人情報や実素材由来の transcript を GitHub にコミットしない

## 12. 初期アーキテクチャ

```text
MOV / WAV
   ↓
Audio extraction
   ├─────────────┐
   ↓             ↓
ASR           Diarization
   ↓             ↓
word TC       speaker ranges
   └──────┬──────┘
          ↓
       merge
          ↓
master_transcript.json
          ↓
修正 UI
          ↓
Premiere JSON exporter
          ↓
Static Transcript Import
          ↓
Text-Based Editing
```

## 13. 開発順序

### Phase 0: 実態確認

- 現行 Mac ローカル Whisper 実装を特定
- 既存修正システムの JSON 入出力箇所を確認
- 手元から新しい開発用 MOV / WAV を1本選ぶ

### Phase 1: Premiere JSON 独自生成

- Premiere 由来 JSON を入力にしない最小 master JSON を作る
- 新規 speaker UUID を生成する
- Premiere JSON を出力する
- 実機 Import して話者・文字・TCを確認する

### Phase 2: ASR 独立

- 元素材から ASR を実行
- word timestamp を master JSON 化
- Premiere JSON へ変換して実機確認

### Phase 3: 話者分離独立

- pyannote を試す
- speaker ranges を生成
- word TC と統合
- Premiere 内蔵話者分離と比較する

### Phase 4: 修正 UI 統合

- 既存修正システムへ master JSON を接続
- 話者区間修正を完成させる
- 修正後 Premiere JSON を出力する

### Phase 5: UXP 自動化

- 対象素材の特定
- JSON 自動適用
- 状態表示
- バッチ適用

### Phase 6: インジェスト自動化

- Watch Folder / 外付けドライブ検出
- 素材コピー後の自動処理
- AME Analysis との並列実行

## 14. MVP 外

初期段階では以下を完成条件に含めない。

- `.prmi` / `.prin` 書き換え
- 完全自動話者実名認識
- AI による完成編集
- Bロール自動配置
- クラウドサービス化
- 全フォーマット対応

## 15. 最初の実機テスト

野嶋・山口素材には依存しない。

手元の別の MOV / WAV を使用し、次を確認する。

1. 独自生成した2話者以上の Transcript JSON を Premiere が受け付ける
2. 話者名が正しく表示される
3. segment の話者切り替えが正しく表示される
4. word timestamp で検索・再生位置ジャンプが成立する
5. Text-Based Editing の削除・カット＆ペーストが成立する

このテストを通過した時点で Phase 1 完了とする。
