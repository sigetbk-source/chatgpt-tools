"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

test("CLIで文字起こしJSONをSRT・確認レポート・話者別SRTに変換する", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "sequence-srt-test-"));
  try {
    const source = path.join(directory, "字幕テスト.json");
    const out = path.join(directory, "output");
    const input = JSON.stringify({
      language: "ja-jp",
      segments: [
        { speaker: "A", words: [{ start: 1, duration: 0.8, text: "集合しました。", eos: true }] },
        { speaker: "A", words: [
          { start: 1.9, duration: 0.5, text: "では" },
          { start: 2.85, duration: 0.27, text: "" },
          { start: 3.12, duration: 0.8, text: "出発します。" },
        ] },
        { speaker: "B", words: [{ start: 4, duration: 0.7, text: "了解です。" }] },
      ],
    });
    fs.writeFileSync(source, input);
    execFileSync(process.execPath, [path.join(__dirname, "../cli.js"), source, "--out-dir", out, "--by-speaker"]);
    const read = name => fs.readFileSync(path.join(out, name), "utf8");
    assert.deepEqual(fs.readdirSync(out).sort(),
      ["字幕テスト.review.json", "字幕テスト.speaker-1-A.srt", "字幕テスト.speaker-2-B.srt", "字幕テスト.srt"]);
    assert.equal(read("字幕テスト.srt"), [
      "1\n00:00:01,000 --> 00:00:01,800\n集合しました",
      "2\n00:00:01,900 --> 00:00:03,920\nでは出発します",
      "3\n00:00:04,000 --> 00:00:04,700\n了解です",
    ].join("\n\n") + "\n\n");
    assert.equal(read("字幕テスト.speaker-2-B.srt"), "1\n00:00:04,000 --> 00:00:04,700\n了解です\n\n");
    assert.deepEqual(JSON.parse(read("字幕テスト.review.json")).issues, []);
    assert.equal(fs.readFileSync(source, "utf8"), input);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
