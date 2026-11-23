"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { generate, outputStem, parseJson, renderSrt, report, timecode } = require("../plugin/engine.js");

const word = (text, start = 0, duration = 0.4) => ({ text, start, duration, type: "word" });
const segment = (speaker, ...words) => ({ speaker, words });
const source = (...segments) => ({
  language: "ja-jp",
  speakers: [{ id: "A", name: "話者 1" }, { id: "B", name: "話者 1" }],
  segments,
});
const subtitle = (text, config) => generate(source(segment("A", word(text, 0, 3))), config);
const lines = (result) => result.cues.map(cue => cue.lines);

test("1. 句点前が11文字以上かつ結合後16文字以上なら句点を削除して改行する", () => {
  assert.deepEqual(lines(subtitle("一二三四五六七八九十甲。天地人空海。")), [["一二三四五六七八九十甲", "天地人空海"]]);
});

test("2. 句点前が10文字以内なら句点を削除して半角スペースで結合する", () => {
  assert.deepEqual(lines(subtitle("一二三四五六七八九十。天地人空海山。")), [["一二三四五六七八九十 天地人空海山"]]);
  assert.deepEqual(lines(subtitle("はい。そうです。合っています。")), [["はい そうです", "合っています"]]);
  assert.deepEqual(lines(subtitle("はい。そうです。", { joinShortPeriod: false })), [["はい", "そうです"]]);
  assert.deepEqual(lines(subtitle("「そうです。」本当ですか？")), [["「そうです」 本当ですか？"]]);
});

test("結合後15文字以内なら空白結合し、判定には句読点と空白を含めない", () => {
  const compact = "一二三四五六七八九十甲。天地人空。";
  assert.deepEqual(lines(subtitle(compact)), [["一二三四五六七八九十甲 天地人空"]]);
  assert.deepEqual(lines(subtitle(compact, { periodCompactLimit: 0 })), [["一二三四五六七八九十甲", "天地人空"]]);
  assert.deepEqual(lines(subtitle("一二三四五六七八九　十。天地人空海山。")), [["一二三四五六七八九　十 天地人空海山"]]);
  assert.deepEqual(lines(subtitle("一二三四五六七八九十甲。天地人空、続く。")), [["一二三四五六七八九十甲 天地人空", "続く"]]);
});

test("ナレーションは元区間ごとに1枚とし、文字、句読点、空白、改行、時刻を保持する", () => {
  const id = "00000000-0000-0000-0000-000000000001";
  const text = "あいうえお".repeat(6) + "。\n\n続けて、ニュースです！\n次の行。";
  const result = generate(source(segment(id, word(text, 0, 4), word(" 以上。", 5, 1)),
    segment(id, word("別の区間です。", 6.1, 1)), segment("A", word("はい。", 7.1, 0.6))), { maxChars: 10 });
  assert.deepEqual(result.cues[0].lines, (text + " 以上。").split("\n"));
  assert.deepEqual(result.cues.map(cue => [cue.start, cue.end]), [[0, 6000], [6100, 7100], [7100, 7700]]);
  assert.equal(result.cues[1].lines[0], "別の区間です。");
  assert.equal(result.cues[2].lines[0], "はい");
  assert.equal(result.srt.trim().split("\n\n").length, 3);
  const named = source(segment("V", word("ナレーションです。")));
  named.speakers.push({ id: "V", name: "ナレーション" });
  assert.deepEqual(lines(generate(named)), [["ナレーションです。"]]);
});

test("話者名と専用IDでナレーションを認識し、設定がオフならSRTから除外する", () => {
  const labels = ["ナレーション", "ナレ", "N  ", " na ", "ＮＡ", "Narrator", "川辺ナレ担当"];
  const data = source(...labels.map((name, i) => segment(String(i), word("例文。", i, 0.5))),
    segment("00000000-0000-0000-0000-000000000001", word("専用ID。", 7)),
    segment("N ", word("話者名のみ。", 8)));
  data.speakers = labels.map((name, i) => ({ id: String(i), name }));
  const included = generate(data);
  assert.equal(included.cues.filter(cue => cue.narration).length, 8);
  const excluded = generate(data, { includeNarration: false });
  assert.deepEqual(excluded.cues, included.cues.filter(cue => !cue.narration));
  assert.deepEqual(excluded.bySpeaker.map(group => group.name), ["川辺ナレ担当"]);
  assert.doesNotMatch(excluded.srt, /専用ID|話者名のみ/);
  assert.equal(generate(source(segment("ナレ", word("ナレーションだけ。"))),
    { includeNarration: false }).srt, "");
});

