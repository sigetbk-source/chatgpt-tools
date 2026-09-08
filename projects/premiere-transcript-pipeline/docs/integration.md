# 非依存文字起こしと話者修正の統合

## 合流する正本

開発ブランチは `feature/premiere-transcript-pipeline`。非依存文字起こしのPhase 1は `cbbc7a4`、ユーザー実機確認は `44176ed`。今回の話者修正はその後続履歴であり、別の製品や別master形式へ分岐させない。mainへのマージは本作業に含めない。

## データの流れ

音声 → 外部ASRの単語時刻 → `bk-master-transcript/v0.1` → `master_edit.edit` → `premiere_export.convert` → `safe-writeback.writeback` → Premiere素材Transcript。

- masterの本文・単語時刻・話者key・内部注釈が正本。Premiere JSONの直接修正をmasterに代わる編集経路にしない。
- `master_edit.edit(master, operation, index, speaker, word_index=None)` は `assign`、`split`、`merge` を提供する。元masterを変更せず返す。単語時刻を維持し、分割境界での単語重複、結合時の異なる注釈の破棄を拒否する。結合で許容する発話間の無音は残る。
- `uxp/transcript-boundaries.js` はPremiere形式での低水準試験用として残す。新しい修正画面はmaster側APIを使う。
- masterにPremiere UUIDを入れない。別の素材別bindingでspeaker key → UUIDを保持し、`convert(master, speaker_ids)` に明示する。名前による推測対応はしない。マッピングは全話者keyを過不足なく含み、UUIDの重複を禁止する。
- 初回の `convert(master)` は従来通りUUIDを新規生成する。返されたspeakersと入力のspeakersの同じ順序から対応表を保存して以後の変換に渡す。既存素材では、確認済みの対応表だけを使用する。

```python
from master_edit import edit
from premiere_export import convert

corrected = edit(master, 'split', index=3, speaker='b', word_index=6)
premiere_json = convert(corrected, speaker_ids)
```

```sh
python3 python/premiere_export.py corrected-master.json candidate.json --speaker-ids speaker-ids.json
```

出力先は新規ファイルのみ。master、対応表、対象情報、バックアップ、実素材の候補JSONはローカルに保持する。

## 共通の書き戻し処理

`uxp/safe-writeback.js` は公式UXPホストを受け取る。必要な入力は `expected`（projectPath、clipName、mediaPath）、`candidate`、`expectedBefore`、`expectedReadback`、`persistBackup`、Undo用 `label`。

1. NFC正規化した対象パス・素材名を完全照合。初版はプロジェクト直下の同名が一つだけの素材に限定する。
2. 現在のexportと `expectedBefore` を全フィールド比較。
3. `persistBackup({target, transcript})` を呼び、永続保存されたバックアップのパスを必須にする。呼出側は一時メモリやログだけをバックアップと扱わない。
4. 対象と現在の内容を再照合してImport。読み戻し一致、保存成功、保存後の読み戻し一致の順に確認して初めて `imported/verified/saved=true` を返す。
5. Import以降の失敗は対象を再照合してバックアップを再Import。復旧したか、復旧内容を保存できたかを分けてエラーの `result.recovery` に返す。復旧に失敗しても元エラーとバックアップのパスを残す。

`expectedReadback` は単純にcandidateと同一とは限らない。Premiereが無音部分まで区間長を伸ばす挙動があるため、初版は既知サンプルの期待値を使用する。任意素材向けの許容差分規則は未確定。ホストから戻った内容をそのまま期待値へ採用して検証を通すことはしない。

この関数はプロジェクトのclose/open、素材ファイル変更、画面操作を行わない。呼出側は同一対象への並行操作を避ける。照合直後の対象変更を完全に防ぐ排他制御や、ビンの再帰探索は今後の実装対象。

## 統合検証結果（2026-09-08）

非依存文字起こしタスクがMLX Whisperで生成済みの45秒masterを入力にした。一区間の話者割当を変更し、別区間を12.22秒で分割。既存UUID対応表を指定して既存変換器で17区間・118単語へ変換した。共通writebackで検証コピーへImportし、既知の期待JSONとの全フィールド一致と実保存に成功した。バックアップは事前にディスクへ保存し、試験用persistBackupは今回の読取内容との一致を確認してその保存先を返した。これは汎用バックアップファイル選択UIの試験ではない。

ログはローカル `master-integration-before.log`、`master-integration-result.log`、バックアップは `master-integration.backup.json`。今回ASR自体の再実行はしていない。自動話者分離・任意音源への完全自動適用・修正画面統合は未完了。今回の統合実行後に追加の再オープンは行っていない。

Pythonテストはmasterの非破壊編集、単語・注釈保持、UUID固定、危険な入力拒否を確認。模擬UXPテストは通常適用、対象不一致、バックアップ失敗、保存失敗、復旧保存失敗、バックアップ中の内容変更を確認する。実I/O障害の再現とは区別する。

## 次の開発

1. このブランチを共通の開発基点にし、旧チェックアウトから独立して変更を重ねない。
2. 既存の修正画面を調査し、master編集APIと素材別bindingの永続化を接続する。
3. ASR実行環境・adapter、自動話者分離を整備する。現状の話者ラベルは手動の仮割当である。
4. 任意素材の読み戻し許容規則、バックアップ保存UI、処理状態表示を実装し、新しい日本語素材で通し検証する。
