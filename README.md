# chatgpt-tools

人間と複数の LLM が、再利用スキル・独立プロジェクト・共通運用ルールを同じ GitHub リポジトリから参照するための入口です。

## 構成

| 場所 | 役割 |
| --- | --- |
| `skills/` | 繰り返し利用するワークフロー、実行コード、固定資産 |
| `projects/` | 独立して動くアプリや開発物 |
| `docs/` | リポジトリ全体の運用・LLM 利用ルール |

## 主要コンテンツ

- [`skills/hawks-magic-chart/`](skills/hawks-magic-chart/README.md): ホークスの優勝マジック推移を 16:9 PNG に決定論的に描画
- [`projects/ticket-launcher/`](projects/ticket-launcher/README.md): チケット発売時刻に販売ページへ移動する iPhone アプリ
- [`projects/premiere-transcript-pipeline/`](projects/premiere-transcript-pipeline/README.md): Premiere非依存で文字起こし・単語TC・話者情報を構築し、Premiere Transcript JSONへ戻す編集基盤
- [`docs/github-operations.md`](docs/github-operations.md): GitHub の集約・移行・変更管理ルール
- [`docs/llm-usage.md`](docs/llm-usage.md): ChatGPT / Codex / Claude / Gemini / Cursor からの参照方法

## 参照順序

1. この `README.md`
2. [`AGENTS.md`](AGENTS.md)
3. 対象の `skills/<name>/README.md` または `projects/<name>/README.md`
4. 対象に `AGENTS.md`、`SKILL.md`、仕様書があれば必要なものだけ読む
5. GitHub 操作を伴う場合は [`docs/github-operations.md`](docs/github-operations.md) も読む

`CLAUDE.md` と `GEMINI.md` は各ツール向けの薄い入口です。仕様や運用の正本は、この README、`AGENTS.md`、対象ディレクトリの README に置きます。
