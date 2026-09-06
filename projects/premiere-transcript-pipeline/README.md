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
- Premiere 連携: Phase 1は公式UXP経由でImportを実機確認。手動UI Importと汎用UXP自動適用は未検証・未実装
- 元素材: 書き換えない
- `.prmi` / `.prin`: 本プロジェクトのMVPでは依存しない。別テーマとして read-only 解析・活用を検討する

## 開発用素材

特定の既存素材には依存しない。Mac ローカルにある、2人以上の会話を含む任意の MOV / WAV をテスト素材として使用する。

テスト素材・生成キャッシュ・個人情報を含む実データは GitHub にコミットしない。

## 文書

- [REQUIREMENTS.md](REQUIREMENTS.md): 要件定義の正本
- [docs/architecture.md](docs/architecture.md): 既存処理の再利用調査
- [docs/premiere-json.md](docs/premiere-json.md): 最小変換の使用法・実機検証記録

## 開発・検証環境

ユーザー指定により Premiere Pro 2025（25系）を使用する。2026-09-07にMacのインストール情報から25.6.6を確認。2026での結果を25系の検証結果として扱わない。

最小変換は `python/premiere_export.py`。使用法と未検証項目は上記文書を参照。

## 状態

- 要件定義: v0.1
- Phase 1: 25.6.6の1素材で独自JSONのImport・仮2話者表示・検索・位置ジャンプ・Text-Based Editing削除/カット＆ペーストを実機確認（2026-09-07）
- 最小変換: `python/premiere_export.py`、自動テスト4件成功
- ASR: 一時環境のMLX Whisperで45秒音声から118単語を生成。既存環境の復旧・汎用ASR adapterは未完了
- 話者分離: 手動の仮割当で表示を試験。自動話者分離・話者割当精度は未検証
- UXP: 対象固定・権限最小の試験用呼び出しで読み込み/読み戻し成功。汎用自動適用は未実装
- 詳細・制約: [検証記録](docs/premiere-json.md)。MVP全体の完了ではない
