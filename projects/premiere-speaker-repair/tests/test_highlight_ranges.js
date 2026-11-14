const assert = require('assert');
const {remapAfterEdit} = require('../speaker_repair/transcript_static/highlight-ranges.js');

const base = '今日はACMEとDELTAの話です';
const ranges = [
  {start: 3, end: 7, kind: 'reference'},
  {start: 8, end: 13, kind: 'whisper'},
];

assert.deepStrictEqual(
  remapAfterEdit(base, 'こんにちは。' + base, ranges),
  [
    {start: 9, end: 13, kind: 'reference'},
    {start: 14, end: 19, kind: 'whisper'},
  ],
  'ハイライト前へ入力しても両方の色を維持する',
);

assert.deepStrictEqual(
  remapAfterEdit(base, '今日はACMEとDELTAのお話です', ranges),
  ranges,
  'ハイライト外の編集で色を消さない',
);

assert.deepStrictEqual(
  remapAfterEdit(base, '今日はACMXとDELTAの話です', ranges),
  [
    {start: 3, end: 6, kind: 'reference'},
    {start: 8, end: 13, kind: 'whisper'},
  ],
  '手入力と重なった文字だけ色を外す',
);

console.log('highlight range tests: OK');
