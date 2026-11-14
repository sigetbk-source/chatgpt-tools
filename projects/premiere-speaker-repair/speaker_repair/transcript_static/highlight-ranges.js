(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.HighlightRanges = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function normalize(ranges) {
    const sorted = (ranges || [])
      .map(range => ({start: Number(range.start), end: Number(range.end), kind: String(range.kind || '')}))
      .filter(range => Number.isFinite(range.start) && Number.isFinite(range.end) && range.start < range.end)
      .sort((left, right) => left.start - right.start || left.end - right.end);
    const output = [];
    for (const range of sorted) {
      const previous = output[output.length - 1];
      if (previous && previous.kind === range.kind && previous.end === range.start) previous.end = range.end;
      else output.push({...range});
    }
    return output;
  }

  function remapAfterEdit(oldText, newText, ranges) {
    oldText = String(oldText || '');
    newText = String(newText || '');
    if (oldText === newText) return normalize(ranges);

    let prefix = 0;
    const prefixLimit = Math.min(oldText.length, newText.length);
    while (prefix < prefixLimit && oldText[prefix] === newText[prefix]) prefix += 1;

    let suffix = 0;
    while (
      suffix < oldText.length - prefix &&
      suffix < newText.length - prefix &&
      oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]
    ) suffix += 1;

    const oldEnd = oldText.length - suffix;
    const newEnd = newText.length - suffix;
    const delta = newText.length - oldText.length;
    const output = [];
    for (const range of normalize(ranges)) {
      if (range.end <= prefix) {
        output.push(range);
        continue;
      }
      if (range.start >= oldEnd) {
        output.push({...range, start: range.start + delta, end: range.end + delta});
        continue;
      }
      if (range.start < prefix) output.push({...range, end: prefix});
      if (range.end > oldEnd) {
        output.push({
          ...range,
          start: Math.max(range.start, oldEnd) + delta,
          end: range.end + delta,
        });
      }
    }
    return normalize(output);
  }

  return {remapAfterEdit};
});
