# 複数 LLM からの利用方法

## 共通原則

このリポジトリでは、LLM 固有の指示より先に、LLM 非依存の仕様と運用を共有します。

- ルート `README.md`: 人間と全 LLM の共通入口
- `AGENTS.md`: 探索・編集・検証に関する共通指示
- `skills/<name>/README.md`: skill の実装、入力、出力、検証方法の正本
- `projects/<name>/README.md`: project の概要、主要ファイル、開発入口の正本
- `SKILL.md`: ChatGPT / Codex が skill を自動選択するための起動条件と短い実行手順
- `.codex/config.toml`: Codex サブエージェントの共通既定値
- `.codex/agents/*.toml`: Codex の役割別モデル・推論量・権限・実行指示
- `CLAUDE.md` / `GEMINI.md`: 共通文書へ案内する薄いアダプタ

同じ説明を複数ファイルへ複製せず、仕様変更は正本へ反映します。固有アダプタには共通ルールを再掲しません。

## 推奨する参照方法

### ChatGPT / Codex

1. ルート `README.md` と `AGENTS.md` を読む。
2. 自動起動した `SKILL.md` の手順に従う。
3. 実装仕様は skill の `README.md` で確認する。
4. Codex でサブエージェントを使う場合は、`AGENTS.md` のルーティング方針と `.codex/agents/*.toml` の役割定義に従う。

### Codex の推奨サブエージェント構成

- `explorer_lite`: `gpt-5.6-luna` / low。読み取り中心の探索、列挙、定型チェック。
- `worker`: `gpt-5.6-terra` / medium。仕様が明確な通常実装と限定テスト。
- `reviewer`: `gpt-5.6` / high。正確性、回帰、セキュリティ、難しいレビュー。
- `browser_operator`: モデルと推論量を固定しない。Browser / Computer Use では親セッションで選んだモデルを継承し、Astra など UI 操作に強いモデルを必要なときだけ使えるようにする。

`.codex/config.toml` はサブエージェントを有効にし、同時スレッド数を 4 に抑える。モデルの全体既定値は固定せず、役割別 TOML で安価なモデルを明示する。これにより `browser_operator` や未指定の役割は親モデルを継承でき、高価なモデルを難しい判断や UI 操作など効果が出やすい工程へ集中させられる。

### Claude

1. `CLAUDE.md` を入口にする。
2. 参照先の `AGENTS.md`、ルート README、対象 README を読む。

### Gemini

1. `GEMINI.md` を入口にする。
2. 参照先の `AGENTS.md`、ルート README、対象 README を読む。

### Cursor など

リポジトリルールとして `AGENTS.md` とルート README を指定し、対象ディレクトリの README を追加コンテキストにします。ツール固有ルールを作る場合も、共通仕様は複製せず参照だけを置きます。

## 変更時の注意

- `SKILL.md` だけを更新して実装仕様を変えない。
- skill の入力・出力・依存関係・検証方法が変わる場合は、対象 README を先に更新する。
- LLM 固有ファイルの違いは起動や読み順に限定し、コードや資産の派生コピーを作らない。
- Codex の利用可能モデルやサブエージェント仕様が変わった場合は、`.codex/config.toml` と `.codex/agents/*.toml` のモデル指定を見直す。
