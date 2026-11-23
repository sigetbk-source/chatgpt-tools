"use strict";

const defaults = Object.freeze({
  maxChars: 25, maxLines: 2, periodThreshold: 10, periodCompactLimit: 15, joinShortPeriod: true,
  joinComma: true, maxSpaces: 1, breakQuestions: true, joinSameSpeaker: true,
  silenceMs: 650, sameSegmentSilenceMs: 5000, correctionMs: 40, detectOverlap: true, minCueMs: 300,
  maxCueMs: 0, cueGapMs: 0, includeNarration: true, extendShortCueMs: 0, joinGapFrames: 0,
});

const labels = {
  unknown_speaker: "話者不明", invalid_time: "不正な単語時刻",
  segment_timing: "区間時刻を使用", short_cue: "短時間の字幕",
  time_adjusted: "時刻補正", same_speaker_overlap: "同一話者の時刻重複",
  crosstalk: "別話者の時刻重複", unknown_overlap: "話者不明の時刻重複",
  word_split: "単語の途中で改行", estimated_boundary: "単語内の字幕境界を推定",
  kinsoku: "禁則を回避できない行境界", cue_overlap: "SRT内の字幕重複",
  long_cue: "表示時間の上限超過", cue_gap: "字幕間隔が設定値未満",
};

const noStart = new Set(Array.from("、。，．！？!?：；・ー〜～…‥」』】）》〉〕］｝’”）)]}ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々"));
const noEnd = new Set(Array.from("「『【《〈〔［｛‘“（([{"));
const closers = new Set(Array.from("」』】）》〉〕］｝’”）)]}"));
const questions = new Set(Array.from("！？!?"));
const particles = new Set(["は", "が", "を", "に", "で", "へ", "と", "も", "から", "まで", "より", "ので", "けど", "ね", "よ"]);
const narrationId = "00000000-0000-0000-0000-000000000001";
const narrationNames = new Set(["ナレーション", "ナレーター", "ナレ", "n", "na", "narration", "narrator"]);
const whitespace = /[ \t\u3000]/;
const latin = /^[\p{Script=Latin}\p{Number}]$/u;
const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const countsTowardPeriodLimit = value => !/^[。、\s]+$/u.test(value);

function settings(options = {}) {
  for (const key of Object.keys(options)) {
    if (!own(defaults, key)) throw new Error("未対応の設定 " + key);
  }
  const result = { ...defaults, ...options };
  for (const [key, value] of Object.entries(result)) {
    if (typeof defaults[key] === "boolean") {
      if (typeof value !== "boolean") throw new Error(key + " は true または false を指定してください");
    } else if (!Number.isSafeInteger(value) || value > (key === "maxLines" ? 2 : 3600000)
      || value < (["maxChars", "maxLines", "periodThreshold"].includes(key) ? 1 : 0)) {
      throw new Error(key + " の値が範囲外です");
    }
  }
  return result;
}

function segmenter(granularity) {
  return typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter("ja", { granularity }) : null;
}

const graphemeSegmenter = segmenter("grapheme");
const wordSegmenter = segmenter("word");

// Mirrors the existing SRT layout's Unicode cluster fallback for older UXP hosts.
function clusters(value) {
  if (graphemeSegmenter) return Array.from(graphemeSegmenter.segment(value), item => item.segment);
  const result = [];
  for (const char of value) {
    const prev = result[result.length - 1];
    const join = /[\p{Mark}\u200d\ufe00-\ufe0f\u{1f3fb}-\u{1f3ff}]/u.test(char)
      || (prev && prev.endsWith("\u200d"))
      || (/^[\u{1f1e6}-\u{1f1ff}]$/u.test(char) && /^[\u{1f1e6}-\u{1f1ff}]$/u.test(prev));
    if (prev && join) result[result.length - 1] += char;
    else result.push(char);
  }
  return result;
}

function timedRange(item) {
  if (!item || !Number.isFinite(item.start)) return null;
  const end = Number.isFinite(item.duration) ? item.start + item.duration : item.end;
  if (!Number.isFinite(end) || item.start < 0 || end < item.start || end * 1000 >= Number.MAX_SAFE_INTEGER) return null;
  return [item.start * 1000, end * 1000];
}

