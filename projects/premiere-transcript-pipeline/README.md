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
- Premiere 連携: Premiere 25のUXPで対象照合・Transcript適用・SRT素材Importを行い、専用CEPで字幕トラックへ配置する。UXPからローカル修正画面を開く入口を追加した。
- 元素材: 書き換えない
- `.prmi` / `.prin`: 本プロジェクトのMVPでは依存しない。別テーマとして read-only 解析・活用を検討する

## 開発用素材

特定の既存素材には依存しない。Mac ローカルにある、2人以上の会話を含む任意の MOV / WAV をテスト素材として使用する。

テスト素材・生成キャッシュ・個人情報を含む実データは GitHub にコミットしない。

## 文書

- [docs/workflow-automation.md](docs/workflow-automation.md): 日本語／多言語の入口、エンジン選択、資料訂正、翻訳、UXP反映をつなぐ操作設計と検証範囲
- [docs/multilingual-review.md](docs/multilingual-review.md): 多言語素材の原語・発話言語・日本語参考訳・併記SRT確認
- [docs/bilingual-srt-layout.md](docs/bilingual-srt-layout.md): SRTの明示改行・対訳ページ分割とPremiereの下寄せ設定による文字欠け防止。静止画・動画は生成しない。
- [docs/master-review.md](docs/master-review.md): 独立master用の話者・区間修正画面の起動と操作

- [docs/integration.md](docs/integration.md): 非依存master修正・UUID維持・安全な書き戻しの共通経路と統合状況

- [REQUIREMENTS.md](REQUIREMENTS.md): 要件定義の正本
- [docs/architecture.md](docs/architecture.md): 既存処理の再利用調査
- [docs/premiere-json.md](docs/premiere-json.md): 最小変換の使用法・実機検証記録
- [docs/speaker-roundtrip.md](docs/speaker-roundtrip.md): 話者修正と話者境界の分割・結合の再現手順

## 開発・検証環境

ユーザー指定により Premiere Pro 2025（25系）を使用する。2026-09-07にMacのインストール情報から25.6.6を確認。2026での結果を25系の検証結果として扱わない。

最小変換は `python/premiere_export.py`。使用法と未検証項目は上記文書を参照。

## 状態

- 2026-09-26: v0.5のワークフロー入口、初期ASR、資料に基づく用語訂正、字幕ページ編集、UXP対象照合・適用・SRT Import、専用CEP字幕配置を実装。修正済みデータのコピーでブラウザ操作、新UXP経路で77発言の同内容再適用・読み戻し・保存をPremiere 25.6.6で確認。新規の未編集・素材時刻シーケンスへCEP経由で併記SRTを配置し、字幕トラック1本とサンプル時点の画面表示も確認した。全字幕のレイアウト・時刻の網羅確認と新しい初回ASR経路は未検証。[操作と検証記録](docs/workflow-automation.md)。下記は各時点の履歴。

- 2026-09-26: UXPパネルに作業フォルダ・選択素材の確認とローカル修正画面の開始／再開を追加。固定先のローカルランナーとのファイル連携、URLの127.0.0.1照合を実装。Premiere 25でパネルの連携フォルダ接続を確認し、実ランナーへの開始／再開要求の往復も確認した。配布用 `.ccx` をローカルへ作成し、Creative Cloudで常設インストール済み。Premiere 25の「UXP プラグイン」メニューからパネルが開くことを確認した。パネルのボタンからブラウザが開く全操作は未検証。

- 2026-09-26: 併記SRTの明示改行・行数/幅検査・確認済みページの受け付けを追加。収容不能な完成出力を止め、ドラフトでは未解決箇所を通知する。SRT6件＋既存多言語16件のテストに合格。Premiere 25の下寄せ・枠設定と実表示は未検証。[仕様](docs/bilingual-srt-layout.md)

- 過分割の比較版: 元ASR区間で代表話者を集計し、手動変更区間を保護する後処理を追加。1166→748区間、手動17区間と全6241語を保持。人物対応・相槌の帰属は要確認。[詳細](docs/diarization-master.md)


- 2026-09-17: 既存sherpa-onnx自動話者出力とWhisperを独立masterへ統合するadapterを追加。48分・6241語の文字/時刻保持、修正用保存・再読込、Premiere JSON出力を確認。自動話者精度と今回の実機Importは未検証。[接続仕様・記録](docs/diarization-master.md)


- 修正画面: 既存画面の構成・スタイルを再利用した独立master用入口を追加。話者割当・単語境界分割・結合・Undo/Redo・作業データ保存・UUID維持のPremiere用書き出しを提供。本文編集・画面からの直接書き戻しは未実装。

- 多言語修正画面: 原語、話者、発話言語、日本語参考訳を別々に保存し、素材基準の原語＋日本語併記SRTと区間言語付きPremiere JSONを書き出すローカル入口を追加。保存済みAssemblyAI応答による無課金候補と、明示操作時だけ動く設定式ライブ再取得を提供する。本文修正後の「整列未解決」には、音声を確認して単語境界を手入力する画面を追加。自動で時刻を確定せず、確定前のPremiere JSON出力を止める。新しい整列画面の実操作・Premiere 25への再Importは未検証。

- 統合: 外部ASR由来master → master側話者修正 → UUIDを維持して変換 → 共通writebackを45秒検証コピーで実機確認。ASR再実行・修正UI統合・自動話者分離は今回未実施。

- 安全性の追加試験: 対象固定の実機試験でプロジェクトパス・素材名・メディアパスの不一致を拒否。模擬保存失敗後のバックアップ再Import・全項目一致・実保存を確認。実I/O障害と製品UIの状態表示は未検証。

- 要件定義: v0.4。併記SRTの欠け防止条件を追加。Premiere 25での実スタイル適合は未検証。
- 話者修正: 2026-09-08に一区間の話者変更・反復適用・保存/再オープン・画面表示を確認。45秒検証コピーでは17区間→16区間へ結合→17区間へ再適用し、保存・再オープン後の全フィールド一致を確認。日英混在適用は未検証
- Phase 1: 25.6.6の1素材で独自JSONのImport・仮2話者表示・検索・位置ジャンプ・Text-Based Editing削除/カット＆ペーストを実機確認（2026-09-07）
- 素材メタデータ: 外部生成JSONの読み込み後に「文字起こしステータス」が「完了」となることをユーザーが実機確認（2026-09-08）
- 最小変換: `python/premiere_export.py`、自動テスト4件成功
- ASR: 一時環境のMLX Whisperで45秒音声から118単語を生成。既存環境の復旧・汎用ASR adapterは未完了
- 話者分離: Phase 1は手動仮割当。既存sherpa-onnx自動出力のmaster統合を追加済み。人物対応・話者割当精度は未確認
- 以前のUXP試験記録: 対象固定の呼び出しで読み込み/読み戻し成功。現在のワークフロー連携の状態は上記を参照。
- 詳細・制約: [検証記録](docs/premiere-json.md)。MVP全体の完了ではない
