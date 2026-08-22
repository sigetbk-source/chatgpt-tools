# Hawks Magic Chart

ホークスの優勝マジックを、毎日のデータから固定レイアウトのPNGへ再描画するスキルです。

## 実行

```bash
python3 -m pip install -r requirements.txt
python3 render_magic_chart.py example_2026-08-20.json hawks_magic_2026-08-20.png
```

入力は日付順で、途中の日を省略しない `data` 配列です。試合なしの日は直前のマジックを持ち越して `opponent` を `null` にします。タイトルは確定仕様としてスクリプト内に固定されています。

対戦相手コード:

- `F`: 北海道日本ハム
- `L`: 埼玉西武
- `M`: 千葉ロッテ
- `E`: 東北楽天
- `B`: オリックス

出力は2880×1620のPNGです。数値・対戦相手を更新するときは、既存PNGを編集せずJSONから再生成してください。