test("3. 読点を空白にして結合し、25文字を超える場合は改行する", () => {
  assert.deepEqual(lines(subtitle("そうですね、これは面白いですね。")), [["そうですね これは面白いですね"]]);
  const phrase = "あ".repeat(19) + "、いうえおかき";
  assert.deepEqual(lines(subtitle(phrase)), [["あ".repeat(19), "いうえおかき"]]);
  assert.deepEqual(lines(subtitle("ええ、そう。", { joinComma: false })), [["ええ、そう"]]);
});

test("4. 半角スペースは1行に最大1個で、3行目は別字幕になる", () => {
  assert.deepEqual(lines(subtitle("はい、そうです、その通り、間違いなく、ええ。")),
    [["はい そうです", "その通り 間違いなく"], ["ええ"]]);
  assert.deepEqual(lines(subtitle("一 二 三、 四")), [["一 二", "三 四"]]);
});

test("5. 疑問符と感嘆符は連続した記号と閉じ括弧ごと区切る", () => {
  assert.deepEqual(lines(subtitle("「本当ですか!?」それはすごい！！明日も来てください。")),
    [["「本当ですか!?」", "それはすごい！！"], ["明日も来てください"]]);
});

test("6. 25文字と2行を守り、長い英数字列を欠落させず要確認にする", () => {
  const long = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".repeat(2);
  const result = subtitle(long);
  assert.deepEqual(result.cues.flatMap(cue => cue.lines).map(text => text.length), [25, 25, 22]);
  assert.equal(result.cues.flatMap(cue => cue.lines).join(""), long);
  assert.ok(result.issues.some(issue => issue.code === "word_split"));
});

test("7. 同一話者の隣接区間をつなぎ、切り取られた文は補完しない", () => {
  const data = source(segment("A", word("今回のお店はですね、", 0, 0.8)),
    segment("A", word("非常に珍しいお店なんです", 0.9, 1)));
  assert.deepEqual(lines(generate(data, { maxChars: 20 })), [["今回のお店はですね", "非常に珍しいお店なんです"]]);
  assert.equal(generate(data, { joinSameSpeaker: false }).cues.length, 2);
});

test("8. 同名でも話者IDが異なれば別字幕、話者不明は区間単位で隔離する", () => {
  const result = generate(source(segment("A", word("はい")), segment("B", word("ええ", 0.4)),
    segment(null, word("そう", 0.8), word("です", 1.2)), segment(null, word("本当", 1.6))));
  assert.deepEqual(lines(result), [["はい"], ["ええ"], ["そうです"], ["本当"]]);
  assert.deepEqual(result.bySpeaker.map(speaker => speaker.id), ["A", "B", null]);
  assert.equal(result.issues.filter(issue => issue.code === "unknown_speaker").length, 2);
});

test("発言末尾で元区間が変わったら別字幕にし、次の区間の空欄をまたいでつなぐ", () => {
  const data = source(segment("A", { ...word("窓が開いているようです。", 0, 1), eos: true }),
    segment("A", word("その先で", 1.1, 1.1), word("", 2.65, 0.27), word("楽団が演奏します", 2.92, 1)));
  const result = generate(data);
  assert.deepEqual(lines(result), [["窓が開いているようです"], ["その先で楽団が演奏します"]]);
  assert.deepEqual(result.cues.map(cue => [cue.start, cue.end]), [[0, 1000], [1100, 3920]]);
  assert.deepEqual(lines(generate(source(segment("A", word("最初"), word("", 0.5, 5.2), word("後続", 5.7))))),
    [["最初"], ["後続"]]);
});

