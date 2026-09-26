const assert = require('assert');
const { runRequest } = require('../uxp/workflow-bridge');
const nonce = 'a'.repeat(32);
const target = { projectPath: '/project', clipName: 'clip', mediaPath: '/media' };
const before = { language: 'ja-jp', segments: [] };
const after = { language: 'ja-jp', segments: [{ words: [] }] };
let state = before, changes = 0, saves = 0, backups = 0;
const clip = { name: 'clip', getMediaFilePath: async () => '/media' };
const project = { path: '/project', getRootItem: async () => ({ getItems: async () => [clip] }),
  getActiveSequence: async () => ({guid:'seq-guid',name:'source',getCaptionTrackCount:async()=>0}),
  lockedAccess: fn => fn(), executeTransaction: fn => { fn({ addAction: action => { state = action; changes++; } }); return true; },
  save: async () => { saves++; return true; } };
const ppro = { Project: { getActiveProject: async () => project },
  FolderItem: { cast: x => { if (typeof x.getItems !== 'function') throw Error('not folder'); return x; } },
  ProjectUtils: { getSelection: async () => ({ getItems: async () => [clip] }) },
  ClipProjectItem: { cast: x => x }, Transcript: { exportToJSON: async () => JSON.stringify(state),
    importFromJSON: JSON.parse, createImportTextSegmentsAction: x => x } };
const request = (operation, payload = {}) => ({ version: 1, nonce, operation, payload, deadlineMs: 1100 });
(async () => {
  assert.deepStrictEqual((await runRequest(request('selected_target'), { ppro, now: () => 1000 })).target, target);
  const captions = await runRequest(request('caption_track_snapshot', {projectPath:'/project'}), {ppro,now:()=>1000});
  assert.strictEqual(captions.sequenceGuid, 'seq-guid'); assert.strictEqual(captions.captionTrackCount, 0);
  assert.deepStrictEqual((await runRequest(request('snapshot', { target }), { ppro, now: () => 1000 })).transcript, before);
  await assert.rejects(runRequest(request('apply_transcript', { target, candidate: after,
    expectedBefore: { wrong: true }, expectedReadback: after }),
    { ppro, now: () => 1000, persistBackup: async () => { backups++; return '/backup'; } }));
  assert.strictEqual(changes, 0); assert.strictEqual(backups, 0);
  const payload = { target, candidate: after, expectedBefore: before, expectedReadback: after };
  await assert.rejects(runRequest(request('apply_transcript', payload),
    { ppro, now: () => 1200, persistBackup: async () => '/backup' }), /expired/);
  assert.strictEqual(changes, 0);
  const applied = await runRequest(request('apply_transcript', payload),
    { ppro, now: () => 1000, persistBackup: async () => { backups++; return '/backup'; } });
  assert.strictEqual(applied.state, 'applied_verified');
  assert.strictEqual(backups, 1); assert.strictEqual(changes, 1); assert.strictEqual(saves, 1);
  assert.strictEqual((await runRequest(request('capabilities'), { ppro, now: () => 1000 })).capabilities.srtTrackPlacement, false);
  let srtImported = false;
  project.importFiles = async paths => { assert.deepStrictEqual(paths, ['/captions.srt']); srtImported = true; return true; };
  project.getRootItem = async () => ({ getItems: async () => [clip,
    ...(srtImported ? [{ name: 'captions.srt', getMediaFilePath: async () => '/captions.srt' }] : [])] });
  const srtPayload = { projectPath: '/project', srtPath: '/captions.srt', expectedText: '1\n00:00:00,000 --> 00:00:01,000\nHi\n' };
  await assert.rejects(runRequest(request('import_srt', srtPayload),
    { ppro, now: () => 1000, readSrt: async () => 'wrong' }), /mismatch/);
  assert.strictEqual(srtImported, false);
  const srtResult = await runRequest(request('import_srt', srtPayload),
    { ppro, now: () => 1000, readSrt: async () => srtPayload.expectedText });
  assert.strictEqual(srtResult.state, 'asset_imported_verified');
  assert.strictEqual(srtResult.placed, false);
  await assert.rejects(runRequest(request('import_srt', srtPayload),
    { ppro, now: () => 1000, readSrt: async () => srtPayload.expectedText }), /already imported/);
  // Premiere 25 can fail transcript export before the first import. The
  // baseline marker is explicit, and failure never claims a successful undo.
  state = null;
  ppro.Transcript.exportToJSON = async () => {
    if (state === null) throw Error('No existing transcript');
    return JSON.stringify(state);
  };
  const empty = await runRequest(request('snapshot', { target }), { ppro, now: () => 1000 });
  assert.strictEqual(empty.transcriptAvailable, false);
  assert.strictEqual(empty.exportError, 'Error: No existing transcript');
  let projectBackup;
  const first = { target, candidate: after, expectedBefore: null,
    expectedReadback: after, allowFirstImport: true, expectedExportError: empty.exportError };
  const initial = await runRequest(request('apply_transcript', first), { ppro, now: () => 1000,
    persistBackup: async () => '/unused',
    persistProjectBackup: async (_, path) => { projectBackup = path; return '/project-backup.prproj'; } });
  assert.strictEqual(projectBackup, '/project');
  assert.strictEqual(initial.baseline, 'project_backup');
  state = null;
  let saveAttempt = 0;
  project.save = async () => ++saveAttempt === 1;
  await assert.rejects(runRequest(request('apply_transcript', first), { ppro, now: () => 1000,
    persistBackup: async () => '/unused', persistProjectBackup: async () => '/copy.prproj' }),
    error => error.result.recovery.needsManualRecovery === true &&
      error.result.recovery.projectBackupPath === '/copy.prproj');
  await assert.rejects(runRequest(request('run_js'), { ppro, now: () => 1000 }), /Invalid/);
  console.log('Workflow bridge: selection, snapshot, mismatch, deadline, backup, applied verification passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
