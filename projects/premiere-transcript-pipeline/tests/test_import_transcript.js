const assert = require('assert');
const { importTranscript } = require('../uxp/import-transcript');
// Model the host lifetime constraint that caused the observed invalid-parameter failure.
let locked = false, imports = 0, actions = 0;
const clip = {};
const ppro = { Transcript: {
  importFromJSON(json) { assert(locked); imports++; return { data: JSON.parse(json) }; },
  createImportTextSegmentsAction(segments, target) { assert(locked); assert.strictEqual(target, clip); return { segments }; }
}};
const project = {
  lockedAccess(fn) { locked = true; try { fn(); } finally { locked = false; } },
  executeTransaction(fn, label) { assert(locked); assert(label); fn({ addAction(action) { assert(locked); assert(action.segments); actions++; } }); return true; }
};
const input = { language: 'ja-jp' };
importTranscript(ppro, project, clip, input, 'Speaker change');
importTranscript(ppro, project, clip, input, 'Repeat');
assert.strictEqual(imports, 2); assert.strictEqual(actions, 2);
assert.deepStrictEqual(input, { language: 'ja-jp' });
assert.throws(() => importTranscript(ppro, { ...project, executeTransaction() { return false; } }, clip, input, 'Rejected'), /rejected/);
assert.throws(() => importTranscript(ppro, project, clip, input, ''), /label/);
console.log('Import lifetime, fresh creation, rejection and label checks passed');