function parseTranscript(input, issues) {
  if (!input || !Array.isArray(input.segments)) {
    throw new Error("Premiereの文字起こしJSONを選択してください（segments がありません）");
  }
  if (input.speakers != null && !Array.isArray(input.speakers)) throw new Error("speakers の形式が不正です");
  const names = new Map((input.speakers || []).filter(s => s && typeof s.id === "string")
    .map(s => [s.id, typeof s.name === "string" ? s.name : s.id]));
  let inputWordCount = 0;
  let emptyWordCount = 0;
  const blocks = [];
  input.segments.forEach((seg, index) => {
    if (!seg || typeof seg !== "object" || Array.isArray(seg)) throw new Error("区間 " + (index + 1) + " が不正です");
    const segment = index + 1;
    const speaker = typeof seg.speaker === "string" && seg.speaker.trim() ? seg.speaker : null;
    const speakerLabel = (names.get(speaker) ?? speaker ?? "").normalize("NFKC").trim().toLowerCase();
    const narration = speaker === narrationId || narrationNames.has(speakerLabel);
    const entries = Array.isArray(seg.words) && seg.words.length
      ? seg.words : (typeof seg.text === "string" && seg.text.trim() ? [seg] : []);
    if (!entries.length && !Array.isArray(seg.words)) throw new Error("区間 " + segment + " に words または text がありません");
    const segmentTime = timedRange(seg);
    const words = [];
    entries.forEach((entry, i) => {
      if (!entry || typeof entry.text !== "string") throw new Error("区間 " + segment + " の語 " + (i + 1) + " に文字列がありません");
      inputWordCount++;
      if (!entry.text.trim()) {
        emptyWordCount++;
        if (entry.eos === true && words.length) words[words.length - 1].eos = true;
        return;
      }
      const direct = timedRange(entry);
      const time = direct || segmentTime;
      if (!time) {
        issues.push({ code: "invalid_time", start: null, segment, word: i + 1, speaker,
          text: entry.text, detail: "時刻を復元できないため本文をこのレポートに保持" });
        return;
      }
      if (!direct || entry === seg) {
        issues.push({ code: "segment_timing", start: time[0], segment, word: i + 1, speaker,
          text: entry.text, detail: entry === seg ? "単語時刻なし" : "単語時刻が不正" });
      }
      words.push({ start: time[0], end: time[1], text: entry.text, segment, word: i + 1,
        speaker, narration, eos: entry.eos === true, fallback: !direct || entry === seg });
    });
    if (!words.length) return;
    if (!speaker) issues.push({ code: "unknown_speaker", start: words[0].start, segment, text: words.map(word => word.text).join("") });
    blocks.push({ speaker, words, language: String(seg.language || input.language || "ja"),
      narrationText: narration ? entries.map(entry => entry.text).join("") : null });
  });
  return { blocks, names, inputWordCount, emptyWordCount, segments: input.segments.length };
}

function checkTiming(blocks, config, issues) {
  const words = blocks.flatMap(block => block.words).filter(word => !word.fallback);
  words.sort((a, b) => a.start - b.start || a.segment - b.segment || a.word - b.word);
  let active = [];
  for (const word of words) {
    active = active.filter(previous => previous.end - word.start > 0.0001);
    if (word.start === word.end) continue;
    for (const previous of active) {
      const same = word.speaker !== null && word.speaker === previous.speaker;
      const overlap = Math.min(previous.end, word.end) - word.start;
      const record = { start: word.start, end: word.start + overlap, speaker: word.speaker,
        otherSpeaker: previous.speaker, segment: word.segment, otherSegment: previous.segment,
        word: word.word, otherWord: previous.word, text: word.text, otherText: previous.text };
      if (same && !word.narration && previous.end <= word.end && previous.end - word.start <= config.correctionMs
        && word.start > previous.start && !active.some(other => other !== previous && other.speaker !== word.speaker)) {
        issues.push({ ...record, code: "time_adjusted", detail: "先行語の終端を後続語の開始に丸めた" });
        previous.end = word.start;
      } else if (config.detectOverlap) {
        const code = same ? "same_speaker_overlap" : (word.speaker && previous.speaker ? "crosstalk" : "unknown_overlap");
        issues.push({ ...record, code });
      }
    }
    active.push(word);
  }
}

