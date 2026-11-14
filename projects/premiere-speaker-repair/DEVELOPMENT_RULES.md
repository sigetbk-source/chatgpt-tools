# 共通開発ルール

この文書は、`premiere-speaker-repair` 全体に適用する共通開発ルールです。
新機能の追加、既存機能の修正、レビューでは、本書を前提として判断してください。

## 複雑な実装の独立レビュー

複雑な実装では、実装担当サブエージェントによる実装後、実装を担当していない独立したレビュー担当サブエージェントがレビューを行う。

問題が見つかった場合は、修正と独立レビューを必要な範囲で繰り返す。要求仕様を満たし、既存機能への重大な回帰がなく、関連テストが通過した時点で完了とする。

## Premiere UXP Action実装ルール

Premiere Pro 25.6を開発・動作確認環境としますが、UXPのAction処理は
Premiere Pro 26.3互換方式で実装します。

### 基本方針

- Premiere Pro 25.6で正常に動作する状態を維持する。
- 実装方法はPremiere Pro 26.3互換を優先し、将来の26系移行コストを最小化する。
- 本ルールは、文字起こし反映、マーカー追加、クリップ操作、シーケンス操作など、Premiereの状態を変更するすべての処理に適用する。

### Actionの生成

- すべての`create*Action()`は、対象Projectの`lockedAccess()`内部で生成する。
- Actionを`lockedAccess()`の外で事前生成しない。
- Actionを保持、キャッシュ、再利用しない。必要な都度生成する。
- Action生成ユーティリティも、`lockedAccess()`内部で呼び出されることを前提に設計する。

### Actionの実行

- 生成したActionは、原則として同じ`lockedAccess()`内の`executeTransaction()`で実行する。
- Premiereの状態を変更する処理は、プロジェクト共通のTransactionラッパーを経由させる。
- 複数のActionを一つの操作として扱う場合は、同一Transactionにまとめ、操作内容が分かるラベルを付ける。

### 実装・レビュー手順

- 新しいAction処理を追加するときは、必ず本ルールを適用する。
- 既存コードを変更するときは、変更範囲に加えてプロジェクト内の`create*Action`を検索し、本ルールに反する実装がないか確認する。
- Actionを`lockedAccess()`の外へ返す、非同期処理をまたいで保持する、後続処理で再利用する設計を導入しない。
- Premiere Pro 25.6で対象操作を動作確認し、Action生成からTransaction実行までが同じ`lockedAccess()`内に収まっていることをレビューする。

### 目的

- Premiere Pro 25.6との互換性を維持する。
- Premiere Pro 26系以降への移行コストを最小化する。
- Actionのライフサイクルに起因する不具合を防ぐ。
- Action関連コードの実装方式をプロジェクト全体で統一する。

## Premiere Pro 26.3移行・互換性ルール

近い将来にPremiere Pro 26系へ移行することを前提とし、25.6との併用期間に
UXP Core APIとPremiere DOM APIの差異が各機能へ拡散しないようにします。

### 対応バージョンと互換レイヤー

- 移行期間中はPremiere Pro 25.6と26.3の両方を正式な動作確認対象とする。
- 25.6と26.3でシグネチャ、戻り値、同期・非同期、または挙動が異なるAPIは、各機能から直接呼び出さず、専用の互換レイヤーへ集約する。
- バージョン分岐を機能コードへ直接記述しない。
- 新しいAPIを採用する前に、Adobe公式APIリファレンスの`Min Version`または`Since`を確認する。
- 26.3以降で追加されたAPIは、25.6対応期間中は機能検出を行い、代替処理または明示的な未対応表示を用意する。
- PremiereのホストバージョンとUXPランタイムバージョンを別々に扱い、起動時の診断ログへ記録する。
- 完全移行時には`manifest.json`の`host.minVersion`を`26.3.0`へ更新し、不要になった25.6向け分岐を計画的に削除する。

### 同期・非同期API

- APIが同期値とPromiseのどちらを返すかを、26.3のAdobe公式API定義で確認する。
- 同期APIに`.then()`を使用しない。
- 25.6と26.3で戻り値形式が異なるAPIは、互換レイヤーで同期値とPromiseを正規化する。
- Premiere DOMのプロパティ取得・設定に、理由なく`await`を付けない。
- `Sequence.setSelection()`は、26.3で`Promise<boolean>`から同期的な`boolean`返却へ変更されたものとして扱い、共通機能から直接呼び出さない。
- API更新時のレビューでは、引数と戻り値だけでなく、同期・非同期の変更も確認する。

