# Hawks Magic Chart

福岡ソフトバンクホークスの優勝マジック点灯日から指定日までを、X 投稿向けの静止画 PNG として描画します。画像生成 AI で既存画像を書き換えず、検証済み JSON を入力として Python で毎回再描画します。

## 仕様

- Matplotlib の非対話バックエンドを使った決定論的な Python 描画
- 2880 × 1620 px（16:9）の PNG
- 日付順の step chart。横ばいは水平、減少日は垂直に落下
- 点灯日前へ線を延ばさない
- 試合のない日も直前のマジックを持ち越して入力する
- 試合日は折れ線直下に対戦相手の固定ロゴを表示する
- タイトル左にホークスの固定ロゴを表示する
- 数値、日付、対戦相手は入力 JSON からのみ取得する

## 固定資産

`assets/` の `hawks_sh.png`、`F.png`、`L.png`、`M.png`、`E.png`、`B.png` を再利用します。ロゴを文字や別フォントで代用せず、更新時も既存資産を優先してください。

## 入力

JSON の `entries` に日ごとの値を昇順で指定します。

```json
{
  "title": "’26 福岡ソフトバンクホークス 優勝マジック推移",
  "entries": [
    {"date": "2026-08-05", "magic": 37, "opponent": "F"},
    {"date": "2026-08-06", "magic": 37, "opponent": "F"},
    {"date": "2026-08-07", "magic": 35, "opponent": "L"}
  ]
}
```

`opponent` は `F`、`L`、`M`、`E`、`B` または `null` です。`null` は試合なしを表します。

## データ検証

描画前にスクリプトが次を検証します。

- `entries` が空でない
- 日付が ISO 形式で重複せず、1日ずつ連続している
- マジックが 0 以上の整数で、前日から増えていない
- 対戦相手コードが対応済みで、固定資産が存在する

入力値はホークス公式、NPB、信頼できる試合情報のうち複数情報で照合し、確認済みの値だけを書き込みます。

## 実行

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 render_magic_chart.py example_2026-08-20.json output.png
```

成功時は指定先に 2880 × 1620 px の PNG を出力します。入力不備、未対応ロゴ、資産不足では終了コード 2 で停止し、不正なグラフは出力しません。