function exceedsSilence(previous, word, config) {
  const gap = word.start - previous.end;
  if (previous.segment === word.segment) return Math.round(gap) > config.sameSegmentSilenceMs;
  return gap > 0 && gap >= config.silenceMs;
}

function runs(blocks, config) {
  const result = [];
  let run;
  let previous;
  for (const block of blocks) {
    if (block.narrationText !== null) {
      result.push(block);
      run = null;
      previous = null;
      continue;
    }
    for (const word of block.words) {
      const newBlock = previous && previous.segment !== word.segment;
      const splitAtBlock = newBlock && (previous.eos || !word.speaker || !config.joinSameSpeaker);
      if (!run || word.speaker !== run.speaker || block.language !== run.language || splitAtBlock
        || word.fallback || previous.fallback || word.start < previous.start
        || exceedsSilence(previous, word, config)) {
        run = { speaker: word.speaker, language: block.language, words: [] };
        result.push(run);
      }
      run.words.push(word);
      previous = word;
    }
  }
  return result;
}

function characterStream(run) {
  const chars = [];
  for (const word of run.words) {
    const values = clusters(word.text.replace(/\r\n?/g, "\n"));
    const previous = chars[chars.length - 1];
    if (!/^(ja|zh)(-|$)/i.test(run.language) && previous && latin.test(previous.value) && latin.test(values[0])) {
      chars.push({ value: " ", word, start: word.start, end: word.start });
    }
    values.forEach((value, index) => {
      const step = (word.end - word.start) / values.length;
      chars.push({ value, word, index, start: word.start + index * step, end: word.start + (index + 1) * step });
    });
  }
  return chars;
}

function chunkStream(chars, config) {
  const chunks = [];
  let start = 0;
  let gap = "none";
  let sentenceLength = 0;
  function finish(end, nextStart, nextGap, omitted = -1) {
    const content = chars.slice(start, end);
    if (omitted >= start && omitted < end) content.splice(omitted - start, 1);
    if (content.length) chunks.push({ chars: content, gap });
    if (nextGap === "hard" || gap !== "hard" || content.length) gap = nextGap;
    start = nextStart;
  }
  for (let i = 0; i < chars.length; i++) {
    const { value } = chars[i];
    if (value === "\n") {
      finish(i, i + 1, "hard");
      sentenceLength = 0;
    } else if (value === "、" && config.joinComma) {
      finish(i, i + 1, "space");
    } else if (whitespace.test(value)) {
      if (i > start) finish(i, i + 1, value === "　" ? "wide" : "space");
      else start = i + 1;
    } else if (value === "。" || config.breakQuestions && questions.has(value)) {
      let end = i + 1;
      if (questions.has(value)) {
        while (end < chars.length && questions.has(chars[end].value)) end++;
      }
      while (end < chars.length && closers.has(chars[end].value)) end++;
      let nextGap = "hard";
      if (value === "。" && config.joinShortPeriod) {
        nextGap = sentenceLength <= config.periodThreshold ? "space" : "compactPeriod";
      }
      finish(end, end, nextGap, value === "。" ? i : -1);
      i = end - 1;
      sentenceLength = 0;
    } else if (countsTowardPeriodLimit(value)) {
      sentenceLength++;
    }
  }
  finish(chars.length, chars.length, "none");
  return chunks;
}

function naturalBoundaries(chars) {
  const dictionary = new Set();
  if (wordSegmenter) {
    const boundaries = new Map();
    let offset = 0;
    chars.forEach((char, i) => {
      offset += char.value.length;
      boundaries.set(offset, i + 1);
    });
    for (const item of wordSegmenter.segment(chars.map(char => char.value).join(""))) {
      const end = boundaries.get(item.index + item.segment.length);
      if (end) dictionary.add(end);
    }
  }
  return dictionary;
}

