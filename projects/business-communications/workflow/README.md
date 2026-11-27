# 宛先照合と案件判断のドライラン

`contacts.py` は照合済みの内部連絡先、Googleコンタクトからの取得値、確認済みLINE ID、案件ごとの担当者を受け取って宛先を解決する。同姓や複数候補、期限・チャネル相違などで確定できない場合は確認を返す。台帳を更新する機能は持たない。

`decide.py` は原文、確認済み案件ID、既存カレンダー候補と版、ファイルの分類、許可された操作をJSONで受け取る。日付や案件が未確定なら確認に回す。操作案は版と根拠を照合し、同じ操作には同じ冪等性キーを返す。

`tasks.py` は同じ判断処理をSQLiteに保存し、担当確保・リース満了後の引き継ぎ・判断履歴・再処理を扱う。環境は `test` 固定。スクショ・Gmail・SMSの根拠を入れるときは `selected_by_owner: true` が必要。

```sh
python3 -B -m unittest discover -s workflow -v
python3 workflow/tasks.py /path/to/test.sqlite3 add /path/to/task.json
python3 workflow/tasks.py /path/to/test.sqlite3 run
python3 workflow/tasks.py /path/to/test.sqlite3 show TASK_ID
python3 workflow/tasks.py /path/to/test.sqlite3 requeue TASK_ID
```

テスト台帳の「受信履歴」タブをCSVで出力すると、`add-line` で指定した受信IDの1件を判断キューに登録できる。保存済みのテスト用LINEメッセージだけが対象。同じ受信IDの同じ内容は再登録しても1タスクに収まる。原文と添付のDrive IDを証拠として保持する。添付の中身は読み込まないため、ファイルの内容や移動先はこの経路では確定しない。CSVとSQLiteには連絡内容が含まれるため、テスト・検証用の非公開領域に置く。

```sh
python3 workflow/tasks.py /path/to/test.sqlite3 add-line /path/to/test-inbox.csv RECEIPT_ID
python3 workflow/tasks.py /path/to/test.sqlite3 run
python3 workflow/tasks.py /path/to/test.sqlite3 show TASK_ID
```

`show` で最新の判断案と検証結果を確認する。案件が不明な場合は保留し、この取込経路ではカレンダー、Notion、Drive、送信キューに対する操作を許可しない。

2026-11-27の検証では、テスト用LINEで受信したテキスト1件と画像2件の台帳データを使い、同じ受信IDを再投入しても3件の判断タスクに収まることを確認した。案件は未確定のままレビュー待ちになり、外部操作案は0件だった。改行を含む日程変更の根拠抽出、未保存・本番行・重複IDの拒否は合成データで確認した。

macOSで明示選択したスクショは、Visionを使ったローカルOCRから判断キューに投入できる。画像は外部に送らず、原文との照合用に画像ハッシュを保持する。認識結果に記載された年月日は判断案に含められる。日程の書き込み権限は付与しない。

```sh
swiftc workflow/screenshot_ocr.swift -o /tmp/business-screenshot-ocr
python3 workflow/tasks.py /path/to/test.sqlite3 add-screenshot /path/to/screenshot.png --ocr /tmp/business-screenshot-ocr
```

判断結果をGoogleカレンダーやNotionに反映するには、実サービスの読取アダプターと、承認済みの実行アダプターを追加する。Google People連携、自動取込、モデルAPI接続、実際の書き込みは未実装。

OCR実行ファイルのビルドと、模擬認識結果から判断タスクを作るテストは確認済み。画像からの文字認識は、この検証環境でVisionがエラーを返したため未確認。
