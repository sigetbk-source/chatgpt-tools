# 独自JSON変換とPhase 1検証

## 最新結果（2026-09-07）

**Phase 1の機能試験は25.6.6の1素材で通過。MVP全体は未完了。**

| 項目 | 最終状態 |
| --- | --- |
| 独自JSONの素材へのImport | 公式UXPで成功、16区間・118単語を読み戻し確認 |
| 仮2話者の表示・切り替え | 実画面で確認 |
| 全文検索 | 「メニュー」が2件ヒット |
| 位置ジャンプ | 検索後のシーケンス再生位置12.3201秒をAPIで確認 |
| Text-Based Editing削除 | 約1.3013秒が映像・2音声トラックから削除され後続が詰まる |
| カット＆ペースト | 同じ素材区間が別の文字位置へ移動、映像・2音声の範囲一致 |
| 再生 | プログラム映像更新・再生位置進行・音声メーター動作を確認 |
| 保存・再オープン | 元の素材文字起こし118単語・話者2名が保持されることを確認 |
| 元MOV | 前後SHA-256一致、変更なし |

削除前のsequence endは45.011633秒、削除/カット後は43.710333秒、貼り付け後は45.011633秒。切り出した素材区間12.2122–13.5135秒が、貼り付け後はシーケンス12.5125–13.8138秒に入った。全3本の使用トラックで一致する。

**制約:** 話者は表示試験用の手動仮割当であり、正確性・自動話者分離は未検証。ASRの表記、音声との単語境界精度、短い相槌や同時発話の品質評価は未実施。検索時のフレーム位置への丸めを含む精度評価は今後行う。手動UIのJSON Importは接続ツールの制約により未検証で、今回は公式UXPを使用した。汎用インポーターの実装・復旧試験はPhase 5で扱う。

以下は実装仕様と途中経過を含む時系列記録。過去の「未検証」「未完了」は当該時点の状態。

## 対象

Premiere Pro 2025。2026-09-07にインストール情報で25.6.6を確認。要件正本は `../REQUIREMENTS.md`。

## 変換

```sh
python3 python/premiere_export.py /absolute/local/master.json /absolute/local/premiere.json
python3 -m unittest discover -s tests -v
```

`bk-master-transcript/v0.1` を入力し、実行ごとに新しいUUID v4を話者に付与する。単語時刻は素材先頭からの秒を保持し、durationはend-start。segment終端は含まれるwordの最大end。重なり発話は消さないが、segment/wordの開始順序は検証する。

未指定confidenceは0（不明時の保守的な出力値。認識確率を測定した値ではない）、eosはfalse、typeはwordとする。内部tagsはmasterに残し、Adobeで許可されたprofanity/fillerだけ出力する。入力masterを変更せず、出力先が既存の場合は拒否する。音声ファイルやPremiereプロジェクトを直接編集しない。

参照した[Adobe公式JSON schema](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/assets/transcript_format_spec.json)に合わせてフィールドを構成した。現在の公式schema参照と25.6.6実機受け入れは別の確認事項。

## 実機手順

1. 2人以上の会話を含むMOV/WAVを確認し、ローカル検証用フォルダへ複製。元と複製のSHA-256を記録する。
2. 独立した検証用プロジェクトを25.6.6で作る。自動文字起こしが動かない状態を確認してから複製素材を読み込む。
3. 素材を聞いて手動masterを作る。話者・文字・時刻の根拠をローカルに記録する。Premiereの文字起こしは実行しない。
4. 変換結果をソース素材のTranscriptにUIからImport。シーケンスStatic Transcriptへの読み込みと混同しない。
5. 話者名・切替・検索結果・単語TCジャンプを確認する。
6. 検証用シーケンスでText-Based Editingの削除・カット＆ペーストを実施し、タイムラインの変化と再生を確認する。
7. 検証用プロジェクトを保存。必要に応じて再起動後の永続化を確認する。素材ハッシュを再確認する。

## 2026-09-07 開発ログ

- 正本ブランチ `feature/premiere-transcript-pipeline` を取得（調査時HEAD `8c2017ffe8cf23a360c87013307a07bbb225b850`）。PR #1はopen。
- READMEに試作済みと記載されていたが、当該ブランチに変換コードはなかったため最小変換を追加。
- 変更: Python変換CLI、単体テスト、生成物除外、README、調査/検証文書。
- 自動テスト4件成功: 新規UUID/時刻/入力保持、異常時刻拒否、未知・重複話者拒否、内部注釈保持。
- ローカルMOV候補は存在するが、2人以上の会話があるかは未確認。開発素材は未確定。
- Premiere 2026は起動のみ。素材読み込み・変更はしていない。その後ユーザー指定で2025へ切り替えた。
- 2025起動を試行したが、画面操作が応答しなくなり、Computer UseがMacロック状態を報告。実機操作は停止。