function lineBreak(chars, start, limit, dictionary) {
  let best = limit;
  let bestScore = -1;
  for (let end = limit; end > start; end--) {
    const last = chars[end - 1];
    const next = chars[end];
    if (noEnd.has(last.value) || noStart.has(next.value)) continue;
    let score = 0;
    if (/[、。！？!?,]/u.test(last.value)) score = 4;
    else if (last.word !== next.word) score = particles.has(last.word.text) ? 3 : 2;
    else if (dictionary && dictionary.has(end)) score = 1;
    if (score > bestScore) {
      best = end;
      bestScore = score;
    }
  }
  return best;
}

function layoutRun(run, config, issues) {
  const chunks = chunkStream(characterStream(run), config);
  const lines = [];
  let line = [];
  let spaces = 0;
  function commit() {
    if (line.length) lines.push(line);
    line = [];
    spaces = 0;
  }
  for (const { chars, gap } of chunks) {
    const needsSpace = gap === "space" || gap === "wide" || gap === "compactPeriod";
    const combinedLength = line.length + chars.length + 1;
    const exceedsCompactLimit = gap === "compactPeriod" && line.concat(chars)
      .filter(char => countsTowardPeriodLimit(char.value)).length > config.periodCompactLimit;
    if (line.length && (gap === "hard" || (needsSpace && (combinedLength > config.maxChars || exceedsCompactLimit
      || gap !== "wide" && spaces >= config.maxSpaces)))) commit();
    if (line.length && needsSpace) {
      const space = gap === "wide" ? "　" : " ";
      line.push({ value: space, start: chars[0].start, end: chars[0].start, word: null });
      if (space === " ") spaces++;
    }
    const dictionary = chars.length > config.maxChars ? naturalBoundaries(chars) : null;
    let start = 0;
    while (start < chars.length) {
      const available = config.maxChars - line.length;
      if (chars.length - start > available) {
        const end = lineBreak(chars, start, start + available, dictionary);
        line = line.concat(chars.slice(start, end));
        commit();
        start = end;
      } else {
        line = line.concat(chars.slice(start));
        break;
      }
    }
  }
  commit();
  lines.forEach((current, index) => {
    if (!index) return;
    const first = current[0];
    const last = lines[index - 1][lines[index - 1].length - 1];
    if (noEnd.has(last.value) || noStart.has(first.value)) {
      issues.push({ code: "kinsoku", start: first.start, speaker: run.speaker, text: current.map(c => c.value).join("") });
    }
    if (last.word === first.word && last.index + 1 === first.index && !/[、。！？!?,]/u.test(last.value)
      && !naturalBoundaries(clusters(first.word.text).map(value => ({ value }))).has(first.index)) {
      issues.push({ code: "word_split", start: first.start, speaker: run.speaker, text: first.word.text });
    }
  });
  return lines;
}

function buildCues(run, config, issues) {
  if (run.narrationText != null) {
    const start = Math.round(run.words[0].start);
    const end = Math.max(start + 1, Math.round(run.words[run.words.length - 1].end));
    const lines = run.narrationText.split(/\r\n|\r|\n/u);
    return [{ start, end, lines, speaker: run.speaker, narration: true }];
  }
  const lines = layoutRun(run, config, issues);
  const cues = [];
  let page = [];
  let prev;
  const commit = (next) => {
    if (!page.length) return;
    const first = page[0][0];
    const lastLine = page[page.length - 1];
    const last = lastLine[lastLine.length - 1];
    const text = page.map(line => line.map(char => char.value).join(""));
    const start = Math.round(prev && prev.word === first.word ? first.start : first.word.start);
    const sourceEnd = Math.round(next && next.word === last.word ? last.end : last.word.end);
    const end = Math.max(start + 1, sourceEnd);
    if (config.maxCueMs && end - start > config.maxCueMs) issues.push({ code: "long_cue", start, end, speaker: run.speaker, text: text.join("\n") });
    if (sourceEnd <= start) issues.push({ code: "time_adjusted", start, end, speaker: run.speaker, detail: "正のSRT表示時間を確保" });
    cues.push({ start, end, speaker: run.speaker, lines: text });
    prev = last;
    page = [];
  };
  for (const line of lines) {
    const exceedDuration = config.maxCueMs && page.length
      && line[line.length - 1].end - page[0][0].start > config.maxCueMs;
    if (page.length === config.maxLines || exceedDuration) {
      const lastLine = page[page.length - 1];
      const previous = lastLine[lastLine.length - 1];
      if (previous.word === line[0].word) {
        issues.push({ code: "estimated_boundary", start: line[0].start, speaker: run.speaker,
          segment: previous.word.segment, word: previous.word.word, text: previous.word.text,
          detail: "単語内の文字位置から時刻を按分" });
      }
      commit(line[0]);
    }
    page.push(line);
  }
  commit();
  return cues;
}

