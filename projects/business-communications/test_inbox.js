// Run alongside Code.gs in JavaScriptCore with mock Google and LINE services.
const rows = [];
let filesWritten = 0;
let attachmentDownloads = 0;
let responseCode = 200;
let failCommit = false;
let locked = false;
const folderContents = new Map();

function check(condition, detail) {
  if (!condition) throw new Error(detail);
}

function iterator(items) {
  return {hasNext: () => items.length > 0, next: () => items.shift()};
}

function mockFolder(path) {
  if (!folderContents.has(path)) folderContents.set(path, new Map());
  const contents = folderContents.get(path);
  return {
    getFoldersByName: name => iterator(folderContents.has(path + '/' + name) ? [mockFolder(path + '/' + name)] : []),
    createFolder: name => mockFolder(path + '/' + name),
    getFilesByName: name => iterator(contents.has(name) ? [contents.get(name)] : []),
    createFile: blob => {
      const name = typeof blob === 'string' ? blob : blob.name;
      check(!contents.has(name), 'overwrote existing file');
      const id = 'file-' + (++filesWritten);
      const file = {getId: () => id, getUrl: () => 'https://example.test/' + id};
      contents.set(name, file);
      return file;
    },
  };
}

const config = {ENVIRONMENT: 'test', TEST_ROOT_ID: 'root', TEST_BOT_ID: 'test-bot',
  WORKER_SECRET: 'synthetic'.repeat(5), TEST_SHEET_ID: 'sheet', ARCHIVE_ID: 'archive',
  ATTACHMENTS_ID: 'attachments', LINE_TEST_ACCESS_TOKEN: 'mock'};
const MimeType = {PLAIN_TEXT: 'text/plain'};
const DriveApp = {getFolderById: mockFolder};
const Utilities = {
  formatDate: () => '2026-11', Charset: {UTF_8: 'UTF-8'},
  base64Encode: value => value, computeHmacSha256Signature: () => 'a'.repeat(44),
};
const PropertiesService = {getScriptProperties: () => ({getProperties: () => config})};
const LockService = {getScriptLock: () => ({
  tryLock: () => { check(!locked, 'nested lock'); locked = true; return true; },
  releaseLock: () => { locked = false; },
})};
const UrlFetchApp = {fetch: () => {
  check(!locked, 'LINE content fetch blocked the ledger lock');
  attachmentDownloads++;
  return {getResponseCode: () => responseCode, getBlob: () => ({getContentType: () => 'application/pdf',
    setName: name => ({name})})};
}};
const Sheets = {Spreadsheets: {Values: {
  get: (_, range, options = {}) => {
    const rendered = row => row.map(value => typeof value !== 'number' || options.valueRenderOption === 'UNFORMATTED_VALUE'
      ? value : String(value));
    if (range.endsWith('A2:A')) return {values: rows.map(row => [row[0]])};
    if (range.endsWith('A2:AA')) return {values: rows.map(rendered)};
    const number = Number(range.split('!A')[1].split(':')[0]);
    return {values: [rendered(rows[number - 2])]};
  },
  append: (body, _, range, options) => {
    check(locked && options.valueInputOption === 'RAW', 'ledger writes must use lock and RAW');
    rows.push(...body.values.map(row => row.slice()));
  },
  update: (body, _, range, options) => {
    check(locked && options.valueInputOption === 'RAW', 'ledger writes must use lock and RAW');
    const number = Number(range.split('!A')[1]);
    if (failCommit && body.values[0][COL['保存状態']] === '保存済み') throw new Error('mock sheet outage');
    rows[number - 2] = body.values[0].slice();
  },
}}};

const now = new Date();
const textEvent = {webhookEventId: 'event1', type: 'message', timestamp: 1700000000000,
  replyToken: 'mock-token', source: {type: 'group', groupId: 'group'}, message: {type: 'text', text: '=1+2'}};
const fileEvent = {webhookEventId: 'event2', type: 'message', message: {type: 'file', id: '1234', fileName: 'same.pdf'}};
const secondFile = {webhookEventId: 'event3', type: 'message', message: {type: 'file', id: '1235', fileName: 'same.pdf'}};
const unknown = {webhookEventId: 'event4', type: 'unknown'};
const sample = [textEvent, fileEvent, secondFile, unknown].map(event => eventRow_(event, now));

check(sample[0][COL['本文']] === '=1+2' && sample[0][COL['送信者ID']] === '' &&
  sample[0][COL['会話ID']] === 'group', 'normalization');
check(!sample[0][COL['原文保存待ち']].includes('mock-token'), 'temporary token in persisted event');
withLock_(() => appendEvents_('sheet', [...sample, sample[0]]));
withLock_(() => appendEvents_('sheet', sample));
check(rows.length === 4, 'duplicate event');

responseCode = 202;
processTestInbox();
check(rows[0][COL['保存状態']] === '保存済み' && rows[1][COL['保存状態']] === '再試行待ち' &&
  rows[3][COL['保存状態']] === '保存済み', 'batch processing or retry');
check(attachmentDownloads === 2 && filesWritten === 4, 'initial file/archive counts');
rows[1][COL['次回試行時刻']] = 0;
rows[2][COL['次回試行時刻']] = 0;
responseCode = 200;
failCommit = true;
try { processTestInbox(); } catch (error) { check(error.message === 'mock sheet outage', 'unexpected worker error'); }
check(filesWritten === 5 && rows[1][COL['処理リース']] > Date.now(), 'crash after file creation');
check(withLock_(() => claimInbox_('sheet', Date.now())).row[0] === 'event3', 'leased row claimed twice');
rows[1][COL['処理リース']] = 0;
rows[2][COL['処理リース']] = 0;
failCommit = false;
processTestInbox();
check(filesWritten === 6 && rows[1][COL['保存状態']] === '保存済み' && rows[2][COL['保存状態']] === '保存済み',
  'recovery or same-name files');
check(rows[1][COL['DriveファイルID']] !== rows[2][COL['DriveファイルID']], 'same-name file ID collision');
check(validEnvelope_({timestamp: String(Math.floor(Date.now() / 1000)), payload: 'abc', signature: 'a'.repeat(44)}, 'key', Date.now()),
  'fresh worker signature');
check(!validEnvelope_({timestamp: '1000000000', payload: 'abc', signature: 'a'.repeat(44)}, 'key', Date.now()), 'stale worker signature');
check(!validEnvelope_({timestamp: String(Math.floor(Date.now() / 1000)), payload: 'abc', signature: 'b'.repeat(44)}, 'key', Date.now()),
  'incorrect worker signature');
print('Inbox tests passed');
