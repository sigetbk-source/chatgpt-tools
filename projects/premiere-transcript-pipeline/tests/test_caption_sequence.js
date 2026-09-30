const assert = require('assert');
const { runRequest } = require('../uxp/workflow-bridge');
const tick = seconds => ({ seconds });
const media = '/media/source.mp3';
const clip = { name: 'source.mp3', getId: () => 'clip-id',
  getMediaFilePath: async () => media,
  getInPoint: async () => tick(0), getOutPoint: async () => tick(300) };
let sequences = [], saves = 0, created = 0;
const trackItem = { getProjectItem: async () => clip, getStartTime: async () => tick(0),
  getInPoint: async () => tick(0), getEndTime: async () => tick(300), getOutPoint: async () => tick(300) };
const project = { path: '/project.prproj', getRootItem: async () => ({ getItems: async () => [clip] }),
  getSequences: async () => sequences, setActiveSequence: async () => true,
  save: async () => { saves++; return true; },
  createSequenceFromMedia: async (name, items) => {
    created++; assert.deepStrictEqual(items, [clip]);
    const seq = { guid: `seq-${created}`, name, getCaptionTrackCount: async () => 0,
      getAudioTrackCount: async () => 1, getVideoTrackCount: async () => 0,
      getAudioTrack: async () => ({ getTrackItems: async () => [trackItem] }),
      getEndTime: async () => tick(300) };
    sequences.push(seq); return seq;
  } };
const ppro = { Project: { getActiveProject: async () => project },
  FolderItem: { cast: x => { if (!x.getItems) throw Error('not folder'); return x; } },
  ClipProjectItem: { cast: x => x },
  Constants: { MediaType: { AUDIO: 'audio' }, TrackItemType: { CLIP: 1 } } };
const payload = { projectPath: '/project.prproj', mediaPath: media,
  sequenceName: '字幕自動配置テスト_未編集_20260926', expectedDurationSeconds: 300 };
const request = p => ({ version: 1, nonce: 'b'.repeat(32),
  operation: 'prepare_caption_test_sequence', payload: p, deadlineMs: 1100 });
(async () => {
  await assert.rejects(runRequest(request({...payload,projectPath:'/wrong'}),{ppro,now:()=>1000}), /Wrong project/);
  assert.strictEqual(created, 0);
  const trimmed = clip.getInPoint; clip.getInPoint = async () => tick(5);
  await assert.rejects(runRequest(request(payload),{ppro,now:()=>1000}), /trim/);
  clip.getInPoint = trimmed; assert.strictEqual(created, 0);
  const normalIn = clip.getInPoint, normalOut = clip.getOutPoint;
  clip.getInPoint = async () => tick(-400000);
  clip.getOutPoint = async () => tick(-400000);
  const result = await runRequest(request(payload),{ppro,now:()=>1000});
  clip.getInPoint = normalIn; clip.getOutPoint = normalOut;
  assert.strictEqual(result.state, 'sequence_created_verified');
  assert.strictEqual(result.sourceTimeVerified, true);
  assert.strictEqual(created, 1); assert.strictEqual(saves, 1);
  await assert.rejects(runRequest(request(payload),{ppro,now:()=>1000}), /already exists/);
  assert.strictEqual(created, 1);
  const originalCreate = project.createSequenceFromMedia;
  project.createSequenceFromMedia = async (...args) => {
    const seq = await originalCreate(...args);
    seq.getAudioTrackCount = async () => 2;
    return seq;
  };
  const stereo = await runRequest(request({...payload,sequenceName:'stereo source'}),{ppro,now:()=>1000});
  assert.strictEqual(stereo.audioItems, 2);
  console.log('Caption test sequence: wrong target, source trim, full readback, idempotent name guard passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
