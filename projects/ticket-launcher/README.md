> [!NOTE]
> このディレクトリは、独立リポジトリ [sigetbk-source/ticket-launcher](https://github.com/sigetbk-source/ticket-launcher) の `main`（`8513697bc9bfc28cd03f65eaa3e6c3bcacd11818`）を 2026-08-22 にコピーして集約したものです。元リポジトリは削除・アーカイブせず、変更せずに残しています。履歴の完全移植ではなく、GitHub 上の現行ファイル一式を優先したスナップショットです。

# Ticket Launcher

Ticket Launcher は、チケット発売時刻に販売ページへ素早く移動するための iPhone アプリです。

## MVP

最初のリリースでは、次の流れだけを確実に実装します。

1. イベント名、発売日時、販売 URL を手動登録する
2. 発売 1 分前にローカル通知する
3. アプリ内に秒単位のカウントダウンを表示する
4. アプリが前面にある場合、発売時刻に販売 URL を開く
5. アプリがバックグラウンドまたは端末がロック中の場合、通知をタップして販売 URL を開く

> [!IMPORTANT]
> iOS は、バックグラウンド中のアプリが指定時刻に Safari を自動で開くことを保証しません。したがって「0 秒で自動オープン」はアプリが前面にある場合の動作とし、それ以外はローカル通知からの起動を正式な仕様にします。

## 開発環境

- macOS
- Xcode（最新版の正式版を基準）
- Swift / SwiftUI
- iPhone 実機
- 対象 OS: iOS 17 以降（初期方針。Xcode プロジェクト作成時に確定）

## 開発を始める

現在は準備段階です。先に Xcode 本体と GitHub 認証を設定してください。

1. App Store から Xcode をインストールする
2. Xcode を一度起動し、追加コンポーネントを導入する
3. GitHub へ接続できるように、Xcode または Git Credential Manager / SSH を設定する
4. このリポジトリを取得する
5. `docs/mvp-spec.md` を確認して Xcode プロジェクトを作成する
6. iPhone 実機で通知、時刻境界、URL 起動を検証する

## リポジトリ方針

GitHub 上の Markdown とコードを開発の正本とします。

- `README.md`: 入口、現在の状態、使い方
- `AGENTS.md`: AI 開発時に必ず守るルール
- `DEVELOPMENT_RULES.md`: 開発・レビュー・完了条件
- `docs/mvp-spec.md`: MVP の要求仕様と受け入れ条件
- `docs/development-log.md`: 作業記録と判断の根拠
- `CHANGELOG.md`: 利用者に関係する変更履歴

秘密情報、署名用データ、個人用設定、一時生成物はコミットしません。仕様変更ではコードと関連文書を同じ変更単位で更新します。

## 現在の状態

- [x] Private リポジトリ作成
- [x] MVP の範囲と iOS 制約を文書化
- [x] AI 開発標準ルールをリポジトリに登録
- [ ] Xcode をインストール
- [ ] SwiftUI プロジェクトを作成
- [ ] MVP を実装
- [ ] シミュレータと実機で受け入れテスト
