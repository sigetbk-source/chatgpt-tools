"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { generate } = require("../plugin/engine.js");
const { placeCaptions, readTarget } = require("../plugin/placement.js");

const input = {
  speakers: [{ id: "a", name: "出演者" }, { id: "n", name: "ＮＡ " }],
  segments: [
    { speaker: "a", words: [{ text: "話します。", start: 1, duration: 1 }] },
    { speaker: "n", words: [{ text: "案内します。", start: 2, duration: 1 }] },
  ],
};
const nonce = "a".repeat(32);

function fixture(initialCount = 0) {
  let request, imported, count = initialCount, time = 0;
  const file = (name, text) => ({
    isFile: true, name, nativePath: "/ipc/sequence-srt-assets/" + name,
    async read() { return text; },
    async write(value) { text = value; },
    async moveTo(_, { newName }) {
      assert.equal(newName, nonce + ".json");
      request = JSON.parse(text);
      assert.equal(request.payload.srtPath, imported);
      count++;
    },
  });
  const inbox = { name: "caption-inbox", isFolder: true, async createFile(name) { return file(name); } };
  const outbox = { name: "caption-outbox", isFolder: true, async getEntries() {
    if (!request) return [];
    return [file(nonce + ".json", JSON.stringify({ nonce, ok: true, result: {
      created: true, saved: true, projectPath: request.payload.projectPath, sequenceGuid: request.payload.sequenceGuid,
    } }))];
  } };
  const root = {
    async getEntries() { return [inbox, outbox, ...["caption-claims", "caption-backups"].map(name => ({ name, isFolder: true }))]; },
    async createFolder(name) {
      assert.equal(name, "sequence-srt-assets");
      return { async createFile(name) { return file(name); } };
    },
  };
  const sequence = {
    name: "編集テスト", guid: "seq-1",
    async getCaptionTrackCount() { return count; },
    async getEndTime() { return { seconds: 3 }; },
  };
  const project = {
    path: "/project/edit.prproj",
    async getActiveSequence() { return sequence; },
    async getRootItem() { return {}; },
    async importFiles(paths) { [imported] = paths; return true; },
  };
  const ppro = { Project: { async getActiveProject() { return project; } } };
  return { ppro, project, root, sequence, outbox, now: () => time, delay: async ms => { time += ms; },
    getRequest: () => request, getImported: () => imported };
}

for (const includeNarration of [true, false]) {
  test("JSON読込からCEPへ配置依頼する（ナレーション" + (includeNarration ? "あり" : "なし") + "）", async () => {
    const existingCount = includeNarration ? 1 : 0;
    const setup = fixture(existingCount);
    const result = generate(JSON.parse(JSON.stringify(input)), { includeNarration });
    const target = await readTarget(setup.ppro);
    const placed = await placeCaptions({ ...setup, target, result, stem: "字幕", nonce });
    const request = setup.getRequest();
    assert.equal(placed.sequenceName, target.sequenceName);
    assert.equal(request.operation, "place_srt");
    assert.equal(request.payload.expectedText, result.srt);
    assert.equal(request.payload.expectedText.includes("案内します。"), includeNarration);
    assert.equal(request.payload.sequenceGuid, "seq-1");
    assert.equal(request.payload.expectedCaptionTrackCount, existingCount);
    assert.equal(placed.captionTrackCount, existingCount + 1);
    assert.equal(await setup.sequence.getCaptionTrackCount(), existingCount + 1);
  });
}

test("SRTの読み込み中に字幕トラック数が変わった場合はCEPに送信しない", async () => {
  const setup = fixture(1);
  const target = await readTarget(setup.ppro);
  setup.project.importFiles = async () => {
    setup.sequence.getCaptionTrackCount = async () => 2;
    return true;
  };
  await assert.rejects(placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce }), /途中で変わりました/);
  assert.equal(setup.getRequest(), undefined);
});

test("対象の切替を検知し、SRT読み込み前に中止する", async () => {
  const setup = fixture();
  const target = await readTarget(setup.ppro);
  setup.sequence.guid = "seq-2";
  await assert.rejects(placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce }), /シークエンスに戻して/);
  assert.equal(setup.getImported(), undefined);
});

test("CEPが応答しないときは送信済みのまま配置成功を返さない", async () => {
  const setup = fixture();
  setup.outbox.getEntries = async () => [];
  const target = await readTarget(setup.ppro);
  let submitted = false;
  await assert.rejects(placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce,
    markSubmitted: () => { submitted = true; } }), /応答がありません/);
  assert.equal(submitted, true);
});

test("CEPが配置開始前に拒否した場合だけ再試行できる", async () => {
  for (const needsManualRecovery of [false, true]) {
    const setup = fixture();
    setup.outbox.getEntries = async () => [{ name: nonce + ".json", isFile: true, async read() {
      return JSON.stringify({ nonce, ok: false, error: "SyntaxError: Unexpected token E in JSON at position 0",
        result: needsManualRecovery ? { needsManualRecovery } : null });
    } }];
    const target = await readTarget(setup.ppro);
    let rejected = false;
    await assert.rejects(placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce,
      markRejected: () => { rejected = true; } }), needsManualRecovery ? /タイムライン/ : /配置開始前/);
    assert.equal(rejected, !needsManualRecovery);
  }
});
