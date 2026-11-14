# 開発ログ

## 2026-11-15 iCloudからの実行環境分離とパネル通常インストール

- 目的: 案件選択画面が開かない症状と、毎回UDTでLoadが必要な状態を解消。
- 原因: CEPがMac標準Pythonを使用し、Xcode規約未承認で停止。書類のvenv依存部品・過去履歴にdatalessファイルがあり、内容読み取りが停止した。
- 変更: 専用Python・依存部品・ナレーション・連携サーバーをLibrary/Application Support/Premiere制作支援へ配備。既存のジョブ、案件辞書、話者情報6JSONを内容照合して移行。旧IPCは再生せず、元案件と元開発環境は保持。
- パネル: Adobe UDTでCCX作成後、UPIAでPremiere制作支援0.4.1を通常登録。25.6.6と26.5.2でEnabledを確認。インストール後のstart-bridgeは所有者の実行権限あり。Premiere編集Actionと権限は変更していない。
- 起動修正: CEPは利用できるローカルPythonを確認して起動し、stderr/起動失敗を表示。古い子プロセスの通知が後続起動へ混ざらないよう照合。独立アプリを~/Applicationsへ設置。
- 未取得履歴: config・辞書・文字起こし・反映準備のSF_DATALESSを読み取り前に確認し、元状態を保持してoffline表示。
- 検証: 起動ヘルパーと状態遷移のテスト、オフライン/移行6テスト、独立レビュー成功。再配置Pythonのprefixとbridge/rapidfuzz import先がインストール先であることを確認。Torch/Torchaudio/SpeechBrain/MLX Whisper import、tensor演算、FFmpeg実行、日本語読み変換、pip check成功。新サーバーのhealthと履歴APIが即応。
- バージョン差: 依存解決でTorchaudio2.11.0→2.11.1、MLX0.32.0→0.32.4。
- 制約: 実素材のASR処理、26.3実機、Whisper重みの移行は未実施。Premiere再起動後、UDTなしでパネルが開き「接続済み」となることをユーザー報告と画面で確認。ローカル版アプリで案件フォルダ選択画面の表示も確認済み。既存の時刻ベース承認には別案件同時書き出しの識別制約がある。
- 主な変更: speaker_repair/project.py、uxp_bridge.py、配布版narration/narration_jobs.py、deployment/local-runtime、インストール済みCEP js/main.jsとstudio-launcher.js。
- 使用法: docs/manual/local-installation.md。仕様: docs/specification/local-runtime.md。
