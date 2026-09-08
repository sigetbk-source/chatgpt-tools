const assert = require('assert');
const { splitSegmentAtWord, mergeSegments } = require('../uxp/transcript-boundaries');
const close = (actual, expected) => assert(Math.abs(actual - expected) < 0.000001, `${actual} != ${expected}`);

const original = {
  language: 'ja-jp',
  speakers: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }],
  segments: [{ start: 10.4, duration: 3.12, language: 'ja-jp', speaker: 'a', words: [
    { text: 'これ', start: 10.4 }, { text: 'は', start: 11.0 }, { text: '例', start: 12.22 }, { text: 'です', start: 12.8 }
  ] }]
};

const split = splitSegmentAtWord(original, 0, 2, 'b');
assert.strictEqual(split.segments.length, 2);
assert.strictEqual(split.segments[0].start, 10.4);
close(split.segments[0].duration, 1.82);
assert.strictEqual(split.segments[0].speaker, 'a');
assert.strictEqual(split.segments[1].start, 12.22);
close(split.segments[1].duration, 1.3);
assert.strictEqual(split.segments[1].speaker, 'b');
assert.deepStrictEqual(split.segments.flatMap(segment => segment.words), original.segments[0].words);
assert.deepStrictEqual(original.segments[0].words.map(word => word.text), ['これ', 'は', '例', 'です']);

const merged = mergeSegments(split, 0, 'a');
assert.strictEqual(merged.segments.length, 1);
assert.strictEqual(merged.segments[0].start, 10.4);
close(merged.segments[0].duration, 3.12);
assert.strictEqual(merged.segments[0].speaker, 'a');
assert.deepStrictEqual(merged.segments[0].words, original.segments[0].words);
assert.throws(() => splitSegmentAtWord(original, 0, 0, 'b'), /between words/);
assert.throws(() => splitSegmentAtWord(original, 0, 2, ''), /speaker/);
assert.throws(() => mergeSegments({ segments: [{ start: 0, duration: 1, words: [{}, {}] }, { start: 1.2, duration: 1, words: [{}, {}] }] }, 0, 'a'), /contiguous/);
console.log('Speaker-boundary split and merge checks passed');
