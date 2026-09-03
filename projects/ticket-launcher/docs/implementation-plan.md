# MVP 実装計画

## 確認済み環境

- Xcode 26.6
- Product Name: `TicketLauncher`
- Interface: SwiftUI
- Language: Swift
- Bundle Identifier: `com.sigetbk.TicketLauncher`
- 外部パッケージなし
- 2026-08-14 時点の雛形ビルド成功

Xcode が生成した個人用設定はコミットせず、共有 Scheme とプロジェクト設定だけを管理する。

## 実装フェーズ

### Phase 0 — 雛形とテスト基盤

- Xcode 雛形を独立コミット
- Deployment Target を iOS 17.0 へ設定
- 単体テストターゲットを追加
- generic iOS ビルドとテスト実行環境を確認

### Phase 1 — モデル、保存、入力

- `TicketEvent` モデル
- `UserDefaults` を使う保存層
- イベント一覧、追加、編集、削除
- イベント名、未来日時、HTTPS URL の検証
- 発売前／発売済みイベントの分類と並び順
- 入力検証と保存の単体テスト

### Phase 2 — 発売前通知

- 通知権限の要求と現在状態の表示
- 10分前、5分前、3分前、1分前の通知予約
- 登録時点ですでに過ぎた通知候補の除外
- イベント ID を使った通知予約・取消
- 編集時の再予約、削除時の取消
- 通知タップから対象イベントを特定する導線
- 通知候補計算の単体テスト
- 日本語表示の5分刻み日時ピッカーと境界値テスト

### Phase 3 — 発売待機とURL起動

- 一覧からの明示的な販売ページ起動
- 通知タップから対象イベントの待機画面を開く
- 待機画面のカウントダウンと自動ロック抑止
- 前面待機中の発売時刻にSafariを1度だけ自動表示
- ロック・アプリ切り替え後の手動起動への安全な切り替え
- 不正 URL やイベント削除後の安全な失敗
- Scene lifecycle をまたぐ重複起動防止

### Phase 4 — 仕上げと検証

- VoiceOver ラベル、Dynamic Type、空状態
- 単体テストと UI 確認
- シミュレータ確認
- iPhone 実機で10/5/3/1分前通知、日本語の5分刻み日時入力、待機画面、画面点灯維持、発売時刻のSafari自動表示を確認
- README、仕様、開発ログ、CHANGELOG を実装結果に合わせて更新
- 実装担当とは別の担当によるレビュー

## 推奨構成

```text
TicketLauncher/
  Models/
  Features/
    EventList/
    EventEditor/
  Services/
    EventStorage/
    NotificationScheduling/
    URLOpening/
TicketLauncherTests/
docs/
```

実際の構成は Xcode のファイルシステム同期グループを活かし、不要な抽象化は追加しない。

## 検証方針

利用可能な Xcode と Simulator を確認してから、固定の端末名を決め打ちせずに `xcodebuild` の destination を指定する。実行した Xcode バージョン、OS、端末、結果を `docs/development-log.md` に記録する。

## 完了ゲート

- `docs/mvp-spec.md` の受け入れ条件がすべて確認済み
- 重大な回帰なし
- 関連テスト成功
- 実機確認成功
- 独立レビュー完了
- README、仕様、ログ、CHANGELOG 更新済み
