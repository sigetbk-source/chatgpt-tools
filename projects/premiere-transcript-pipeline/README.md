# Premiere Transcript Pipeline

Premiere Pro の内蔵文字起こし・話者分離を入力の正本にせず、素材音声からローカル処理で文字・単語タイムスタンプ・話者情報を独自構築し、Premiere 互換 Transcript JSON として戻すための開発プロジェクトです。

## 目的

- Premiere の文字起こし精度・話者分離精度への依存を減らす
- 文字起こし・話者・タイムスタンプを独自 `master transcript` として保持する
- 現行の文字起こし修正システムを活かしながら、話者区間も独自に構築・修正できるようにする
- 修正済み Transcript を Premiere の Text-Based Editing で利用する
- 将来の意味ベース編集、LLM 編集支援、ラッシュ支援の基盤にする

## MVP

任意の MOV / WAV を1本入力し、Premiere に一度も文字起こしさせずに次を実現する。

1. Mac ローカルで ASR を実行する
2. 単語単位のタイムスタンプを得る
3. Premiere 非依存で話者区間・話者 ID を生成する
4. 文字と話者を修正可能な独自 master JSON を作る
5. Premiere 公式 Transcript JSON へ変換する
6. 対象素材の Static Transcript として読み込み、Text-Based Editing が成立することを確認する

## 初期方針

- ASR: 現行のローカル Whisper 系処理をまず再利用する
- Word alignment: 現行の単語 TC 精度を確認し、不足する場合に追加する
- Speaker diarization: pyannote 系を第一候補として検証する
- 正本: Premiere JSON ではなく独自 master JSON
- Premiere 連携: 最初は手動 JSON Import、検証後に UXP 自動適用へ進む
- 元素材: 書き換えない
- `.prmi` / `.prin`: 本プロジェクトのMVPでは依存しない。別テーマとして read-only 解析・活用を検討する

## 開発用素材

特定の既存素材には依存しない。Mac ローカルにある、2人以上の会話を含む任意の MOV / WAV をテスト素材として使用する。

テスト素材・生成キャッシュ・個人情報を含む実データは GitHub にコミットしない。

## 文書

- [REQUIREMENTS.md](REQUIREMENTS.md): 要件定義の正本
- `docs/architecture.md`: 処理構成・データフロー（今後作成）
- `docs/premiere-json.md`: Premiere Transcript JSON の仕様・実機検証記録（今後作成）

## 状態

- 要件定義: v0.1 作成中
- 独自 master JSON → Premiere JSON の最小変換: プロトタイプ作成済み、Premiere 実機確認は未実施
- 独自 ASR / 話者分離パイプライン: 未実装
- UXP 自動適用: 未実装
