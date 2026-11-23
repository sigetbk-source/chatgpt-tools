"use strict";

const storage = require("uxp").storage;
const fs = storage.localFileSystem;
const engine = require("./engine.js");
const placement = require("./placement.js");
const ppro = require("premierepro");
const bridgeTokenKey = "sequence-srt-caption-bridge";

const fields = [
  ["includeNarration", "ナレーションを含める"],
  ["maxChars", "1行の最大文字数"],
  ["maxLines", "1枚の最大行数（1〜2）"],
  ["periodThreshold", "句点の手前で空白結合できる文字数"],
  ["periodCompactLimit", "空白結合後の文字数の例外（0＝無効）"],
  ["joinShortPeriod", "句点での空白結合を有効にする"],
  ["joinComma", "読点を空白で結合"],
  ["maxSpaces", "1行の半角スペース上限"],
  ["breakQuestions", "疑問符・感嘆符で改行"],
  ["joinSameSpeaker", "同じ話者の隣接区間を結合"],
  ["silenceMs", "元区間をまたぐ無音分割（ms）"],
  ["sameSegmentSilenceMs", "同じ元区間で結合する無音の上限（ms）"],
  ["correctionMs", "軽微な時刻補正の許容幅（ms）"],
  ["detectOverlap", "時刻の重複を報告"],
  ["minCueMs", "短時間の確認目安（ms）"],
  ["maxCueMs", "最長表示の目安（ms、0＝無効）"],
  ["cueGapMs", "字幕間隔の目安（ms、0＝無効）"],
];