test("9. 1語内に複数字幕が必要なら時刻を按分し、入力は変更しない", () => {
  const data = source(segment("A", word("はい?!いいえ！！そうです。", 1.2504, 4)));
  const saved = JSON.stringify(data);
  const result = generate(data);
  assert.equal(JSON.stringify(data), saved);
  assert.equal(result.cues[0].start, 1250);
  assert.equal(result.cues[0].end, result.cues[1].start);
  assert.equal(result.cues[1].end, 5250);
  assert.ok(result.issues.some(issue => issue.code === "estimated_boundary"));
  const zero = generate(source(segment("A", word("はい", 2, 0))));
  assert.equal(zero.cues[0].end, 2001);
});

test("10. 同一話者の小さな重複は補正し、大きい重複は保持して報告する", () => {
  const input = source(segment("A", word("最初?!", 0.5, 0.52)),
    segment("A", word("後続。", 1, 0.5)));
  const small = generate(input, { maxLines: 1 });
  assert.equal(small.cues[0].end, 1000);
  assert.ok(small.issues.some(i => i.code === "time_adjusted"));
  const large = generate(input, { maxLines: 1, correctionMs: 0 });
  assert.equal(large.cues[0].end, 1020);
  assert.ok(large.issues.some(i => i.code === "same_speaker_overlap"));
});

test("11. 別話者が重なる場合は双方を維持し、話者別SRTにも出力できる", () => {
  const result = generate(source(segment("A", word("私の話", 0, 2)), segment("B", word("相づち", 1, 0.5))));
  assert.deepEqual(result.cues.map(cue => [cue.start, cue.end]), [[0, 2000], [1000, 1500]]);
  assert.ok(result.issues.some(issue => issue.code === "crosstalk"));
  assert.match(renderSrt(result.bySpeaker[1].cues), /^1\n00:00:01,000 --> 00:00:01,500\n相づち/);
});

test("12. 同じ元区間は無音5秒ちょうどまで結合し、5秒を超えたら分割する", () => {
  const data = gap => source(segment("A", word("最初", 0, 0.5), word("後続", 0.5 + gap, 1)));
  assert.deepEqual(lines(generate(data(0.8))), [["最初後続"]]);
  assert.deepEqual(lines(generate(data(5))), [["最初後続"]]);
  assert.deepEqual(lines(generate(data(5.001))), [["最初"], ["後続"]]);
  assert.deepEqual(lines(generate(data(0.5), { sameSegmentSilenceMs: 500 })), [["最初後続"]]);
});

test("元区間をまたぐ無音分割の閾値は独立して適用する", () => {
  const data = source(segment("A", word("最初", 0, 0.5)), segment("A", word("後続", 1, 1)));
  assert.deepEqual(lines(generate(data, { silenceMs: 500 })), [["最初"], ["後続"]]);
  assert.deepEqual(lines(generate(data, { silenceMs: 501 })), [["最初後続"]]);
});

test("区間時刻にフォールバックし、時刻が全て不正な本文はレポートに保持する", () => {
  const data = source({ speaker: "A", start: 1, duration: 2, text: "区間だけ。", words: [] },
    segment("B", word("保留する本文", -1, 0.3)));
  const result = generate(data);
  assert.deepEqual(result.cues.map(cue => [cue.start, cue.end]), [[1000, 3000]]);
  assert.ok(result.issues.some(issue => issue.code === "segment_timing"));
  assert.match(report(result), /保留する本文/);
});

test("禁則とUnicode文字境界を保ち、分単位の時間書式を出力する", () => {
  assert.deepEqual(lines(subtitle("あいうえ（お）", { maxChars: 5 })), [["あいうえ", "（お）"]]);
  assert.deepEqual(lines(subtitle("か\u3099👨‍👩‍👧‍👦あ", { maxChars: 2 })), [["か\u3099👨‍👩‍👧‍👦", "あ"]]);
  assert.equal(timecode(3723456.49), "01:02:03,456");
});

test("BOM付きJSON、シークエンス名と安全なファイル名", () => {
  assert.deepEqual(parseJson("\ufeff{}"), {});
  assert.equal(outputStem({}, "頭だけ.json"), "頭だけ");
  assert.equal(outputStem({ sequenceName: "編集 / 01" }, "export.json"), "編集 _ 01");
  assert.throws(() => generate({}, { maxLines: 3 }), /maxLines/);
});