| 検証項目 | 状態 |
| --- | --- |
| 独自master → JSON変換 | 自動テスト成功 |
| 2人以上の会話素材の確定 | 未確認 |
| Premiere 25へのImport | 未検証 |
| 話者名・話者切替表示 | 未検証 |
| 全文検索・TCジャンプ | 未検証 |
| Text-Based Editing削除 | 未検証 |
| Text-Based Editingカット＆ペースト | 未検証 |
| 保存後の再表示 | 未検証 |
| ローカルWhisperの再実行 | 未検証 |

Phase 1は未完了。Macのロック解除と開発素材確認後に続行する。

### 同日・ロック解除後の追記

- ユーザー指定の外付けSSDから会話場面のMOVを選択。個別パス、素材情報、実データはリポジトリ外のローカル記録に保存。
- 冒頭45秒をstream copyで別MOVへ切り出し、試験用TCをゼロ起点にした。2本の音声streamはMOVに保持し、ASR用にはmixした16 kHz WAVを別生成した。元MOVの変更はしていない。
- Premiere 25.6.6に検証専用プロジェクトを作り、切り出しMOVを読み込み・保存。環境設定の「クリップを自動文字起こし」がオフであることをImport前に確認。ソースのテキストパネルが「ソースから文字起こし」の空状態であることを確認した。
- ローカルMLX Whisperで16 segments / 118 word entriesを生成。手動の仮話者2名を付与したmasterからPremiere JSONを生成し、既存roundtripスキルのvalidatorも通過した。話者名は「話者A（仮）」「話者B（仮）」。仮割当を話者分離の実機検証と扱わない。
- Premiere独自パネルへのマウス操作はComputer Useが `AXError.notImplemented` を返す。キーボードでパネル拡大とメニューボタンへのフォーカスまでは可能だが展開できていない。
- UXP Developer ToolsはPremiere 25.6.6のみを接続ホストとして返した。読み取り専用probeのValidateは成功したがLoadはtimeout。同期対象外の一時フォルダからの再試行でも同じ。probeによるTranscript変更は実施していない。
- 接続復旧のためのPremiere終了は、自動承認レビューが他のプロジェクト・未保存作業への影響を理由に拒否。終了は行わず、ユーザー許可を待つ。

現時点の残件はJSON Import、話者表示、全文検索とTCジャンプ、Text-Based Editingの削除・カット＆ペースト、保存後の再表示。素材ImportとJSON生成だけでPhase 1完了とはしない。


### 同日・ユーザー許可による再起動後

- Premiere 2025を終了し再起動。広いファイルアクセス権限を宣言したUXPプローブは自動承認レビューに拒否されたため、不要なrequiredPermissionsを除去して実行。権限なしのprobeはLoadに成功した。
- 保存済み検証プロジェクトを公式APIで開き、プロジェクトパス・素材名・素材パスを照合。変更前の `.prproj` コピーをローカルに保存した。
- 文字起こしが空の素材への `exportToJSON` は25.6.6で `Illegal Parameter type` を返した。UIで事前に空状態を確認済みの検証素材に限り、プロジェクトバックアップを復旧元としてImportを実行した。この例外を任意素材で無条件に無視する汎用処理にはしていない。
- `importFromJSON` と `createImportTextSegmentsAction` を用い、Action生成とexecuteTransactionを同じlockedAccess内で実行。JSONはplugin内に埋め込み、広いファイルアクセス権限は付与していない。
- Import成功。読み戻しの16区間・118単語について文字と話者参照が一致し、単語開始・durationは1マイクロ秒以内で一致。2話者のID・名前も一致。`imported=true / verified=true / saved=true` を確認。
- 専用シーケンス `Phase1_TBE_Test` を作成・保存。元MOVを直接使わず、45秒の検証用MOVのみを参照。
- 画面確認直前にMacが再びロックされ、Computer Useが手動解除を要求。話者の画面表示、検索、TCジャンプ、Text-Based Editingの実操作は引き続き未検証。Phase 1は未完了。
