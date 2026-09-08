const assert = require('assert');
const { writeback } = require('../uxp/safe-writeback');
const clone = x => JSON.parse(JSON.stringify(x));
const before = { segments: [{ speaker: 'a' }] }, after = { segments: [{ speaker: 'b' }] };
function host(saveResults = [true]) {
  const h = { state: clone(before), imports: 0, backups: 0, saves: 0 };
  const clip = { name: 'clip', getMediaFilePath: async () => '/media' };
  const project = {
    path: '/project', getRootItem: async () => ({ getItems: async () => [clip] }),
    lockedAccess: fn => fn(),
    executeTransaction: fn => { fn({ addAction: a => { h.state = clone(a); h.imports++; } }); return true; },
    save: async () => { h.saves++; return saveResults.shift() ?? false; }
  };
  h.ppro = { Project: { getActiveProject: async () => project }, ClipProjectItem: { cast: x => x },
    Transcript: { exportToJSON: async () => JSON.stringify(h.state), importFromJSON: JSON.parse,
      createImportTextSegmentsAction: s => s } };
  h.args = { ppro: h.ppro, expected: { projectPath: '/project', clipName: 'clip', mediaPath: '/media' },
    candidate: after, expectedReadback: after, expectedBefore: before, label: 'Speaker edit',
    persistBackup: async data => { h.backups++; assert.deepStrictEqual(data.transcript, before); return '/backup'; } };
  return h;
}
(async () => {
  let h = host(); assert.strictEqual((await writeback(h.args)).saved, true);
  assert.strictEqual(h.imports, 1); assert.strictEqual(h.backups, 1);
  for (const key of ['projectPath', 'clipName', 'mediaPath']) {
    h = host(); h.args.expected[key] += '-wrong'; await assert.rejects(writeback(h.args));
    assert.strictEqual(h.imports, 0); assert.strictEqual(h.backups, 0);
  }
  h = host(); h.args.persistBackup = async () => { throw Error('disk full'); };
  await assert.rejects(writeback(h.args)); assert.strictEqual(h.imports, 0);
  h = host([false, true]);
  await assert.rejects(writeback(h.args), e => e.result.imported === false && e.result.recovery.saved === true);
  assert.deepStrictEqual(h.state, before); assert.strictEqual(h.saves, 2);
  h = host([false, false]);
  await assert.rejects(writeback(h.args), e => e.result.recovery.saved === false && !!e.result.recovery.error);
  h = host(); h.args.persistBackup = async () => { h.state = { changed: true }; return '/backup'; };
  await assert.rejects(writeback(h.args)); assert.strictEqual(h.imports, 0);
  console.log('Safe writeback: success, mismatch, backup failure, save failure, recovery failure, stale input passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
