# Premiere 制作支援

Adobe Premiere Pro向けのローカル制作支援ツールです。元素材の文字起こし、
Whisperの解析結果、台本・資料を比較し、人が確認した修正を対象素材へ戻します。
ナレーション制作ランタイムと組み合わせると、生成音声の更新とSRT同期も扱えます。

## 構成

- `speaker_repair/`: 音声処理、資料照合、確認画面、ローカル連携サーバー。
- `uxp-plugin/`: Premiere内の制作支援パネルと書き戻し操作。
- `scripts/`: Adobe UXP Developer Toolsを使う開発用の補助処理。
- `deployment/local-runtime/`: iCloud同期対象から実行環境を切り離すビルド部品、CEP連携、起動処理。
- `tests/`: 架空データを使う回帰テスト。

Premiere側の変更前にプロジェクトと素材の識別情報を照合し、バックアップと
読み戻し確認を行います。自動提案は確認・承認してから反映します。

## 必要なもの

- macOSと、対応するAdobe Premiere Pro。基本対応は25.6以降です。
- 再配置可能なPython 3.12配布物。依存部品は `pyproject.toml` を参照。
- UXPの開発・CCX作成にはAdobe UXP Developer Tools、通常インストールにはAdobeのプラグイン管理ツール。
- ナレーション制作をビルドする場合は、別途用意した互換ナレーションランタイム
  （`launcher.py`、`project_server.py`、`voice_workflow.py`などを含むもの）。
  この公開ソースにはそのランタイム一式を含めていません。
- 音声認識モデルと制作素材。モデル重み、Python本体、顧客資料、認証情報は同梱しません。

## ローカル版のビルド

実行環境は通常 `~/Library/Application Support/Premiere制作支援/` に置きます。
書類・デスクトップのiCloud同期を使っていても、ツール本体の起動が
未ダウンロードファイルに依存しないようにするためです。

[ビルド手順](deployment/local-runtime/README.md)、
[使用法](docs/manual/local-installation.md)、
[実行環境仕様](docs/specification/local-runtime.md)を参照してください。
公開用のUXPソースは利用者固有のパスを含みません。ビルド時に、そのMacの
ローカル保存先とCEP連携先へ書き換えます。ビルド前のプラグインは配布用ではありません。

依存部品を導入した環境で、次のように確認できます。

```sh
python3 deployment/local-runtime/test_offline_guards.py
node deployment/local-runtime/cep-and-app/test-launcher.js
node deployment/local-runtime/cep-and-app/test-main-state.js
```

ナレーションランタイムを別途必要とする旧テストもあります。すべてのテストが
このソースだけで実行できるわけではありません。

## 制約

Premiere 25.6ではSRTキャプショントラック作成にCEP互換パネルを使います。
一部の自動書き戻し補助処理はUXP Developer Toolsを利用します。
通常版パネルを開くことと、すべての補助処理から開発ツール依存がなくなることは別です。
モデル本体は必要に応じてローカルキャッシュへ取得します。

## 出典

元のローカルリポジトリは `premiere-speaker-repair`（remote未設定）です。
参照ブランチは `fix/local-runtime-startup-20261115`、参照コミットは
`fe7e95cf0cda79ff03146161a05cc08cb3557b19`、コピー日は2026-11-15です。
追跡済みソースのスナップショットとして集約し、元のGit履歴は移植していません。
案件データ・過去の内部作業記録・利用者固有のパスを除き、公開用の架空例へ整理しています。
開発・レビューでは [DEVELOPMENT_RULES.md](DEVELOPMENT_RULES.md) を参照してください。
