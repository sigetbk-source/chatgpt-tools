# chatgpt-tools

ChatGPT / AI ワークフローと独立ツールを、共通の GitHub リポジトリで管理するための場所です。

## 構成

| ディレクトリ | 役割 | 例 |
| --- | --- | --- |
| `skills/` | 再利用可能な ChatGPT / AI ワークフロー・スキル | `hawks-magic-chart/` |
| `projects/` | 独立したアプリや実装プロジェクト | `ticket-launcher/` |
| `docs/` | リポジトリ共通の運用ルール・設計方針 | `github-operations.md` |

## 入口

- [Skills](skills/README.md)
- [Projects](projects/README.md)
- [GitHub 運用ルール](docs/github-operations.md)

新しい分野や小規模ツールは原則としてこのリポジトリに追加し、公開範囲、権限、CI/CD、リリース、履歴分離などの明確な理由がある場合だけ独立リポジトリを検討します。
