# 宛先照合と案件判断のドライラン

`contacts.py` は照合済みの内部連絡先、Googleコンタクトからの取得値、確認済みLINE ID、案件ごとの担当者を受け取って宛先を解決する。同姓や複数候補、期限・チャネル相違などで確定できない場合は確認を返す。台帳を更新する機能は持たない。

`decide.py` は原文、確認済み案件ID、既存カレンダー候補と版、ファイルの分類、許可された操作をJSONで受け取る。日付や案件が未確定なら確認に回す。操作案は版と根拠を照合し、同じ操作には同じ冪等性キーを返す。

`tasks.py` は同じ判断処理をSQLiteに保存し、担当確保・リース満了後の引き継ぎ・判断履歴・再処理を扱う。環境は `test` 固定。スクショ・Gmail・SMSの根拠を入れるときは `selected_by_owner: true` が必要。

```sh
python3 -B -m unittest discover -s workflow -v
python3 workflow/tasks.py /path/to/test.sqlite3 add /path/to/task.json
python3 workflow/tasks.py /path/to/test.sqlite3 run
python3 workflow/tasks.py /path/to/test.sqlite3 requeue TASK_ID
```

判断結果をGoogleカレンダーやNotionに反映するには、実サービスの読取アダプターと、承認済みの実行アダプターを追加する。Google People連携、OCR、自動取込、モデルAPI接続、実際の書き込みは未実装。
