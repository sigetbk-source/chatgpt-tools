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

## 個人LINEの転送専用グループ（テスト）

1. 運用者が確認した専用グループIDと本人IDを受信用GASの `TEST_FORWARD_GROUP_ID`、`TEST_FORWARD_OWNER_ID` に保存する。両方の一致が取れた投稿だけが判断エンジンの転送対象になる。ID未取得や本人以外の投稿も原文・添付の保存対象になる。
2. テスト用GASで `setupTestStorage` を実行し、現行の27列の右に「受信経路詳細」「LINE送信元種別」「LINEグループID」「転送受付」の4列が加わることを確認してから、既存テスト用Webアプリを新バージョンへ更新する。テストWebhook URLは維持する。既存の保存処理は新しい4列の有無に関わらず既存列を使う。
3. 過去分は `backfillTestInboxRouting` で保存済み原文から新4列だけ補完できる。1回の上限は40件。原文ファイルIDが付くまでは対象外。原文のイベントIDと受信IDが違う場合は停止する。復旧後の再実行で記入済みの行は変更しない。
4. 専用・一般グループの区別と本人以外の除外を確認する。元発言者・元日時は受信側で生成せず、Webhook送信者、LINE発生日時、受信日時を保持する。Keepメモはこの経路の対象外。

## 受信経路の死活確認（テスト）

外部の実行環境に、既存Workerの `/healthz` を `LINE_RECEIVER_WORKER_HEALTH_URL`、受信GASの `/exec` を `LINE_RECEIVER_GAS_URL` として設定する。GASと監視側に共通の `RECEIVER_HEALTH_SECRET` を設定する。32文字以上でWebhook中継用の鍵と共有しない。

```sh
python3 receiver_health.py /path/to/private-health.sqlite3 --failures 3 --max-pending-age 600
```

Workerの設定確認、認証付きGASの台帳読み取り、専用Driveフォルダ内の合成マーカー書き込み・読み戻しを行う。合成マーカーは毎回同じ専用ファイルを更新し、受信履歴や判断キューに登録しない。滞留の初期値は10分、連続失敗は3回。業務受信がないことだけで障害にしない。外部の実行側はJSONの `notice` が `outage` または `recovered` のとき、LINE送受信に依存しない通知先に渡せる。常時監視の起動間隔と通知先は試験後に決める。このチェックでLINEからWorkerへの実配送は検査できない。

## 資格情報と移行前の確認

- トークン・鍵の入力や表示中は画面取得とログ出力を停止する。作業記録には設定名と設定有無だけ残す。露出した値は運用から外し、失効・再発行後に対象Botを照合してから接続を戻す。
- 本番前はWord・PDF・動画について台帳、MIME、サイズ、原文と実ファイルIDを照合する。音声・位置・スタンプと未対応種別、複数転送、スクショと実ファイルの組を追加検証する。
- 切替前に台帳・保存フォルダ・ファイルの実効共有範囲、送信通数の見積もり、監視の通知と復旧をテスト環境で確認する。切替時は旧経路の最終受信と新経路の最初の受信、その間の記録と再配信を照合する。異常時は保存データを残し、記録した直前のWebhook設定・デプロイ版に戻して再配信の重複を受信IDで照合する。本番切替は承認後に実施する。

- [LINE Webhook署名検証](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)
- [LINEグループでの受信](https://developers.line.biz/en/docs/messaging-api/group-chats/)
- [Apps Scriptウェブアプリ](https://developers.google.com/apps-script/guides/web)
- [Apps Scriptの上限](https://developers.google.com/apps-script/guides/services/quotas)
- [Cloudflare Workersのプラン別上限](https://developers.cloudflare.com/workers/platform/limits/)
