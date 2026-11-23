"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { generate } = require("../plugin/engine.js");
const { placeCaptions, readTarget, readVideoTimeline } = require("../plugin/placement.js");

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

test("最上段の映像が切り替わる点を取得し、ミュートしたトラックは除外する", async () => {
  const setup = fixture();
  const time = seconds => ({ seconds });
  const clip = (start, end) => ({ getStartTime: () => time(start), getEndTime: () => time(end) });
  const tracks = [{
    isMuted: async () => false,
    getTrackItems(type, includeEmpty) {
      assert.equal(type, 1);
      assert.equal(includeEmpty, false);
      return [clip(1.2345, 2), clip(0, 1.2345)];
    },
  }, {
    isMuted: async () => true,
    getTrackItems() { assert.fail("ミュートしたトラックを読み込んだ"); },
  }, {
    isMuted: async () => false,
    getTrackItems() { return [clip(1, 1.5)]; },
  }];
  setup.ppro.Constants = { TrackItemType: { CLIP: 1 } };
  setup.sequence.getVideoTrackCount = async () => tracks.length;
  setup.sequence.getVideoTrack = async index => tracks[index];
  setup.sequence.getAudioTrackCount = async () => 1;
  let audioClips = [clip(0, 2.5)];
  setup.sequence.getAudioTrack = async () => ({
    isMuted: async () => false,
    getTrackItems: () => audioClips,
  });
  const target = await readTarget(setup.ppro);
  assert.deepEqual(await readVideoTimeline(setup.ppro, target), { endMs: 3000, cuts: [0, 1000] });
  audioClips = [clip(0, 1.5006), clip(1.5006, 2.5)];
  assert.deepEqual(await readVideoTimeline(setup.ppro, target), { endMs: 3000, cuts: [0, 1000, 1500] });
  setup.sequence.guid = "seq-2";
  await assert.rejects(readVideoTimeline(setup.ppro, target), /シークエンスに戻してください/);
});

test("発話終了と同じフレームの映像カットで止め、上段のテキストを無視して次の字幕まで延長する", async () => {
  const setup = fixture();
  const clip = (start, end) => ({
    getStartTime: () => ({ seconds: start }),
    getEndTime: () => ({ seconds: end }),
  });
  const track = clips => ({ isMuted: () => false, getTrackItems: () => clips });
  const cut = 134.200733;
  const title = clip(180, 188.4883);
  title.getComponentChain = async () => ({
    getComponentCount: () => 2,
    getComponentAtIndex: index => ({ getMatchName: async () => ["AE.ADBE Motion", "AE.ADBE Text"][index] }),
  });
  setup.ppro.Constants = { TrackItemType: { CLIP: 1 } };
  setup.sequence.getEndTime = () => ({ seconds: 200 });
  setup.sequence.getVideoTrackCount = () => 2;
  setup.sequence.getVideoTrack = index => [
    track([clip(0, cut), clip(cut, 200)]), track([title]),
  ][index];
  setup.sequence.getAudioTrackCount = () => 1;
  setup.sequence.getAudioTrack = () => track([clip(0, cut), clip(cut, 188.4883), clip(188.4883, 200)]);
  const timeline = await readVideoTimeline(setup.ppro, await readTarget(setup.ppro));
  assert.deepEqual(timeline.cuts, [0, 134201, 200000]);
  const segments = [
    ["a", "先の発話。", 132.768, 1.433], ["n", "次の発話。", 135.087, 1],
    ["a", "あとの発話。", 185.350, 1.110], ["n", "その次の発話。", 188.710, 1],
  ].map(([speaker, text, start, duration]) => ({ speaker, words: [{ text, start, duration }] }));
  const result = generate({ speakers: input.speakers, segments }, { extendShortCueMs: 4000 }, timeline);
  assert.equal(result.cues[0].end, 134201);
  assert.equal(result.cues[2].end, 188710);
  title.getComponentChain = async () => { throw new Error("component chain unavailable"); };
  const fallback = await readVideoTimeline(setup.ppro, await readTarget(setup.ppro));
  assert.deepEqual(fallback.cuts, [0, 134201, 180000, 188488, 200000]);
  setup.sequence.getTimebase = () => "8475667200";
  setup.ppro.TickTime = { createWithTicks(ticks) {
    assert.equal(ticks, "8475667200");
    return { seconds: 1001 / 30000 };
  } };
  const frames = await readVideoTimeline(setup.ppro, await readTarget(setup.ppro), true);
  assert.ok(Math.abs(frames.frameMs - 1001 / 30) < 0.000001);
  assert.deepEqual(frames.visibleSpans, [[0, 134201], [134201, 180000], [180000, 188488], [188488, 200000]]);
});

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

test("保存・バックアップに33秒かかってもCEPの配置結果を受け取れる", async () => {
  const setup = fixture(1);
  const getEntries = setup.outbox.getEntries;
  setup.outbox.getEntries = async () => setup.now() < 33000 ? [] : getEntries();
  const target = await readTarget(setup.ppro);
  const placed = await placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce });
  assert.equal(setup.getRequest().deadlineMs, 120000);
  assert.equal(setup.now(), 33000);
  assert.equal(placed.captionTrackCount, 2);
});

test("保存・バックアップ中に期限切れになった場合は配置を再試行できる", async () => {
  const setup = fixture();
  setup.outbox.getEntries = async () => {
    if (setup.now() < setup.getRequest().deadlineMs + 1000) return [];
    return [{ name: nonce + ".json", isFile: true, async read() {
      return JSON.stringify({ nonce, ok: false, error: "Error: Request expired before caption placement" });
    } }];
  };
  const target = await readTarget(setup.ppro);
  let rejected = false;
  await assert.rejects(placeCaptions({ ...setup, target, result: generate(input), stem: "字幕", nonce,
    markRejected: () => { rejected = true; } }), /字幕を追加する前に中断/);
  assert.equal(rejected, true);
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
