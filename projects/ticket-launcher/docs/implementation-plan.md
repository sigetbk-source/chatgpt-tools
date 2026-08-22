# MVP 実装計画

## 前提

- Xcode の正式版をインストール済みであること
- iPhone 実機を接続し、開発者署名ができること
- リポジトリをローカルへ取得できること
- Bundle Identifier は Xcode プロジェクト作成時に利用可能な値を確定する

## プロジェクト作成

- Product Name: `TicketLauncher`
- Interface: SwiftUI
- Language: Swift
- Include Tests: 有効
- Storage: SwiftData を第一候補
- Deployment Target: iOS 17 以降を初期候補とし、作成時に確定
- アプリ本体、単体テスト、UI テストのターゲットを用意する

Xcode が生成した個人用設定はコミットせず、共有 Scheme とプロジェクト設定だけを管理する。

## 実装フェーズ

### Phase 1 — モデルと入力

- `TicketEvent` モデル
- イベント一覧、追加、編集、削除
- イベント名、日時、HTTPS URL の検証
- 永続化と再起動後の復元
- 入力検証と保存の単体テスト

### Phase 2 — 通知

- 通知権限の要求と現在状態の表示
- イベント ID を使った通知予約・取消
- 編集時の再予約、削除時の取消
- 60 秒未満前に登録した場合の規則を確定して実装
- 通知内容から対象イベントを特定する導線

### Phase 3 — カウントダウン

- 現在時刻を注入可能な時刻計算サービス
- 日・時・分・秒の残り時間表示
- 0 秒到達を一度だけ通知する状態管理
- 前面復帰時の再計算
- 境界値の単体テスト

### Phase 4 — URL 起動

- 前面状態で 0 秒到達時に一度だけ `openURL`
- 通知タップから対象 URL を開く
- 不正 URL やイベント削除後の安全な失敗
- Scene lifecycle をまたぐ重複起動防止

### Phase 5 — 仕上げと検証

- VoiceOver ラベル、Dynamic Type、空状態
- 単体テストと UI テスト
- シミュレータ確認
- iPhone 実機で通知、ロック画面、バックグラウンド復帰、URL 起動を確認
- README、仕様、開発ログ、CHANGELOG を実装結果に合わせて更新
- 実装担当とは別の担当によるレビュー

## 推奨構成

```text
TicketLauncher/
  App/
  Models/
  Features/
    EventList/
    EventEditor/
    Countdown/
  Services/
    NotificationScheduling/
    Clock/
    URLOpening/
  Resources/
TicketLauncherTests/
TicketLauncherUITests/
docs/
```

実際の構成は Xcode 生成物を基準にし、不要な抽象化は追加しない。

## 検証コマンドの方針

利用可能な Xcode と Simulator 名を確認してから、固定の端末名を決め打ちせずに `xcodebuild test` の destination を指定する。実行した Xcode バージョン、OS、端末、結果を `docs/development-log.md` に記録する。

## 完了ゲート

- `docs/mvp-spec.md` の受け入れ条件がすべて確認済み
- 重大な回帰なし
- 関連テスト成功
- 実機確認成功
- 独立レビュー完了
- README、仕様、ログ、CHANGELOG 更新済み
