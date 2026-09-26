const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(require('path').join(__dirname, '../cep/jsx/host.jsx'), 'utf8');
let created = 0, saves = 0;
const srt = { getMediaPath: () => '/captions.srt' };
const sequence = {sequenceID:'seq-id',name:'source',captionTracks:{numTracks:0},
  createCaptionTrack: (item, start, format) => { assert.strictEqual(item,srt); assert.strictEqual(start,0);
    assert.strictEqual(format,9); created++; return true; }};
const context = { JSON, Error, String, File: function(path) { this.exists = path === '/captions.srt'; },
  Sequence: {CAPTION_FORMAT_SUBTITLE:9},
  app: {project:{path:'/project.prproj',activeSequence:sequence,
    rootItem:{children:{numItems:1,0:srt}},save:()=>{saves++; return true;}}}};
vm.createContext(context); vm.runInContext(source,context);
const payload = {projectPath:'/project.prproj',sequenceGuid:'seq-id',sequenceName:'source',
  srtPath:'/captions.srt',sourceTimeVerified:true,expectedCaptionTrackCount:0};
assert.strictEqual(JSON.parse(context.captionPrepare(JSON.stringify(payload))).ready,true);
assert.strictEqual(JSON.parse(context.captionPlace(JSON.stringify(payload))).created,true);
assert.strictEqual(created,1); assert.strictEqual(saves,2);
assert.strictEqual(JSON.parse(context.captionPlace(JSON.stringify({...payload,sequenceGuid:'wrong'}))).ok,false);
assert.strictEqual(JSON.parse(context.captionPlace(JSON.stringify({...payload,sourceTimeVerified:false}))).ok,false);
sequence.captionTracks.numTracks=1;
assert.strictEqual(JSON.parse(context.captionPlace(JSON.stringify(payload))).ok,false);
assert.strictEqual(created,1);
console.log('CEP caption host identity, source-time tag, empty track, placement/save passed');
