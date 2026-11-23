"use strict";

const bridgeFolders = ["caption-inbox", "caption-outbox", "caption-claims", "caption-backups"];

async function readTarget(ppro) {
  const project = await ppro.Project.getActiveProject();
  if (!project?.path) throw new Error("保存済みのPremiereプロジェクトを開いてください。");
  const sequence = await project.getActiveSequence();
  if (!sequence?.guid) throw new Error("対象のシークエンスをタイムラインで開いてください。");
  return { project, sequence, projectPath: project.path, sequenceGuid: String(sequence.guid), sequenceName: sequence.name };
}

function sameTarget(left, right) {
  return left.projectPath === right.projectPath && left.sequenceGuid === right.sequenceGuid
    && left.sequenceName === right.sequenceName;
}

async function readBridge(root) {
  const entries = await root.getEntries();
  if (!bridgeFolders.every(name => entries.some(entry => entry.name === name && entry.isFolder))) {
    throw new Error("CEPの「Transcript Caption Placement」に表示される待受フォルダを選んでください。");
  }
  return Object.fromEntries(entries.map(entry => [entry.name, entry]));
}

function requestId() {
  return Array.from({ length: 4 }, () =>
    Math.floor(Math.random() * 0x100000000).toString(16).padStart(8, "0")).join("");
}

async function placeCaptions({ ppro, target, root, result, stem, format,
  now = Date.now, delay = ms => new Promise(resolve => setTimeout(resolve, ms)),
  nonce = requestId(), markSubmitted = () => {}, markRejected = () => {} }) {
  if (!result?.cues?.length) throw new Error("配置する字幕がありません。");
  if (!sameTarget(target, await readTarget(ppro))) {
    throw new Error("JSONを読み込んだときのシークエンスに戻してください。");
  }
  const expectedCaptionTrackCount = await target.sequence.getCaptionTrackCount();
  if (!Number.isInteger(expectedCaptionTrackCount) || expectedCaptionTrackCount < 0) {
    throw new Error("配置先の字幕トラック数を確認できませんでした。");
  }
  const duration = (await target.sequence.getEndTime()).seconds;
  if (!Number.isFinite(duration) || result.cues.some(cue => cue.end > duration * 1000 + 50)) {
    throw new Error("字幕の終了時刻が対象シークエンスの尺を超えています。JSONの書き出し元を確認してください。");
  }

  const bridge = await readBridge(root);
  const assets = bridge["sequence-srt-assets"] || await root.createFolder("sequence-srt-assets");
  const file = await assets.createFile(stem.slice(0, 30) + "-" + nonce + ".srt", { overwrite: false });
  await file.write(result.srt, { format });
  if (await file.read({ format }) !== result.srt) throw new Error("生成したSRTを照合できませんでした。");

  const bin = await target.project.getRootItem();
  if (!sameTarget(target, await readTarget(ppro))) throw new Error("配置対象のシークエンスが切り替わりました。");
  const imported = await target.project.importFiles([file.nativePath], true, bin, false);
  if (!imported) throw new Error("PremiereがSRTの読み込みを受け付けませんでした。");
  if (!sameTarget(target, await readTarget(ppro))) {
    throw new Error("SRT読み込み後に対象が切り替わりました。プロジェクトパネルで読み込まれたSRTを確認してください。");
  }
  if (await target.sequence.getCaptionTrackCount() !== expectedCaptionTrackCount) {
    throw new Error("字幕トラック数が途中で変わりました。タイムラインを確認してから再試行してください。");
  }

  const deadlineMs = now() + 20000;
  const request = {
    version: 1, nonce, operation: "place_srt", deadlineMs,
    payload: {
      projectPath: target.projectPath, sequenceGuid: target.sequenceGuid, sequenceName: target.sequenceName,
      srtPath: file.nativePath, expectedText: result.srt, expectedCaptionTrackCount, sourceTimeVerified: true,
    },
  };
  const inbox = bridge["caption-inbox"];
  const outbox = bridge["caption-outbox"];
  const pending = await inbox.createFile(nonce + ".tmp", { overwrite: false });
  await pending.write(JSON.stringify(request), { format });
  await pending.moveTo(inbox, { newName: nonce + ".json", overwrite: false });
  markSubmitted();

  while (now() < deadlineMs + 2000) {
    const responseFile = (await outbox.getEntries()).find(entry => entry.name === nonce + ".json" && entry.isFile);
    if (responseFile) {
      const response = JSON.parse(await responseFile.read({ format }));
      if (response.nonce !== nonce) throw new Error("CEPの応答を照合できませんでした。タイムラインを確認してください。");
      if (!response.ok) {
        if (response.result?.needsManualRecovery) {
          throw new Error("CEPで配置できませんでした。タイムラインを確認してください。 " + response.error);
        }
        markRejected();
        if (/^SyntaxError: Unexpected token E in JSON/.test(response.error)) {
          throw new Error("CEPがPremiereの配置スクリプトを実行できませんでした。配置開始前のエラーです。");
        }
        if (expectedCaptionTrackCount > 0 && /Incomplete caption target/.test(response.error)) {
          throw new Error("既存字幕があるシークエンスに追加するには、install.command でCEPを更新し、Premiereを再起動してください。");
        }
        throw new Error("CEPは配置を開始できませんでした。 " + response.error);
      }
      if (!response.result?.created || !response.result?.saved || response.result.sequenceGuid !== target.sequenceGuid
        || response.result.projectPath !== target.projectPath || await target.sequence.getCaptionTrackCount() !== expectedCaptionTrackCount + 1) {
        throw new Error("字幕トラックの配置結果を照合できませんでした。タイムラインを確認してください。");
      }
      return { sequenceName: target.sequenceName, srtPath: file.nativePath, captionTrackCount: expectedCaptionTrackCount + 1 };
    }
    await delay(250);
  }
  throw new Error("CEPの応答がありません。CEPパネルの待受とタイムラインを確認してください。");
}

module.exports = { readTarget, readBridge, placeCaptions };
