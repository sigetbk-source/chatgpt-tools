# Ticket Launcher

Ticket Launcher は、チケット発売時刻に販売ページへ素早く移動するための iPhone アプリです。

> [!NOTE]
> このディレクトリは、ローカルの独立リポジトリ `ticket-launcher` の確認済みコミット `fe7231f8b4d7ac0d3297ca4ffa6add1f2cbad9a7`（`codex/mvp-implementation`）を 2026-08-23 にコピーしたスナップショットです。Git 履歴は移植していません。移行元リポジトリとその GitHub `main`（`8513697bc9bfc28cd03f65eaa3e6c3bcacd11818`）は変更・削除・アーカイブしていません。

## 主要ファイル

- `TicketLauncher/TicketLauncher.xcodeproj`: Xcode で開く開発入口
- `TicketLauncher/TicketLauncher/`: SwiftUI アプリ本体
- `TicketLauncher/TicketLauncherTests/`: 単体テスト
- `docs/mvp-spec.md`: 現在の要求仕様と受け入れ条件
- `docs/development-log.md`: 実装・検証履歴と未完了の実機確認
- `DEVELOPMENT_RULES.md` / `AGENTS.md`: 開発・レビュー時のルール

## MVP

最初のリリースでは、次の流れだけを確実に実装します。

1. イベント名、発売日時、販売 URL を手動登録する
2. 発売 5 分前と3 分前にローカル通知する
3. 通知タップで発売待機画面を開き、カウントダウンする
4. 待機画面を表示したままiPhoneをロックしなければ、発売時刻にSafariで販売 URL を自動で開く
5. 発売済みイベントを履歴として保持する

> [!IMPORTANT]
> 発売時刻の自動表示には、Ticket Launcherの発売待機画面を前面に表示し、iPhoneをロックせずに待つ必要があります。手動ロックやアプリ切り替え後は自動表示せず、画面のボタンから開きます。

## 開発環境

- macOS
- Xcode（最新版の正式版を基準）
- Swift / SwiftUI
- iPhone 実機
- 対象 OS: iOS 17 以降

## 開発を始める

Xcode プロジェクトを開き、署名先の Team と実機を選択してビルドします。

1. `TicketLauncher/TicketLauncher.xcodeproj` を Xcode で開く
2. `docs/mvp-spec.md` で現在の仕様を確認する
3. generic iOS または Simulator でビルドする
4. iPhone 実機で通知、ロック画面、URL 起動を検証する

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
- [x] Xcode をインストール
- [x] SwiftUI プロジェクトを作成
- [x] 手動登録・端末内保存・5分前/3分前通知・発売待機画面を実装
- [x] 前面待機中の画面点灯維持と、発売時刻のSafari自動表示を実装
- [ ] シミュレータと実機で受け入れテスト