document.addEventListener("DOMContentLoaded", () => {
  const element = id => document.getElementById(id);
  const settings = Object.create(null);
  const pageSize = 12;
  let source;
  let result;
  let page = 0;
  let busy = false;
  const submittedTargets = new Set();
  const targetKey = target => JSON.stringify([target.projectPath, target.sequenceGuid]);

  function refreshButtons() {
    element("open").disabled = busy;
    element("generate").disabled = busy || !source;
    element("save").disabled = busy || !result || !result.cues.length;
    element("bridge").disabled = busy;
    element("place").disabled = busy || !result?.cues.length || !source?.target || submittedTargets.has(targetKey(source.target));
    element("save-report").disabled = busy || !result;
    element("save-speakers").disabled = busy || !result || !result.bySpeaker.length;
    element("previous").disabled = !result || page === 0;
    element("next").disabled = !result || (page + 1) * pageSize >= result.issues.length;
  }

  function rows(id, values, className) {
    const container = element(id);
    container.textContent = "";
    for (const value of values) {
      const row = document.createElement("div");
      row.className = className;
      row.textContent = value;
      container.appendChild(row);
    }
  }

  function showReview() {
    const issues = result ? result.issues : [];
    const first = page * pageSize;
    const shown = issues.slice(first, first + pageSize);
    const title = result && issues.length ? "要確認 " + (first + 1) + "–" + (first + shown.length) + " / " + issues.length : "";
    rows("review", title ? [title, ...shown.map(engine.issueText)] : [], "issue");
    refreshButtons();
  }

  function invalidate() {
    result = null;
    page = 0;
    element("result").textContent = "";
    rows("subtitle-preview", [], "sample");
    showReview();
  }

  function addAction(id, action) {
    element(id).addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      refreshButtons();
      try {
        await action();
      } catch (error) {
        element("status").textContent = error.message || String(error);
      } finally {
        busy = false;
        refreshButtons();
      }
    });
  }

  for (const [key, label] of fields) {
    const isBoolean = typeof engine.defaults[key] === "boolean";
    const control = document.createElement(isBoolean ? "sp-checkbox" : "sp-textfield");
    settings[key] = control;
    if (isBoolean) {
      control.textContent = label;
      control.checked = engine.defaults[key];
      element("settings").appendChild(control);
    } else {
      const row = document.createElement("div");
      row.className = "setting";
      const title = document.createElement("span");
      title.className = "setting-label";
      title.textContent = label;
      control.setAttribute("aria-label", label);
      control.value = String(engine.defaults[key]);
      row.appendChild(title);
      row.appendChild(control);
      element("settings").appendChild(row);
    }
    control.addEventListener(isBoolean ? "change" : "input", () => {
      if (!source) return;
      invalidate();
      element("status").textContent = "設定を変更しました。再生成してください。";
    });
  }

  element("show-settings").addEventListener("click", () => {
    const section = element("settings-section");
    const show = section.style.display === "none";
    section.style.display = show ? "block" : "none";
    element("show-settings").textContent = show ? "設定を閉じる" : "設定を開く";
  });
  element("reset").addEventListener("click", () => {
    for (const [key, control] of Object.entries(settings)) {
      const value = engine.defaults[key];
      if (typeof value === "boolean") control.checked = value;
      else control.value = String(value);
    }
    invalidate();
    if (source) element("status").textContent = "初期値に戻しました。再生成してください。";
  });

  addAction("open", async () => {
    const file = await fs.getFileForOpening({ types: ["json"] });
    if (!file) return;
    const data = engine.parseJson(await file.read({ format: storage.formats.utf8 }));
    if (!Array.isArray(data?.segments)) throw new Error("Premiereの文字起こしJSONを選択してください");
    const target = await placement.readTarget(ppro).catch(() => null);
    source = { data, path: file.nativePath, stem: engine.outputStem(data, file.name), target };
    invalidate();
    element("file-info").textContent = file.name + "｜" + data.segments.length + "区間";
    element("placement-target").textContent = target ? "配置先: " + target.sequenceName
      : "配置するには対象シークエンスを開いてからJSONを選んでください。";
    const previews = data.segments.slice(0, 3).map(segment => {
      const text = Array.isArray(segment?.words) ? segment.words.map(word => word?.text ?? "").join("") : segment?.text ?? "";
      const name = Array.isArray(data.speakers) ? data.speakers.find(s => s?.id === segment?.speaker)?.name : "";
      return (name ? name + "｜" : "") + text.slice(0, 180);
    });
    rows("source-preview", previews, "sample");
    element("status").textContent = "JSONを読み込みました。";
  });

  addAction("generate", async () => {
    const options = {};
    for (const [key, label] of fields) {
      const control = settings[key];
      if (typeof engine.defaults[key] === "boolean") options[key] = Boolean(control.checked);
      else {
        if (!/^\d+$/.test(control.value.trim())) throw new Error(label + "は整数で入力してください。");
        options[key] = Number(control.value);
      }
    }
    invalidate();
    result = engine.generate(source.data, options);
    element("result").textContent = result.stats.cues + "枚｜話者ID " + result.stats.speakers + "件｜要確認 " + result.issues.length + "件";
    rows("subtitle-preview", result.cues.slice(0, 3).map(cue =>
      engine.timecode(cue.start) + " – " + engine.timecode(cue.end) + "\n" + cue.lines.join("\n")), "sample");
    element("status").textContent = result.cues.length ? "生成しました。" : "保存できる字幕がありません。";
    showReview();
  });

  async function save(suffix, extension, contents) {
    const file = await fs.getFileForSaving(source.stem + suffix, { types: [extension] });
    if (!file) return;
    const normalize = path => path.normalize().replace(/\\/g, "/").toLowerCase();
    if (source.path && file.nativePath && normalize(source.path) === normalize(file.nativePath)) {
      throw new Error("元の文字起こしJSONと別の名前で保存してください。");
    }
    await file.write(contents, { format: storage.formats.utf8 });
    element("status").textContent = file.name + " を保存しました。";
  }

  addAction("save", () => save(".srt", "srt", result.srt));
  addAction("bridge", async () => {
    const root = await fs.getFolder();
    if (!root) return;
    await placement.readBridge(root);
    localStorage.setItem(bridgeTokenKey, await fs.createPersistentToken(root));
    element("status").textContent = "CEP連携フォルダを設定しました。";
  });

  addAction("place", async () => {
    const { target, stem } = source;
    const generated = result;
    const token = localStorage.getItem(bridgeTokenKey);
    if (!token) throw new Error("先に「CEP連携フォルダを選ぶ」で待受フォルダを設定してください。");
    const root = await fs.getEntryForPersistentToken(token).catch(() => {
      throw new Error("CEP連携フォルダを開けません。もう一度選んでください。");
    });
    element("status").textContent = target.sequenceName + " に字幕を配置しています…";
    const placed = await placement.placeCaptions({ ppro, target, root, result: generated, stem, format: storage.formats.utf8,
      markSubmitted: () => submittedTargets.add(targetKey(target)) });
    element("status").textContent = placed.sequenceName + " に字幕トラックを配置・保存しました。";
  });
  addAction("save-report", () => save(".review.json", "json", engine.report(result)));
  addAction("save-speakers", async () => {
    const groups = result.bySpeaker;
    const stem = source.stem;
    const folder = await fs.getFolder();
    if (!folder) return;
    let written = 0;
    for (const [index, speaker] of groups.entries()) {
      const name = engine.outputStem({}, speaker.name);
      const filename = stem + ".speaker-" + (index + 1) + "-" + name + ".srt";
      const file = await folder.createFile(filename, { overwrite: false });
      await file.write(engine.renderSrt(speaker.cues), { format: storage.formats.utf8 });
      written++;
      element("status").textContent = "話者別SRT " + written + " / " + groups.length;
    }
  });
  element("previous").addEventListener("click", () => { page--; showReview(); });
  element("next").addEventListener("click", () => { page++; showReview(); });
  refreshButtons();
});
