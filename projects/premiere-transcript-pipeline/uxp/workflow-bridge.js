// Only these operations are accepted. Request files never contain executable code.
const { writeback, resolveTarget, equal } = require('./safe-writeback');
const { importTranscript } = require('./import-transcript');

const CAPABILITIES = Object.freeze({
  transcriptSnapshot: true,
  transcriptWriteback: true,
  srtAssetImport: true,
  sourceTimeTestSequence: true,
  srtTrackPlacement: false // Placement is handled by the separate CEP adapter.
});

function assertRequest(request, now = Date.now()) {
  if (!request || request.version !== 1 ||
      !/^[a-f0-9]{32}$/.test(request.nonce || '') ||
      !['capabilities', 'selected_target', 'snapshot', 'apply_transcript', 'import_srt', 'caption_track_snapshot', 'prepare_caption_test_sequence'].includes(request.operation) ||
      !Number.isFinite(request.deadlineMs) || request.deadlineMs < now ||
      request.deadlineMs > now + 120000) throw Error('Invalid or expired bridge request');
}

async function findMedia(ppro, item, mediaPath, matches = []) {
  let clip = null;
  try { clip = ppro.ClipProjectItem.cast(item); } catch (_) {}
  if (clip && typeof clip.getMediaFilePath === 'function' &&
      (await clip.getMediaFilePath()).normalize('NFC') === mediaPath.normalize('NFC')) matches.push(clip);
  let folder = null;
  try { folder = ppro.FolderItem.cast(item); } catch (_) {}
  if (folder && typeof folder.getItems === 'function')
    for (const child of await folder.getItems()) await findMedia(ppro, child, mediaPath, matches);
  return matches;
}

async function prepareCaptionTestSequence(request, ppro, now) {
  const p = request.payload || {};
  const same = (a, b) => typeof a === 'string' && typeof b === 'string' &&
    a.normalize('NFC') === b.normalize('NFC');
  if (!p.projectPath || !p.mediaPath || !p.mediaPath.toLowerCase().endsWith('.mp3') ||
      typeof p.sequenceName !== 'string' || !p.sequenceName.trim() ||
      typeof p.expectedDurationSeconds !== 'number' || !Number.isFinite(p.expectedDurationSeconds) ||
      p.expectedDurationSeconds <= 0) throw Error('Exact MP3 target, sequence name, and duration required');
  const project = await ppro.Project.getActiveProject();
  if (!project || !same(project.path, p.projectPath)) throw Error('Wrong project');
  const existing = await project.getSequences();
  if (existing.some(seq => same(seq.name, p.sequenceName))) throw Error('Sequence name already exists; inspect the prior result');
  const root = await project.getRootItem();
  const matches = await findMedia(ppro, root, p.mediaPath);
  if (matches.length !== 1) throw Error('Missing or ambiguous source media');
  const clip = matches[0];
  const tolerance = 0.5;
  const mediaType = ppro.Constants.MediaType.AUDIO;
  const inPoint = await clip.getInPoint(mediaType), outPoint = await clip.getOutPoint(mediaType);
  // Premiere returns -400000s for both unset source marks on this 25.6 host.
  // Full duration is still established independently from the new timeline.
  const unmarked = inPoint && outPoint &&
    inPoint.seconds === -400000 && outPoint.seconds === -400000;
  if (!inPoint || !outPoint || !Number.isFinite(inPoint.seconds) || !Number.isFinite(outPoint.seconds) ||
      (!unmarked && (Math.abs(inPoint.seconds) > 0.001 ||
      Math.abs(outPoint.seconds - p.expectedDurationSeconds) > tolerance)))
    throw Error('Source item has a trim or unexpected duration: in=' +
      String(inPoint && inPoint.seconds) + 's out=' + String(outPoint && outPoint.seconds) +
      's expected=' + String(p.expectedDurationSeconds) + 's');
  if (now() > request.deadlineMs) throw Error('Request expired before sequence creation');
  const sequence = await project.createSequenceFromMedia(p.sequenceName, [clip], root);
  if (!sequence || !sequence.guid) throw Error('Sequence creation failed');
  const result = { state: 'sequence_created_pending_verification', projectPath: project.path,
    sequenceGuid: String(sequence.guid), sequenceName: sequence.name, mediaPath: p.mediaPath };
  try {
    if (!same(sequence.name, p.sequenceName)) throw Error('Created sequence name mismatch');
    if (await sequence.getCaptionTrackCount() !== 0) throw Error('New sequence contains a caption track');
    const audioCount = await sequence.getAudioTrackCount();
    const videoCount = await sequence.getVideoTrackCount();
    const items = [];
    for (let i = 0; i < audioCount; i++) {
      const track = await sequence.getAudioTrack(i);
      items.push(...await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false));
    }
    for (let i = 0; i < videoCount; i++) {
      const track = await sequence.getVideoTrack(i);
      if ((await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false)).length)
        throw Error('Unexpected video items in audio test sequence');
    }
    if (items.length < 1 || items.length > 2) throw Error('Expected one or two source audio channels');
    const sequenceEnd = await sequence.getEndTime();
    if (!sequenceEnd || Math.abs(sequenceEnd.seconds - p.expectedDurationSeconds) > tolerance)
      throw Error('Sequence duration mismatch');
    for (const item of items) {
      const sourceItem = ppro.ClipProjectItem.cast(await item.getProjectItem());
      const start = await item.getStartTime(), itemIn = await item.getInPoint();
      const end = await item.getEndTime(), itemOut = await item.getOutPoint();
      if (!same(await sourceItem.getMediaFilePath(), p.mediaPath) ||
          (typeof clip.getId === 'function' && typeof sourceItem.getId === 'function' &&
            String(sourceItem.getId()) !== String(clip.getId())) ||
          !start || !itemIn || !end || !itemOut ||
          Math.abs(start.seconds) > 0.001 || Math.abs(itemIn.seconds) > 0.001 ||
          Math.abs(end.seconds - p.expectedDurationSeconds) > tolerance ||
          Math.abs(itemOut.seconds - p.expectedDurationSeconds) > tolerance ||
          Math.abs(sequenceEnd.seconds - end.seconds) > tolerance)
        throw Error('Source-time clip readback mismatch');
    }
    if (!await project.setActiveSequence(sequence)) throw Error('Could not activate test sequence');
    if (!await project.save()) throw Error('Test sequence save failed');
    const saved = (await project.getSequences()).filter(seq => String(seq.guid) === result.sequenceGuid);
    if (saved.length !== 1 || !same(saved[0].name, p.sequenceName)) throw Error('Saved sequence readback mismatch');
    return { ...result, state: 'sequence_created_verified', saved: true,
      sourceTimeVerified: true, captionTrackCount: 0,
      durationSeconds: sequenceEnd.seconds, audioItems: items.length };
  } catch (cause) {
    const error = Error('Test sequence verification failed: ' + String(cause));
    error.result = { ...result, needsManualReview: true };
    throw error;
  }
}

