/** Low-level 25.6 import primitive. Caller owns target checks, backup and verification. */
function importTranscript(ppro, project, clip, transcript, label) {
  if (typeof label !== 'string' || !label.trim()) throw new Error('An Undo label is required');
  const json = JSON.stringify(transcript);
  let committed = false;
  project.lockedAccess(() => {
    // Keep TextSegments creation in the same scope as the Action: verified on 25.6.6.
    const segments = ppro.Transcript.importFromJSON(json);
    const action = ppro.Transcript.createImportTextSegmentsAction(segments, clip);
    committed = project.executeTransaction(compound => compound.addAction(action), label);
  });
  if (!committed) throw new Error('Transcript transaction rejected');
}
module.exports = { importTranscript };
