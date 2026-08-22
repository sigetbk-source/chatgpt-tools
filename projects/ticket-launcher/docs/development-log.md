# 開発ログ

## 2026-08-14 — リポジトリ初期整備

### 実施

- GitHub の Private リポジトリ `sigetbk-source/ticket-launcher` を開発正本として確認。
- MVP を「手動登録、1 分前ローカル通知、カウントダウン、販売 URL 起動」に限定。
- README、AI 開発ルール、開発ルール、MVP 仕様、CHANGELOG、gitignore を追加。
- iOS の制約として、バックグラウンドまたはロック中の Safari 自動起動は保証せず、通知タップを正式な動作とする方針を明記。

### 環境確認

- Xcode 本体: 未導入。Command Line Tools が選択されている。
- Swift: Apple Swift 6.3.3 を確認。
- GitHub CLI: 未導入。
- HTTPS による Private リポジトリ取得: Mac 側の GitHub 認証が未設定のため未完了。
- GitHub アプリ連携: リポジトリの参照・更新権限を確認。

### 次の作業

1. Xcode 本体をインストールし、初回セットアップを完了する。
2. Xcode または SSH で GitHub 認証を設定し、リポジトリをローカルへ取得する。
3. SwiftUI App プロジェクトとテストターゲットを作成する。
4. データモデル、通知管理、カウントダウン、URL 起動の順に実装する。
5. シミュレータと iPhone 実機で受け入れ条件を検証する。

### 未検証

Xcode、シミュレータ、実機を使うビルドおよびランタイムテストは未実施。現段階は実装完了ではなく、着手準備完了。