async function firstImport(request, { ppro, persistProjectBackup, now }) {
  const p = request.payload;
  if (p.expectedBefore !== null || p.allowFirstImport !== true ||
      typeof p.expectedExportError !== 'string' || !p.expectedExportError ||
      !p.candidate || !p.expectedReadback || typeof persistProjectBackup !== 'function')
    throw Error('Unexportable baseline requires a project backup');
  const target = await resolveTarget(ppro, p.target);
  const verifyUnavailable = async () => {
    let failure = '';
    try {
      const exported = await ppro.Transcript.exportToJSON(target.clip);
      if (exported) throw Error('A transcript now exists');
      failure = 'empty export';
    } catch (error) {
      if (String(error) === 'Error: A transcript now exists') throw error;
      failure = String(error);
    }
    if (failure !== p.expectedExportError) throw Error('Transcript export state changed');
  };
  await verifyUnavailable();
  if (!await target.project.save()) throw Error('Project baseline save failed');
  const backupPath = await persistProjectBackup(request.nonce, target.project.path);
  if (typeof backupPath !== 'string' || !backupPath) throw Error('Project backup was not persisted');
  await verifyUnavailable();
  if (now() > request.deadlineMs) throw Error('Request expired before import');
  let attempted = false;
  try {
    attempted = true;
    importTranscript(ppro, target.project, target.clip, p.candidate, 'Import reviewed first transcript');
    equal(JSON.parse(await ppro.Transcript.exportToJSON(target.clip)), p.expectedReadback);
    if (!await target.project.save()) throw Error('Save failed');
    equal(JSON.parse(await ppro.Transcript.exportToJSON(target.clip)), p.expectedReadback);
    return { imported: true, verified: true, saved: true, backupPath,
      target: { ...p.target }, state: 'applied_verified',
      baseline: 'project_backup' };
  } catch (cause) {
    const error = Error('First import failed: ' + String(cause));
    error.result = { imported: false, saved: false, backupPath,
      recovery: attempted ? { needsManualRecovery: true, projectBackupPath: backupPath,
        reason: 'Premiere 25 has no verified clear-transcript API' } : null };
    throw error;
  }
}

