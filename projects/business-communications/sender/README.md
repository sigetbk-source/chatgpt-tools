# テスト用LINE送信キュー

受信用とは別のApps Scriptに `Code.gs` と `appsscript.json` を配置し、受信と同じテスト台帳に送信キューを作成する。Apps ScriptのAdvanced Sheetsサービスが必要。送信キューの依頼内容・承認署名・履歴は、スクリプトから更新する。表示用の参照が必要なときはフィルタビューを使う。

スクリプトプロパティに `ENVIRONMENT=test`、`TEST_ROOT_ID`、`TEST_SHEET_ID`、`TEST_BOT_ID`、受信イベントから本人確認した `TEST_SELF_LINE_ID`、`TEST_SELF_EMAIL`、32文字以上の `SEND_APPROVAL_SECRET`、呼び出し元ごとの鍵を含む `SEND_CLIENTS_JSON` を設定する。LINEテスト用トークンは `LINE_TEST_ACCESS_TOKEN` に設定する。作成・疎通試験では `ENABLE_TEST_PUSH` を未設定にしておく。

1. `setupTestSendQueue` を実行する。
2. 手動検証用の呼び出し元から `client.py enqueue` でテキストを登録する。調査用に `client.py status` で同じ呼び出し元が登録済みの送信IDを照会できる。依頼が同じで冪等性キーも同じ場合は既存IDを返す。本文・宛先・時刻等が違う場合は拒否する。
3. 送信IDと内容を確認し、`SEND_APPROVE_ID` に送信IDを設定して `approveTestSend` を実行する。承認署名は依頼元・冪等性キー・依頼JSON・登録日時を含む。編集後は送信前の記録を再承認する。
4. テスト用トークンと本人宛てを確認できたら `ENABLE_TEST_PUSH=yes` とし、合成テキスト1件を `processTestSend` で手動検証する。確認後、頻度を決めて定期起動する。

Pythonクライアントは `LINE_SEND_TEST_URL`、`LINE_SEND_CLIENT_ID`、`LINE_SEND_CLIENT_SECRET` を環境設定から受け取る。Apps Scriptの認証付きPOSTは `client.timestamp.payload` をHMAC-SHA256で署名し、`payload` はUTF-8 JSONの標準Base64表記。Apps Script/GAS呼び出し元も同じ形式を使える。

送信前にBot ID・現在の使用量・月間上限を照合する。試験用上限 `TEST_MONTHLY_LIMIT` の既定値は20通。結果不明は保留する。APIが一時失敗を返した場合のみ、同じLINEリトライキーで最大3回試行する。自動のプラン変更は行わない。Driveリンクはテストフォルダ直下の対象で、本人のアクセスを確認できるファイルに限る。

送信予定人数を加算してから残枠を判定する。月間利用量または上限の応答が不正・取得不能な場合は送信せず「条件待ち」。残枠不足は「上限保留」。`TEST_QUOTA_WARNING_PERCENT`（既定80）に到達する場合は順次、送信履歴に `quotaWarning` を記録する。外部通知先は未接続。

本番計画は日本向けコミュニケーションプランの月200通を初期想定とし、納品60通・本人通知30通・その他30通で120通、残80通と仮置きする。実プランと宛先人数別の見込みを本番移行前に確認する。 [LINE公式アカウントの料金プラン](https://www.lycbiz.com/jp/service/line-official-account/plan/)

現段階の対応は本人宛てテキストとDriveファイルURLのPush。テンプレート・宛先は呼び出し元が決める。定期起動、返信トークンによる返信、直接メディア送信、本番移行、料金上限通知は未検証または未実装。

## 検証

```sh
/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc sender/Code.gs sender/test_sender.js
```

- [LINE Messaging APIリファレンス](https://developers.line.biz/en/reference/messaging-api/)
- [LINEのAPI再試行](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
