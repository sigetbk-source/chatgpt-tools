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

転送専用グループの記録は、新4列付きの受信履歴CSVから `forwarded.py` で処理する。Webhook上の本人・グループで区切り、「佐藤さんから」「転送元：佐藤さん」などの申告や、単独の終了表現を区切り候補として扱う。新着が途切れてから初期値120秒待ち、添付・原文の保存を確認する。保存待ちのまま30分経過した単位や保存失敗は判断案を出さずレビュー対象にする。

```sh
python3 workflow/forwarded.py /path/to/test-inbox.csv /path/to/private-reviews.sqlite3 --quiet-seconds 120
```

CSVを繰り返し確認する場合は同じSQLiteを使う。元発言者名は申告に根拠を持つ候補として扱い、複数発言に一律適用しない。元日時・元連絡先IDは不明のまま残す。同じ本文の別単位は再転送候補として表示する。既にレビュー保存した受信IDと重なる遅着は訂正候補として履歴を追加する。完全に別の受信IDで届いた補足は新しい要確認として扱う。

`screenshot_ocr --layout screenshot.png` で読取位置を含むJSONを取得する。画像の保存済みDrive IDをキー、そのJSON配列を値とするオブジェクトを `--layout-evidence` で指定すると、画像ごとに表示順、左右、日付・時刻、ファイル名の候補を保持する。月日だけの表示から年を埋めない。左右の位置から人物を確定しない。表示名と同名の実添付があれば候補として列挙する。`--local-files` には保存済みDrive IDをキー、選別したローカルファイルのパスを値とするJSONを指定できる。ファイルサイズとSHA-256を記録し、ハッシュが一致しても受信単位を自動統合しない。

Mac版LINEの全画面では、検索欄と入力欄が識別できる場合に会話欄を抽出する。検索欄・入力欄を確認できない画像では、`--layout-evidence` の画像ごとの値を `{"lines": [...], "region": [左, 上, 幅, 高さ]}` として会話欄を指定できる。座標は画像全体に対する0～1の値。会話欄を確認できた場合だけ、吹き出しの候補と近接する表示時刻を組にする。複数の組が考えられる場合は、時刻を空欄にしておく。明示した `captured_at`（タイムゾーン付きのISO日時）がある場合に限り「今日」「昨日」等を日付候補に変換する。転送時刻と画面端の時計は基準に使わない。

`--local-files` と `--ocr` を一緒に指定すると、同じ実行で保存済みの選択画像を読み取れる。OCR認識器が失敗した画像は `unavailable` と表示し、ほかの証拠の処理を継続する。確認済みの位置付き観測JSONは `--layout-evidence` で渡せる。スクショと添付の対応、発言者・時刻、同じ連絡かどうかはレビュー対象。履歴テキスト読取は未実装。

2026-11-27の検証では、テスト用LINEで受信したテキスト1件と画像2件の台帳データを使い、同じ受信IDを再投入しても3件の判断タスクに収まることを確認した。案件は未確定のままレビュー待ちになり、外部操作案は0件だった。改行を含む日程変更の根拠抽出、未保存・本番行・重複IDの拒否は合成データで確認した。

macOSで明示選択したスクショは、Visionを使ったローカルOCRから判断キューに投入できる。画像は外部に送らず、原文との照合用に画像ハッシュを保持する。認識結果に記載された年月日は判断案に含められる。日程の書き込み権限は付与しない。

```sh
swiftc workflow/screenshot_ocr.swift -o /tmp/business-screenshot-ocr
python3 workflow/tasks.py /path/to/test.sqlite3 add-screenshot /path/to/screenshot.png --ocr /tmp/business-screenshot-ocr
```

判断結果をGoogleカレンダーやNotionに反映するには、実サービスの読取アダプターと、承認済みの実行アダプターを追加する。Google People連携、自動取込、モデルAPI接続、実際の書き込みは未実装。

本人のMacのターミナルで、実際のLINEスクショからVisionが文字と座標を取得できることを確認した。会話抽出・日時候補・時刻対応は実際の配置を再現したデータで検証した。Workの実行環境ではVisionがエラーを返すため、OCRを含む自動実行は未検証。