### ロックとTransaction内の処理

- `lockedAccess()`と`executeTransaction()`のコールバックを`async`にしない。
- これらのコールバック内で`await`、Promise、ネットワーク通信、ファイル操作、タイマー処理を実行しない。
- 必要な非同期データ取得、入力検証、JSON変換は、ロック取得前に完了させる。
- Actionをロック外へ返さず、Action変数を非同期処理や後続処理へ持ち出さない。
- `executeTransaction()`の戻り値を必ず確認し、`false`の場合は失敗として処理する。
- すべてのTransactionに、PremiereのUndo履歴で操作内容を識別できる具体的なラベルを付ける。

### Premiereオブジェクトの保持

- Actionは保持、キャッシュ、再利用しない。
- `Project`、`Sequence`、`ClipProjectItem`などのPremiereオブジェクトを、プロジェクト切り替え、保存、再読み込みをまたいで無条件に信用しない。
- 長期保持する対象は、Project GUID、素材ID、シーケンスGUID、メディアパスなどの識別情報を基本とする。
- Premiereの状態を変更する直前に、アクティブProjectと対象オブジェクトを再取得し、GUID、ID、パスを再検証する。
- DOMオブジェクトのキャッシュは検索高速化の補助に限定し、変更前の同一性検証を省略する理由にしない。
- `Project.saveAs()`後も同じProjectオブジェクトが元プロジェクトを指し続けると仮定しない。

### 26.3以降のAPI採用

- `Since: 26.3`のAPIを、25.6対応期間中の共通コードから無条件に呼び出さない。
- APIの利用可否は、可能な限り`typeof api === "function"`などの機能検出で判断する。
- 機能検出結果とホストバージョンが食い違う場合は、安全側へ停止し、診断ログを残す。
- 26.3向け処理と25.6向け代替処理が、同じ入力に対して同等の結果を返すことをテストする。
- 非推奨と明記されたAPIを新規コードで採用しない。
- `Transcript.hasTranscript()`や`Transcript.querySupportedLanguages()`などの26.3追加APIは、完全移行までは互換レイヤー経由で利用する。

### 静的検査

- UXP JavaScriptには、Adobe公式の`@adobe/eslint-plugin-premierepro`を適用する。
- Actionのロック外生成、Transaction外の`addAction()`、ロック内の非同期処理、Actionのスコープ外持ち出しをエラーとして扱う。
- `lockedAccess()`で囲まれていない`executeTransaction()`と、UndoラベルのないTransactionを検出対象とする。
- Python文字列などに埋め込まれたUXP JavaScriptも検査対象とし、可能な限り独立した`.js`テンプレートへ分離する。
- 埋め込みコードを分離できない場合は、抽出してESLintを実行する仕組み、または同等の静的テストを用意する。

### 動作確認

- UXP/APIに関係する変更は、移行期間中、Premiere Pro 25.6と26.3の両方で確認する。
- Action処理は、実行結果に加えてUndoとRedoも確認する。
- 書き戻し後にプロジェクトを保存・再読み込みし、結果が保持されることを確認する。
- 処理中にアクティブProject、対象素材、シーケンスが変わった場合、安全に停止することを確認する。
- ActionまたはTransactionを変更した場合は、成功、Transaction拒否、対象不一致、保存失敗をテストする。
- 25.6向け互換処理には削除条件を記録し、期限なしの互換コードにしない。

### UXP UIとエラー通知

- UXPを一般的なWebブラウザーと同一視せず、使用するHTML、CSS、Web APIがUXPでサポートされていることを確認する。
- `alert()`、`confirm()`、`prompt()`を重要な結果通知の唯一の手段にしない。
- 失敗内容はパネル内表示と診断ログにも残し、ダイアログが表示されない環境でも確認できるようにする。
- ファイル、ネットワーク、外部アプリ連携に必要な権限だけを`manifest.json`へ宣言する。

### 公式資料

- [Premiere Pro UXP Changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
- [Understanding UXP APIs](https://developer.adobe.com/premiere-pro/uxp/resources/fundamentals/apis/)
- [Premiere API Reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/)
- [ESLint Support](https://developer.adobe.com/premiere-pro/uxp/resources/fundamentals/eslint-support/)
- [UXP Manifest](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
