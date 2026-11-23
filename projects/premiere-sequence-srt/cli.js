#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const { generate, outputStem, parseJson, renderSrt, report } = require("./plugin/engine.js");

const usage = "node cli.js INPUT.json [--out-dir DIR] [--name NAME] [--settings CONFIG.json] [--by-speaker]";

function main(args) {
  if (args.includes("--help") || args.includes("-h")) {
    console.log(usage);
    return;
  }
  const [inputPath, ...flags] = args;
  if (!inputPath || inputPath.startsWith("--")) throw new Error(usage);
  const options = { "--out-dir": path.dirname(inputPath) };
  let bySpeaker = false;
  for (let i = 0; i < flags.length; i++) {
    const flag = flags[i];
    if (flag === "--by-speaker") {
      bySpeaker = true;
    } else if (["--out-dir", "--name", "--settings"].includes(flag) && flags[i + 1] && !flags[i + 1].startsWith("--")) {
      options[flag] = flags[++i];
    } else {
      throw new Error("不明な引数、または引数値がありません: " + flag + "\n" + usage);
    }
  }
  const input = parseJson(fs.readFileSync(inputPath, "utf8"));
  const config = options["--settings"] ? parseJson(fs.readFileSync(options["--settings"], "utf8")) : {};
  const result = generate(input, config);
  const stem = outputStem(options["--name"] ? { sequenceName: options["--name"] } : input, path.basename(inputPath));
  const directory = options["--out-dir"];
  const outputs = [[stem + ".review.json", report(result)]];
  if (result.cues.length) outputs.unshift([stem + ".srt", result.srt]);
  if (bySpeaker) {
    for (const [index, speaker] of result.bySpeaker.entries()) {
      const name = outputStem({}, speaker.name);
      outputs.push([stem + ".speaker-" + (index + 1) + "-" + name + ".srt", renderSrt(speaker.cues)]);
    }
  }

  fs.mkdirSync(directory, { recursive: true });
  const protectedPaths = [inputPath, options["--settings"]].filter(Boolean).map(filename => {
    const existing = fs.statSync(filename);
    return [existing.dev, existing.ino];
  });
  for (const [filename] of outputs) {
    let existing;
    try {
      existing = fs.statSync(path.join(directory, filename));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (existing && protectedPaths.some(([dev, ino]) => dev === existing.dev && ino === existing.ino)) {
      throw new Error("入力ファイルと出力先が重なっています。--out-dir または --name を指定してください。");
    }
  }
  for (const [filename, contents] of outputs) {
    const target = path.join(directory, filename);
    const temporary = path.join(directory, "." + randomUUID() + ".tmp");
    try {
      fs.writeFileSync(temporary, contents, { encoding: "utf8", flag: "wx" });
      fs.renameSync(temporary, target);
    } finally {
      fs.rmSync(temporary, { force: true });
    }
    console.log(target);
  }
  console.log(result.stats.cues + "枚｜話者ID " + result.stats.speakers + "件｜要確認 " + result.issues.length + "件");
  if (!result.cues.length) process.exitCode = 1;
}

try {
  main(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