async function runRequest(request, { ppro, persistBackup, persistProjectBackup, readSrt, now = Date.now }) {
  assertRequest(request, now());
  if (request.operation === 'capabilities') return { capabilities: CAPABILITIES };
  if (request.operation === 'prepare_caption_test_sequence')
    return prepareCaptionTestSequence(request, ppro, now);
  if (request.operation === 'caption_track_snapshot') {
    const p = request.payload || {};
    if (typeof p.projectPath !== 'string' || !p.projectPath) throw Error('Project path required');
    const project = await ppro.Project.getActiveProject();
    if (!project || project.path.normalize('NFC') !== p.projectPath.normalize('NFC')) throw Error('Wrong project');
    const sequence = await project.getActiveSequence();
    if (!sequence || !sequence.guid || !sequence.name) throw Error('No active sequence');
    if (p.sequenceGuid && String(sequence.guid) !== String(p.sequenceGuid)) throw Error('Wrong sequence');
    const captionTrackCount = await sequence.getCaptionTrackCount();
    if (!Number.isInteger(captionTrackCount) || captionTrackCount < 0) throw Error('Caption track count unavailable');
    return { projectPath: project.path, sequenceGuid: String(sequence.guid),
      sequenceName: sequence.name, captionTrackCount };
  }
  if (request.operation === 'selected_target') {
    const project = await ppro.Project.getActiveProject();
    if (!project || !project.path) throw Error('No saved active project');
    const selection = await ppro.ProjectUtils.getSelection(project);
    const items = await selection.getItems();
    if (items.length !== 1) throw Error('Select exactly one source clip in the Project panel');
    let clip = null;
    try { clip = ppro.ClipProjectItem.cast(items[0]); } catch (_) {}
    if (!clip) throw Error('Selection is not a source clip');
    const target = { projectPath: project.path, clipName: clip.name,
      mediaPath: await clip.getMediaFilePath(),
      ...(project.guid ? { projectGuid: String(project.guid) } : {}),
      ...(typeof clip.getId === 'function' ? { itemId: String(clip.getId()) } : {}) };
    // Reject ambiguous duplicate items even if the selected one is known.
    await resolveTarget(ppro, target);
    return { target, capabilities: CAPABILITIES };
  }
  if (request.operation === 'import_srt') {
    const p = request.payload || {};
    if (typeof p.projectPath !== 'string' || !p.projectPath ||
        typeof p.srtPath !== 'string' || !p.srtPath.toLowerCase().endsWith('.srt') ||
        typeof p.expectedText !== 'string' || !p.expectedText.trim() ||
        typeof readSrt !== 'function') throw Error('Incomplete SRT import request');
    const project = await ppro.Project.getActiveProject();
    if (!project || project.path.normalize('NFC') !== p.projectPath.normalize('NFC')) throw Error('Wrong project');
    if (await readSrt(p.srtPath) !== p.expectedText) throw Error('SRT content mismatch');
    const root = await project.getRootItem();
    if ((await findMedia(ppro, root, p.srtPath)).length) throw Error('SRT already imported');
    if (now() > request.deadlineMs) throw Error('Request expired before import');
    const imported = await project.importFiles([p.srtPath], true, root, false);
    if (!imported) throw Error('SRT import rejected');
    const matches = await findMedia(ppro, await project.getRootItem(), p.srtPath);
    if (matches.length !== 1) throw Error('SRT import readback missing or ambiguous');
    if (!await project.save()) throw Error('SRT import save failed');
    return { state: 'asset_imported_verified', imported: true, saved: true,
      placed: false, srtPath: p.srtPath, projectPath: p.projectPath };
  }
  const target = await resolveTarget(ppro, request.payload && request.payload.target);
  if (request.operation === 'snapshot') {
    try {
      const raw = await ppro.Transcript.exportToJSON(target.clip);
      if (!raw) return { target: { ...request.payload.target }, transcript: null,
        transcriptAvailable: false, exportError: 'empty export', capabilities: CAPABILITIES };
      const transcript = JSON.parse(raw);
      return { target: { ...request.payload.target }, transcript,
        transcriptAvailable: true, capabilities: CAPABILITIES };
    } catch (error) {
      // The host may have no transcript, or export may have failed for another
      // reason. Do not label it "empty" and do not enable writeback.
      return { target: { ...request.payload.target }, transcript: null,
        transcriptAvailable: false, exportError: String(error), capabilities: CAPABILITIES };
    }
  }
  if (typeof persistBackup !== 'function') throw Error('Backup storage unavailable');
  const p = request.payload;
  if (!p || !p.candidate || !p.expectedReadback) throw Error('Incomplete writeback');
  if (p.allowFirstImport === true) return firstImport(request, { ppro, persistProjectBackup, now });
  if (!p.expectedBefore) throw Error('Expected transcript baseline required');
  // A pending request must still be fresh immediately before the first change.
  const result = await writeback({ ppro, expected: p.target,
    candidate: p.candidate, expectedBefore: p.expectedBefore,
    expectedReadback: p.expectedReadback, persistBackup: data => {
      if (now() > request.deadlineMs) throw Error('Request expired before backup');
      return persistBackup(request.nonce, data);
    }, beforeImport: () => {
      if (now() > request.deadlineMs) throw Error('Request expired before import');
    }, label: 'Apply reviewed transcript' });
  const verified = await resolveTarget(ppro, p.target);
  equal(JSON.parse(await ppro.Transcript.exportToJSON(verified.clip)), p.expectedReadback);
  return { ...result, target: { ...p.target }, state: 'applied_verified' };
}

module.exports = { CAPABILITIES, assertRequest, runRequest };
