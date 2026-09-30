const { importTranscript } = require('./import-transcript');

function equal(left, right, path = 'root') {
  if (typeof left === 'number' && typeof right === 'number') {
    if (!Number.isFinite(left) || !Number.isFinite(right) || Math.abs(left - right) > 1e-6) throw Error('Mismatch ' + path);
    return;
  }
  if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') {
    if (left !== right) throw Error('Mismatch ' + path);
    return;
  }
  if (Array.isArray(left) !== Array.isArray(right)) throw Error('Type mismatch ' + path);
  const a = Object.keys(left).sort(), b = Object.keys(right).sort();
  if (JSON.stringify(a) !== JSON.stringify(b)) throw Error('Keys mismatch ' + path);
  for (const key of a) equal(left[key], right[key], path + '.' + key);
}

async function resolveTarget(ppro, expected) {
  for (const key of ['projectPath', 'clipName', 'mediaPath']) {
    if (typeof expected[key] !== 'string' || !expected[key]) throw Error('Missing target ' + key);
  }
  const same = (a, b) => typeof a === 'string' && a.normalize('NFC') === b.normalize('NFC');
  const project = await ppro.Project.getActiveProject();
  if (!project || !same(project.path, expected.projectPath)) throw Error('Wrong project');
  if (expected.projectGuid && String(project.guid) !== String(expected.projectGuid)) throw Error('Wrong project GUID');
  const matches = [];
  const visit = async item => {
    if (same(item.name, expected.clipName)) {
      let clip = null;
      try { clip = ppro.ClipProjectItem.cast(item); } catch (_) { /* bin */ }
      if (clip && same(await clip.getMediaFilePath(), expected.mediaPath) &&
          (!expected.itemId || String(clip.getId()) === String(expected.itemId))) matches.push(clip);
    }
    let folder = null;
    try { folder = ppro.FolderItem.cast(item); } catch (_) { /* clip */ }
    if (folder && typeof folder.getItems === 'function')
      for (const child of await folder.getItems()) await visit(child);
  };
  await visit(await project.getRootItem());
  if (matches.length !== 1) throw Error('Missing or ambiguous clip/media');
  return { project, clip: matches[0] };
}

/** persistBackup must durably save the full transcript and target, and return a path. */
async function writeback({ ppro, expected, candidate, expectedReadback, expectedBefore, persistBackup, label, beforeImport }) {
  if (typeof persistBackup !== 'function' || !expectedBefore || !expectedReadback) throw Error('Backup and expectations required');
  if (typeof label !== 'string' || !label.trim()) throw Error('Undo label required');
  let target = await resolveTarget(ppro, expected);
  const read = async clip => JSON.parse(await ppro.Transcript.exportToJSON(clip));
  const backup = await read(target.clip);
  equal(backup, expectedBefore);
  const backupPath = await persistBackup({ target: { ...expected }, transcript: JSON.parse(JSON.stringify(backup)) });
  if (typeof backupPath !== 'string' || !backupPath) throw Error('Backup not persisted');
  target = await resolveTarget(ppro, expected);
  equal(await read(target.clip), backup);
  if (beforeImport) beforeImport();
  let attempted = false;
  try {
    attempted = true;
    importTranscript(ppro, target.project, target.clip, candidate, label);
    equal(await read(target.clip), expectedReadback);
    if (!await target.project.save()) throw Error('Save failed');
    equal(await read(target.clip), expectedReadback);
    return { imported: true, verified: true, saved: true, backupPath };
  } catch (cause) {
    const recovery = { restored: false, saved: false };
    if (attempted) {
      try {
        target = await resolveTarget(ppro, expected);
        importTranscript(ppro, target.project, target.clip, backup, label + ': restore backup');
        equal(await read(target.clip), backup);
        recovery.restored = true;
        recovery.saved = Boolean(await target.project.save());
        if (!recovery.saved) throw Error('Recovery save failed');
      } catch (error) { recovery.error = String(error); }
    }
    const error = Error('Writeback failed: ' + String(cause));
    error.result = { imported: false, saved: false, backupPath, recovery };
    throw error;
  }
}
module.exports = { writeback, resolveTarget, equal };
