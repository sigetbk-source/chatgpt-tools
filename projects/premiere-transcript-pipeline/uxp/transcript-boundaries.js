/**
 * Pure Transcript JSON edits.  These do not call Premiere; the caller must
 * perform target checks, backup, Import, save and read-back verification.
 */
function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function requireSegment(transcript, index) {
  if (!transcript || !Array.isArray(transcript.segments)) throw new Error('Transcript segments are required');
  if (!Number.isInteger(index) || index < 0 || index >= transcript.segments.length) throw new Error('Invalid segment index');
  const segment = transcript.segments[index];
  if (!Array.isArray(segment.words) || segment.words.length < 2) throw new Error('Segment needs at least two words');
  return segment;
}

function splitSegmentAtWord(transcript, segmentIndex, firstWordCount, nextSpeaker) {
  const output = clone(transcript);
  const segment = requireSegment(output, segmentIndex);
  if (!Number.isInteger(firstWordCount) || firstWordCount <= 0 || firstWordCount >= segment.words.length) {
    throw new Error('Split must be between words');
  }
  if (typeof nextSpeaker !== 'string' || !nextSpeaker) throw new Error('Next speaker is required');

  const boundary = segment.words[firstWordCount].start;
  const end = segment.start + segment.duration;
  if (!Number.isFinite(boundary) || boundary <= segment.start || boundary >= end) throw new Error('Split boundary is outside segment');

  const first = { ...segment, duration: boundary - segment.start, words: segment.words.slice(0, firstWordCount) };
  const second = {
    ...segment,
    start: boundary,
    duration: end - boundary,
    speaker: nextSpeaker,
    words: segment.words.slice(firstWordCount)
  };
  output.segments.splice(segmentIndex, 1, first, second);
  return output;
}

function mergeSegments(transcript, firstSegmentIndex, speaker) {
  const output = clone(transcript);
  const first = requireSegment(output, firstSegmentIndex);
  const second = requireSegment(output, firstSegmentIndex + 1);
  if (typeof speaker !== 'string' || !speaker) throw new Error('Merged speaker is required');
  const boundary = first.start + first.duration;
  if (Math.abs(boundary - second.start) > 0.000001) throw new Error('Segments are not contiguous');

  const merged = {
    ...first,
    duration: second.start + second.duration - first.start,
    speaker,
    words: [...first.words, ...second.words]
  };
  output.segments.splice(firstSegmentIndex, 2, merged);
  return output;
}

module.exports = { splitSegmentAtWord, mergeSegments };
