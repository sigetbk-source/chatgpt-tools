# LINE受信基盤のセットアップと検証

## 構成

テスト用LINE → HTTPS中継（Cloudflare WorkersまたはPythonコンテナ）→ 専用Apps ScriptのdoPost → 受信台帳。1分間隔の保存ワーカーが台帳から原文・添付をDriveへ保存する。台帳に未書き込みの場合は中継が503を返し、再配信で受け直す。台帳に書き込み済みの場合はイベントIDで重複排除する。原文は日本時間の年月フォルダにイベント別JSONとして保存し、短命のreply tokenを除外する。元ファイル名は台帳にも保持する。元の名前がないデータにはイベントIDと種別で一意な名前を付ける。

保存は最長6回の自動試行とし、失敗を台帳に表示する。タイムアウトなどで処理が止まった行は7分間のリース終了後に再取得する。Drive作成直後の台帳障害では同一のファイル名から既存のファイルを再取得する。試験中は受信履歴シートの行の並べ替え・削除を避け、閲覧にはフィルタビューを使う。

## ローカル検証

```sh
python3 -B -m unittest -v test_relay
/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc Code.gs test_inbox.js
node edge/test_worker.mjs
```

PythonテストはHTTPハンドラーの入力・応答をメモリ上で検証する。Apps Script側はGoogle／LINEサービスの代替実装で複数イベント、重複、同名、保存待ち、障害後の再開、署名エンベロープ判定を検証する。

## テスト環境へ導入

LINE接続前に、専用GASに `ENVIRONMENT=test` と `TEST_ROOT_ID` だけを設定して `runSyntheticTextTest` または `runSyntheticAttachmentTest` を実行できる。LINE通信と定期トリガー登録を伴わず、非公開のテストルートに合成データを保存する。テキストは台帳の重複排除と原文ログを、添付は同名2件と名前なし1件のDrive ID、名前、MIMEとバイト列を照合する。初回はテストGASの権限を確認する。

1. マイドライブに非公開の `業務連絡管理（テスト専用）` フォルダを用意する。新しいスタンドアロンApps Scriptに `Code.gs` と `appsscript.json` を配置して、Advanced Sheetsサービスを有効にする。既存の案件管理GASとは別プロジェクトとする。
2. 専用GASのスクリプトプロパティに `ENVIRONMENT=test`、`TEST_ROOT_ID`、`WORKER_SECRET`、`LINE_TEST_ACCESS_TOKEN` と、テストチャネルの `TEST_BOT_BASIC_ID` を設定する。ワーカー共有鍵は新規の十分にランダムな32文字以上。 `configureTestBotIdentity` を実行し、トークンのBotとベーシックIDが一致する場合に `TEST_BOT_ID` を取得する。
3. `setupTestStorage` を実行する。テスト用ルートに受信スプレッドシート、原文ログ／受信ファイルの子フォルダを作成し、テストGASに処理トリガーを登録する。Webアプリは所有者として実行し、署名付きPOSTだけ業務処理を通す。Apps ScriptのURLは `/exec` を用いる。
4. Cloudflare WorkersのFreeプランを使う場合は、`edge/` のWrangler設定からデプロイする。コンテナのHTTPSホストを使う場合は、ルートのDockerfileから立ち上げる。いずれも秘密領域に `GAS_WORKER_URL`、`GAS_WORKER_SECRET`、`LINE_TEST_CHANNEL_SECRET`、`LINE_TEST_BOT_ID` を設定する。GAS側と中継側の共有鍵を一致させる。ホスト側でTLSを終端し、Webhook本文・リクエストヘッダーをログに出さない。
5. テスト用LINEのWebhook URLに `/line/webhook` を設定してVerifyを実行する。再配信設定を確認し、合成テキスト、画像、同名PDFを送って、テスト台帳の行、Driveファイル、エラー状態を確認する。検証後に停止する場合はテスト用のWebhook設定とテストGASのトリガーだけを対象とする。

## テスト用LINEでの実受信（2026-11-27）

- Cloudflare Workerと専用Apps Scriptをデプロイし、LINE DevelopersのWebhook検証に成功した。設定の再表示でWebhookが有効であることを確認した。
- LINEアプリから送信したテキスト1件と画像2件を受信した。台帳の3件が「保存済み」になり、Driveに原文3件と画像ファイル2件があることを照合した。
- 検証範囲はテスト用LINE、テスト台帳、テスト用Driveフォルダ。

## 未検証

実際のLINEからの再配信と大容量添付の処理、画像以外の形式、取戻しイベント時の閲覧制限・保管期限、媒体別の取得期限、受信者プロフィールの補完、台帳件数増加時の運用、本番環境。Apps ScriptのURL Fetch応答上限を超える添付はこの段階では保存失敗として記録する。LINEの送信、案件管理、カレンダー、業務Driveの変更は受信処理の経路に含まれない。

- [LINE Webhook署名検証](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
- [Apps Scriptウェブアプリ](https://developers.google.com/apps-script/guides/web)
- [Apps Scriptの上限](https://developers.google.com/apps-script/guides/services/quotas)
- [Cloudflare Workersのプラン別上限](https://developers.cloudflare.com/workers/platform/limits/)