function timecode(ms) {
  const pad = (n, width) => String(n).padStart(width, "0");
  const t = Math.max(0, Math.round(ms));
  return pad(Math.floor(t / 3600000), 2) + ":" + pad(Math.floor(t / 60000) % 60, 2) + ":"
    + pad(Math.floor(t / 1000) % 60, 2) + "," + pad(t % 1000, 3);
}

function renderSrt(cues) {
  return cues.map((c, i) => (i + 1) + "\n" + timecode(c.start) + " --> " + timecode(c.end)
    // SRT reserves a blank line for the cue delimiter, so use a non-breaking space for an original blank line.
    + "\n" + c.lines.map(line => line.trim() ? line : line + "\u00a0").join("\n") + "\n\n").join("");
}

function verifyCueTiming(cues, config, issues) {
  let active = [];
  for (const cue of cues) {
    active = active.filter(previous => previous.end > cue.start - config.cueGapMs);
    for (const previous of active) {
      const gap = cue.start - previous.end;
      if (config.cueGapMs && !previous.narration && previous.speaker && previous.speaker === cue.speaker && gap >= 0
        && config.cueGapMs - gap <= config.correctionMs
        && cue.start - config.cueGapMs - previous.start >= Math.max(1, config.minCueMs)) {
        previous.end = cue.start - config.cueGapMs;
        issues.push({ code: "time_adjusted", start: previous.end, speaker: cue.speaker, detail: "字幕間隔を確保" });
      } else if (gap < 0 && config.detectOverlap) {
        issues.push({ code: "cue_overlap", start: cue.start, end: Math.min(cue.end, previous.end),
          speaker: cue.speaker, otherSpeaker: previous.speaker, text: cue.lines.join("\n") });
      } else if (gap >= 0 && config.cueGapMs && previous.speaker && previous.speaker === cue.speaker) {
        issues.push({ code: "cue_gap", start: cue.start, speaker: cue.speaker, text: cue.lines.join("\n") });
      }
    }
    active.push(cue);
  }
}

function joinCueGaps(cues, config, timeline) {
  if (!Number.isFinite(timeline?.frameMs) || timeline.frameMs <= 0 || !Array.isArray(timeline.visibleSpans)) {
    throw new Error("字幕つなぎには、Premiereで対象シークエンスのフレームレートと映像クリップが必要です。");
  }
  if (config.cueGapMs) return;
  const maxGap = Math.ceil(config.joinGapFrames * timeline.frameMs);
  const spans = timeline.visibleSpans;
  let spanIndex = 0;
  for (let index = 0; index + 1 < cues.length; index++) {
    const cue = cues[index], next = cues[index + 1];
    const gap = next.start - cue.end;
    if (gap <= 0) continue;
    // SRT times are in milliseconds. Also count the frame boundaries in the gap.
    const frameGap = Math.ceil(next.start / timeline.frameMs) - Math.ceil(cue.end / timeline.frameMs);
    if (gap > maxGap && frameGap > config.joinGapFrames) continue;
    if (config.maxCueMs && next.start - cue.start > config.maxCueMs) continue;
    while (spans[spanIndex]?.[1] < cue.end) spanIndex++;
    const span = spans[spanIndex];
    if (span && span[0] < cue.end && next.start < span[1]) cue.end = next.start;
  }
}

