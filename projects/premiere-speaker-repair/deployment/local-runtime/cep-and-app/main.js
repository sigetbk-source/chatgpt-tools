(function () {
  var cs = new CSInterface();
  var status = document.getElementById("status");
  var extensionPath = cs.getSystemPath(SystemPath.EXTENSION);
  var fs = require("fs");
  var path = require("path");
  var os = require("os");
  var childProcess = require("child_process");
  var crypto = require("crypto");
  var revisionDir = path.join(extensionPath, "data", "revisions");
  var projectManifestDir = path.join(extensionPath, "data", "project_manifests");
  var bindingsPath = path.join(extensionPath, "data", "premiere_project_bindings.json");
  var placementStatePath = path.join(revisionDir, "placement_state.json");
  var currentExportPath = path.join(extensionPath, "data", "current_narration_export.json");
  var oneTimeApplyPath = path.join(extensionPath, "data", "apply_once.json");
  var runtimeLauncher = path.join(os.homedir(), "Library", "Application Support", "ナレーション制作ツール", "runtime", "launcher.py");
  var localRuntime = path.join(os.homedir(), "Library", "Application Support", "Premiere制作支援");
  var localLauncher = path.join(localRuntime, "narration", "launcher.py");
  if (fs.existsSync(localLauncher)) runtimeLauncher = localLauncher;
  var studioLauncher = require(path.join(extensionPath, "js", "studio-launcher.js"));
  var studioStarting = false;
  var studioLaunchSerial = 0;
  var checkingRevisions = false;
  var activeProjectPath = "";
  var pendingCaseStartedAt = 0;
  var activeManifest = null;

  function jsxString(value) {
    return '"' + String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r/g, "\\r").replace(/\n/g, "\\n") + '"';
  }

  function readJson(filePath, fallback) {
    try { return JSON.parse(fs.readFileSync(filePath, "utf8")); } catch (_) { return fallback; }
  }

  function writeJson(filePath, value) {
    if (!fs.existsSync(revisionDir)) fs.mkdirSync(revisionDir, { recursive: true });
    fs.writeFileSync(filePath, JSON.stringify(value, null, 2), "utf8");
  }

  function bindings() { return readJson(bindingsPath, { schemaVersion: 1, projects: {} }); }

  function normalizedProjectPath(value) {
    var result = String(value || "").replace(/\\\\/g, "/").replace(/\/$/, "");
    try { return result.normalize("NFC"); } catch (_) { return result; }
  }

  function isCurrentProjectManifest(manifest) {
    var binding = manifest && manifest.premiereBinding;
    return !!(binding && binding.projectPath &&
      normalizedProjectPath(binding.projectPath) === normalizedProjectPath(activeProjectPath));
  }

  // ブラウザ単独で承認した場合は、書き出しデータにPremiereプロジェクトの
  // パスが含まれない。その場合でも、承認ジョブと開いているプロジェクトが
  // 同じ案件フォルダ内にある時だけ安全に紐付ける。
  function isManifestForActiveProjectCase(manifest) {
    if (!manifest || !manifest.jobPath || !activeProjectPath) return false;
    var jobPath = normalizedProjectPath(manifest.jobPath);
    var marker = "/.premiere-tools/jobs/";
    var index = jobPath.indexOf(marker);
    if (index < 1) return false;
    var casePath = jobPath.slice(0, index);
    var projectPath = normalizedProjectPath(activeProjectPath);
    return projectPath === casePath || projectPath.indexOf(casePath + "/") === 0;
  }

  function bindLocalManifest(manifest) {
    var data = bindings();
    var current = data.projects[activeProjectPath];
    if (current && current.revision === (manifest.revision || "") &&
        current.manifestPath && fs.existsSync(current.manifestPath)) {
      return;
    }
    if (!fs.existsSync(projectManifestDir)) fs.mkdirSync(projectManifestDir, { recursive: true });
    var projectId = crypto.createHash("sha256").update(normalizedProjectPath(activeProjectPath), "utf8").digest("hex").slice(0, 16);
    var revision = String(manifest.revision || Date.now()).replace(/[^A-Za-z0-9._-]/g, "_");
    var localManifestPath = path.join(projectManifestDir, projectId + "_" + revision + ".json");
    fs.writeFileSync(localManifestPath, JSON.stringify(manifest, null, 2), "utf8");
    data.projects[activeProjectPath] = {
      manifestPath: localManifestPath,
      revision: manifest.revision || "",
      boundAt: new Date().toISOString()
    };
    writeJson(bindingsPath, data);
  }

  function setApplyEnabled(enabled) { document.getElementById("applyButton").disabled = !enabled; }

  function clearManifest(message) {
    activeManifest = null;
    document.getElementById("caseName").textContent = "案件データは未承認です";
    document.getElementById("caseDetail").textContent = message;
    setApplyEnabled(false);
  }

  function showManifest(manifest, showStatus) {
    activeManifest = manifest;
    document.getElementById("caseName").textContent = manifest.caseName || "案件名不明";
    document.getElementById("caseDetail").textContent = "最終承認: " + (manifest.updatedAt || "日時不明") + " ／ " + (manifest.mode === "audio" ? "音声＋テロップ" : "テロップのみ");
    setApplyEnabled(true);
    if (showStatus) status.textContent = "このPremiereプロジェクト用の承認済みデータです。反映できます。";
    return manifest;
  }

  function currentNarrationExport() {
    var manifest = readJson(currentExportPath, null);
    return manifest && manifest.schemaVersion >= 1 ? manifest : null;
  }

  function refreshManifest(showStatus) {
    if (!activeProjectPath) {
      clearManifest("先にPremiereプロジェクトを保存してから、制作画面を開いてください。");
      return null;
    }
    var latest = currentNarrationExport();
    if (pendingCaseStartedAt) {
      var latestTime = latest && Date.parse(latest.updatedAt || "");
      if (latest && latestTime && latestTime >= pendingCaseStartedAt - 1000) {
        bindLocalManifest(latest);
        pendingCaseStartedAt = 0;
        return showManifest(latest, showStatus);
      }
      clearManifest("台本を読み込み、ブラウザで内容を承認すると、この案件名と反映ボタンが有効になります。");
      return null;
    }
    // UXP/PythonがSRTを同期すると共用ポインターだけが先に更新される。
    // 開いているPremiereプロジェクト用と確認できた場合に限り、最新データへ追従する。
    if (latest && (isCurrentProjectManifest(latest) || isManifestForActiveProjectCase(latest))) {
      bindLocalManifest(latest);
      return showManifest(latest, showStatus);
    }
    var binding = bindings().projects[activeProjectPath];
    var manifest = binding && binding.manifestPath ? readJson(binding.manifestPath, null) : null;
    if (!manifest || !manifest.schemaVersion) {
      clearManifest("このPremiereプロジェクトでは、まだナレーションを制作・承認していません。");
      if (showStatus) status.textContent = "先に「制作画面を開く」から台本を読み込んでください。";
      return null;
    }
    return showManifest(manifest, showStatus);
  }

  function targetFile(manifest, key, label) {
    var filePath = manifest && manifest[key];
    if (!filePath) { status.textContent = "エラー: " + label + "がありません。"; return null; }
    if (!fs.existsSync(filePath)) { status.textContent = "エラー: " + label + "が見つかりません。制作画面で再度書き出してください。"; return null; }
    return filePath;
  }

  function createCaption(manifest) {
    var srtPath = targetFile(manifest, "srtPath", "テロップSRT");
    if (!srtPath) return;
    status.textContent = "キャプションを作成中…";
    cs.evalScript("ntCreateCaption(" + jsxString(srtPath) + ")", function (result) {
      status.textContent = result;
      if (String(result).indexOf("完了:") === 0) markJobApplied(manifest, result);
    });
  }

  function markJobApplied(manifest, message) {
    if (!manifest || !manifest.jobPath) return;
    var statusPath = path.join(manifest.jobPath, "status.json");
    var job = readJson(statusPath, null);
    if (!job) return;
    job.state = "applied";
    job.message = "音声修正とSRTの貼り直しが完了しました。";
    job.appliedAt = new Date().toISOString();
    job.captionState = "completed";
    job.captionSrtPath = manifest.srtPath || "";
    job.captionAppliedAt = job.appliedAt;
    job.updatedAt = job.appliedAt;
    job.applyDetail = String(message || "");
    try { fs.writeFileSync(statusPath, JSON.stringify(job, null, 2), "utf8"); } catch (_) {}
  }

  function recordNarrationPlacement(manifest, trackNumber) {
    if (!manifest || !manifest.jobPath || !manifest.audioItems) return;
    var statusPath = path.join(manifest.jobPath, "status.json");
    var job = readJson(statusPath, null);
    if (!job) return;
    // 差分更新では表示名でなく、初回配置時の実メディアパスを照合する。
    job.premiereBinding = {
      schemaVersion: 1,
      projectPath: activeProjectPath,
      audioTrack: trackNumber,
      items: manifest.audioItems.map(function (item, index) {
        return { id: item.id, mediaPath: item.audioPath, order: index };
      }),
      recordedAt: new Date().toISOString()
    };
    job.updatedAt = new Date().toISOString();
    try { fs.writeFileSync(statusPath, JSON.stringify(job, null, 2), "utf8"); } catch (_) {}
  }

  function requestNarrationTranscripts(manifest) {
    if (!manifest || !manifest.jobPath) {
      status.textContent = "エラー: この書き出しは自動文字起こし登録に対応していません。制作画面で承認し直してください。";
      return false;
    }
    var statusPath = path.join(manifest.jobPath, "status.json");
    var job = readJson(statusPath, null);
    if (!job) {
      status.textContent = "エラー: ナレーションジョブの状態を読めません。";
      return false;
    }
    // 保存・読み戻しまで完了した同じ書き出しは、再度登録待ちに戻さない。
    if (job.transcriptState === "completed" && (job.transcriptRevision === manifest.revision || (!job.transcriptRevision && job.revision === manifest.revision && job.transcriptCompletedAt))) return true;
    job.transcriptState = "requested";
    job.transcriptRequestedAt = new Date().toISOString();
    job.transcriptError = "";
    job.message = "個別WAVへの文字起こし登録を待っています。";
    job.updatedAt = job.transcriptRequestedAt;
    try { fs.writeFileSync(statusPath, JSON.stringify(job, null, 2), "utf8"); }
    catch (error) { status.textContent = "エラー: 文字起こし登録を依頼できません: " + error.message; return false; }
    return true;
  }

  function waitForNarrationTranscripts(manifest, onComplete) {
    var statusPath = path.join(manifest.jobPath, "status.json");
    var startedAt = 0;
    var poll = window.setInterval(function () {
      var job = readJson(statusPath, null);
      if (job && job.transcriptState === "completed") {
        window.clearInterval(poll);
        onComplete();
        return;
      }
      if (job && job.transcriptState === "failed") {
        window.clearInterval(poll);
        status.textContent = "文字起こし登録を中止しました: " + (job.transcriptError || "不明なエラー");
        return;
      }
      if (Date.now() - startedAt > 120000) {
        window.clearInterval(poll);
        status.textContent = "文字起こし登録の完了を確認できません。UXPの「文字起こし修正」パネルを開いたまま、Premiereプロジェクトを保存してからもう一度実行してください。";
      }
    }, 500);
  }

  function formatSrtMilliseconds(ms) {
    var hours = Math.floor(ms / 3600000); ms -= hours * 3600000;
    var mins = Math.floor(ms / 60000); ms -= mins * 60000;
    var secs = Math.floor(ms / 1000); ms -= secs * 1000;
    function pad(n, width) { var s = String(n); while (s.length < width) s = "0" + s; return s; }
    return pad(hours, 2) + ":" + pad(mins, 2) + ":" + pad(secs, 2) + "," + pad(ms, 3);
  }

  function makeAlignedSrt(items, timing) {
    if (!timing || !timing.items || timing.items.length !== items.length || !(timing.frameSeconds > 0 && timing.frameSeconds < 1)) throw new Error("音声位置が一致しません");
    var blocks = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i], placed = timing.items[i];
      if (item.id !== placed.id || !(placed.endSeconds > placed.startSeconds)) throw new Error("音声の順番または時間が一致しません: " + item.id);
      // 実際の音声の開始・終了位置を使う。SRTのミリ秒表現が
      // フレーム境界を下回らないよう切り上げ、余分なフレームは加えない。
      var startMs = Math.ceil(placed.startSeconds * 1000 - 0.000001);
      var endMs = Math.ceil(placed.endSeconds * 1000 - 0.000001);
      blocks.push((i + 1) + "\n" + formatSrtMilliseconds(startMs) + " --> " + formatSrtMilliseconds(endMs) + "\nN　" + item.captionText + "\n");
    }
    return blocks.join("\n") + "\n";
  }

  function syncCaptionToPlacedAudio(manifest, trackNumber) {
    cs.evalScript("ntGetNarrationTimings(" + jsxString(JSON.stringify(manifest.audioItems)) + "," + (trackNumber - 1) + ")", function (raw) {
      try {
        if (String(raw).indexOf("エラー:") === 0) throw new Error(raw);
        var timing = JSON.parse(raw), original = targetFile(manifest, "srtPath", "テロップSRT");
        if (!original) return;
        var aligned = original.replace(/\.srt$/i, "") + "_audio-aligned_" + Date.now() + ".srt";
        fs.writeFileSync(aligned, makeAlignedSrt(manifest.audioItems, timing), "utf8");
        var synced = Object.assign({}, manifest, {srtPath: aligned});
        createCaption(synced);
      } catch (e) { status.textContent = "字幕の同期を中止しました: " + e.message; }
    });
  }

  function placeImportedAudioAndCreateCaption(manifest, trackNumber) {
    status.textContent = "文字起こしを確認しました。音声をタイムラインへ配置中…";
    cs.evalScript("ntPlaceImportedNarrationAudioItems(" + jsxString(JSON.stringify(manifest.audioItems)) + "," + (trackNumber - 1) + ")", function (result) {
      if (String(result).indexOf("完了:") !== 0) { status.textContent = result; return; }
      recordNarrationPlacement(manifest, trackNumber);
      syncCaptionToPlacedAudio(manifest, trackNumber);
    });
  }

  function applyLatest() {
    var manifest = refreshManifest(false);
    if (!manifest) { status.textContent = "制作画面で書き出しを完了してください。"; return; }
    if (manifest.mode !== "audio") { createCaption(manifest); return; }
    var trackNumber = parseInt(document.getElementById("audioTrack").value, 10);
    if (!trackNumber || trackNumber < 1) { status.textContent = "音声トラック番号を確認してください。"; return; }
    if (manifest.audioItems && manifest.audioItems.length) {
      status.textContent = "個別音声をビンへ一括で読み込み中…";
      cs.evalScript("ntImportNarrationAudioItems(" + jsxString(JSON.stringify(manifest.audioItems)) + "," + jsxString(manifest.audioBinName || "個別ナレーション音声") + ")", function (result) {
        if (String(result).indexOf("完了:") !== 0) { status.textContent = result; return; }
        if (!requestNarrationTranscripts(manifest)) return;
        status.textContent = "個別WAVへ本文を文字起こしとして登録中…";
        waitForNarrationTranscripts(manifest, function () { placeImportedAudioAndCreateCaption(manifest, trackNumber); });
      });
      return;
    }
    var audioPath = targetFile(manifest, "audioPath", "仮ナレーション音声");
    if (!audioPath) return;
    status.textContent = "音声を配置中…";
    cs.evalScript("ntPlaceNarrationAudio(" + jsxString(audioPath) + "," + (trackNumber - 1) + ")", function (result) {
      if (String(result).indexOf("完了:") !== 0) { status.textContent = result; return; }
      createCaption(manifest);
    });
  }

  function adoptCurrentTimeline() {
    var manifest = refreshManifest(false);
    if (!manifest || !manifest.jobPath || !manifest.audioItems || !manifest.audioItems.length) { status.textContent = "承認済みの個別音声データが必要です。"; return; }
    var trackNumber = parseInt(document.getElementById("audioTrack").value, 10);
    if (!trackNumber) { status.textContent = "音声トラック番号を確認してください。"; return; }
    status.textContent = "現在のAトラックを照合中…";
    cs.evalScript("ntGetAudioTrackMediaPaths(" + (trackNumber - 1) + ")", function (raw) {
      if (String(raw).indexOf("エラー:") === 0) { status.textContent = raw; return; }
      var data; try { data = JSON.parse(raw); } catch (_) { status.textContent = "エラー: タイムライン照合結果を読めません。"; return; }
      var expected = manifest.audioItems.map(function (item) { return item.audioPath; });
      if (expected.length !== data.mediaPaths.length || expected.some(function (item) { return data.mediaPaths.indexOf(item) < 0; })) {
        status.textContent = "登録しませんでした。指定トラックのWAVが承認済みデータと一致しません。昨日のナレーションだけが並ぶトラックを指定してください。";
        return;
      }
      recordNarrationPlacement(manifest, trackNumber);
      status.textContent = "現在のタイムラインを差分更新対象として登録しました。クリップやキャプションは変更していません。";
    });
  }

  function openStudio() {
    if (studioStarting) return;
    if (!fs.existsSync(runtimeLauncher)) {
      status.textContent = "エラー: 制作ランタイムが見つかりません。ナレーション制作ツールを再インストールしてください。";
      return;
    }
    studioStarting = true;
    var launchSerial = ++studioLaunchSerial;
    var button = document.getElementById("openStudioButton");
    button.disabled = true;
    status.textContent = "制作ツールの起動環境を確認中…";
    var previousManifest = activeManifest;
    var startedAt = Date.now();
    function finishStart() {
      if (studioLaunchSerial !== launchSerial) return;
      studioStarting = false; button.disabled = false;
    }
    function resetPending() {
      // An older studio closing must not cancel a later studio's approval.
      if (pendingCaseStartedAt === startedAt) {
        pendingCaseStartedAt = 0;
        if (previousManifest) showManifest(previousManifest, false);
        else clearManifest("案件フォルダと台本を選んで制作を開始してください。");
      }
    }
    studioLauncher.launchStudio({
      childProcess: childProcess,
      launcher: runtimeLauncher,
      candidates: [
        path.join(localRuntime, "python", "bin", "python3"),
        path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "bin", "python3"),
        "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"
      ],
      onStarted: function () {
        if (studioLaunchSerial !== launchSerial) return;
        startedAt = Date.now();
        pendingCaseStartedAt = startedAt;
        clearManifest("台本を読み込み中です。ブラウザで承認するまで反映できません。");
        status.textContent = "案件フォルダの選択を起動しています。選択後、ブラウザ制作画面が表示されます。";
        window.setTimeout(finishStart, 1500);
      },
      onError: function (message) {
        if (studioLaunchSerial !== launchSerial) return;
        finishStart(); resetPending();
        status.textContent = message;
      },
      onClosed: function () {
        if (studioLaunchSerial !== launchSerial) return;
        finishStart();
        if (pendingCaseStartedAt === startedAt) {
          resetPending();
          status.textContent = "制作画面の起動を終了しました。必要ならもう一度開いてください。";
        }
      }
    });
  }

  function checkRevisionQueue(showEmptyMessage) {
    if (checkingRevisions) return;
    var queue = readJson(path.join(revisionDir, "queue.json"), { batches: [] });
    var placement = readJson(placementStatePath, { schemaVersion: 1, placedBatches: [] });
    var placed = placement.placedBatches || [];
    var pending = (queue.batches || []).filter(function (name) { return placed.indexOf(name) < 0; });
    if (!pending.length) { if (showEmptyMessage) status.textContent = "追加・修正の未配置分はありません。"; return; }
    var batchName = pending[0];
    var manifestPath = path.join(revisionDir, batchName);
    var manifestText;
    try { manifestText = fs.readFileSync(manifestPath, "utf8"); } catch (error) { status.textContent = "追加・修正データを読めません: " + error.message; return; }
    checkingRevisions = true;
    status.textContent = "追加・修正を専用シーケンスへ配置中…";
    cs.evalScript("ntAppendRevisionBatch(" + jsxString(manifestText) + ")", function (result) {
      checkingRevisions = false;
      status.textContent = result;
      if (String(result).indexOf("完了:") === 0) {
        placed.push(batchName); placement.placedBatches = placed; placement.lastPlacedAt = new Date().toISOString(); writeJson(placementStatePath, placement);
        window.setTimeout(function () { checkRevisionQueue(false); }, 400);
      }
    });
  }

  document.getElementById("openStudioButton").onclick = openStudio;
  document.getElementById("refreshButton").onclick = function () { refreshManifest(true); };
  document.getElementById("applyButton").onclick = applyLatest;
  document.getElementById("adoptButton").onclick = adoptCurrentTimeline;
  document.getElementById("diffCaptionButton").onclick = function () {
    var manifest = refreshManifest(false);
    if (!manifest) { status.textContent = "差分更新後のデータがありません。"; return; }
    createCaption(manifest);
  };
  document.getElementById("revisionButton").onclick = function () { checkRevisionQueue(true); };
  cs.evalScript("ntGetActiveProjectPath()", function (result) {
    activeProjectPath = String(result || "");
    refreshManifest(false);
    var oneTime = readJson(oneTimeApplyPath, null);
    if (oneTime && oneTime.projectPath === activeProjectPath && activeManifest &&
        oneTime.revision === activeManifest.revision) {
      cs.evalScript("(app.project.activeSequence ? app.project.activeSequence.name : '')", function (sequenceName) {
        if (String(sequenceName) !== oneTime.sequenceName) {
          status.textContent = "自動反映を中止しました。指定シーケンスを開いてください。";
          return;
        }
        if (typeof oneTime.audioTrack !== "undefined") {
          var requestedTrack = Number(oneTime.audioTrack);
          if (!isFinite(requestedTrack) || requestedTrack < 1 || Math.floor(requestedTrack) !== requestedTrack) {
            status.textContent = "自動反映を中止しました。音声トラック番号が不正です。";
            return;
          }
          document.getElementById("audioTrack").value = String(requestedTrack);
        }
        try { fs.renameSync(oneTimeApplyPath, oneTimeApplyPath + ".consumed"); }
        catch (error) { status.textContent = "自動反映を中止しました: " + error.message; return; }
        if (oneTime.captionOnly) {
          syncCaptionToPlacedAudio(activeManifest, Number(document.getElementById("audioTrack").value));
        } else {
          applyLatest();
        }
      });
    }
  });
  window.setInterval(function () { refreshManifest(false); }, 2500);
}());