function extendShortCues(cues, config, timeline) {
  if (!Number.isSafeInteger(timeline?.endMs) || timeline.endMs < 0 || !Array.isArray(timeline.cuts)
    || timeline.cuts.some(cut => !Number.isSafeInteger(cut) || cut < 0)) {
    throw new Error("短い字幕を延長するには、Premiereで対象シークエンスの映像カット位置を読み込んでください。");
  }
  const cuts = [...timeline.cuts].sort((a, b) => a - b);
  const extensionLimits = new Map();
  let cutIndex = 0;
  for (const [index, cue] of cues.entries()) {
    if (cue.end - cue.start >= config.extendShortCueMs) continue;
    while (cuts[cutIndex] <= cue.start) cutIndex++;
    let upcomingCut = cutIndex;
    while (cuts[upcomingCut] < cue.end) upcomingCut++;
    const next = cues[index + 1];
    const nextLimit = next ? next.start - config.cueGapMs : Infinity;
    const durationLimit = config.maxCueMs ? cue.start + config.maxCueMs : Infinity;
    const targetEnd = cue.start + config.extendShortCueMs;
    const cutLimit = cuts[upcomingCut] ?? Infinity;
    const limit = Math.min(targetEnd, timeline.endMs, nextLimit, cutLimit, durationLimit);
    if (limit > cue.end) cue.end = limit;
    if (limit < targetEnd) {
      const reasons = [[nextLimit, "次の字幕"], [cutLimit, "映像の切り替わり"],
        [timeline.endMs, "シークエンスの終端"], [durationLimit, "最長表示の設定"]];
      const reason = reasons.find(([time]) => time === limit)[1];
      extensionLimits.set(cue, reason + "で延長を制限");
    }
  }
  return extensionLimits;
}

function generate(input, options, timeline) {
  const config = settings(options);
  const issues = [];
  const parsed = parseTranscript(input, issues);
  checkTiming(parsed.blocks, config, issues);
  const cues = runs(parsed.blocks, config)
    .filter(run => config.includeNarration || run.narrationText == null)
    .flatMap(run => buildCues(run, config, issues))
    .sort((a, b) => a.start - b.start);
  const extensionLimits = config.extendShortCueMs ? extendShortCues(cues, config, timeline) : null;
  if (config.joinGapFrames) joinCueGaps(cues, config, timeline);
  verifyCueTiming(cues, config, issues);
  for (const cue of cues) {
    const { start, end, speaker, lines, narration } = cue;
    const minimum = narration ? config.extendShortCueMs : Math.max(config.minCueMs, config.extendShortCueMs);
    if (end - start < minimum) {
      const detail = extensionLimits?.get(cue);
      issues.push({ code: "short_cue", start, end, speaker, text: lines.join("\n"), ...(detail && { detail }) });
    }
  }
  issues.sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity));
  const speakers = new Map();
  for (const cue of cues) {
    if (!speakers.has(cue.speaker)) speakers.set(cue.speaker, []);
    speakers.get(cue.speaker).push(cue);
  }
  return {
    cues, issues, srt: renderSrt(cues),
    stats: { segments: parsed.segments, words: parsed.inputWordCount, emptyWords: parsed.emptyWordCount,
      cues: cues.length, speakers: speakers.size - Number(speakers.has(null)) },
    bySpeaker: Array.from(speakers, ([id, group]) => ({
      id, name: id === null ? "話者不明" : parsed.names.get(id) || id, cues: group,
    })),
  };
}

function issueText(issue) {
  const time = issue.start == null ? "時刻不明" : timecode(issue.start);
  const source = issue.segment ? " 区間" + issue.segment + (issue.word ? "/語" + issue.word : "") : "";
  return [time + " " + labels[issue.code] + source, issue.text && issue.text.replace(/\s+/g, " "),
    issue.detail].filter(Boolean).join("｜");
}

function report(result) {
  return JSON.stringify({ stats: result.stats, issues: result.issues.map(issue => ({
    ...issue, timecode: issue.start == null ? null : timecode(issue.start),
    endTimecode: issue.end == null ? null : timecode(issue.end), summary: issueText(issue),
  })) }, null, 2) + "\n";
}

function outputStem(input, sourceName) {
  return String(input.sequenceName || input.sequence?.name || sourceName.replace(/\.json$/i, ""))
    .trim().replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").replace(/^[. ]+|[. ]+$/g, "") || "字幕";
}

function parseJson(text) {
  return JSON.parse(text.replace(/^\ufeff/, ""));
}

module.exports = { defaults, generate, issueText, outputStem, parseJson, renderSrt, report, timecode };
