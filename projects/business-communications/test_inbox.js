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
    getName: () => path === 'root' ? '業務連絡管理（テスト専用）' : path.split('/').pop(),
    getSharingAccess: () => 'private',
    getFoldersByName: name => iterator(folderContents.has(path + '/' + name) ? [mockFolder(path + '/' + name)] : []),
    createFolder: name => mockFolder(path + '/' + name),
    getFilesByName: name => iterator(contents.has(name) ? [contents.get(name)] : []),
    createFile: (blob, text) => {
      const name = typeof blob === 'string' ? blob : blob.name;
      check(!contents.has(name), 'overwrote existing file');
      const id = 'file-' + (++filesWritten);
      const file = {getId: () => id, getUrl: () => 'https://example.test/' + id,
        getBlob: () => ({getDataAsString: () => text}), setContent: value => { text = value; }};
      contents.set(name, file);
      return file;
    },
  };
}

const config = {ENVIRONMENT: 'test', TEST_ROOT_ID: 'root', TEST_BOT_ID: 'test-bot',
  WORKER_SECRET: 'synthetic'.repeat(5), TEST_SHEET_ID: 'sheet', ARCHIVE_ID: 'archive',
  ATTACHMENTS_ID: 'attachments', LINE_TEST_ACCESS_TOKEN: 'mock'};
const MimeType = {PLAIN_TEXT: 'text/plain'};
const DriveApp = {getFolderById: mockFolder, Access: {PRIVATE: 'private'}, getFileById: id =>
  [...folderContents.values()].flatMap(folder => [...folder.values()]).find(file => file.getId() === id)};
const ContentService = {createTextOutput: value => ({setMimeType: () => JSON.parse(value)}), MimeType: {JSON: 'json'}};
const Utilities = {
  formatDate: () => '2026-11', Charset: {UTF_8: 'UTF-8'},
  base64Encode: value => value, computeHmacSha256Signature: () => 'a'.repeat(44),
  base64Decode: value => value, newBlob: value => ({getDataAsString: () => value}),
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
    if (range.endsWith('D2:D')) return {values: rows.map(row => [row[COL['受信日時']]])};
    if (range.endsWith('P2:P')) return {values: rows.map(row => [row[COL['保存状態']]])};
    if (range.endsWith('A1:' + INBOX_LAST_COLUMN)) return {values: [HEADERS, ...rows.map(rendered)]};
    if (range.endsWith('A2:' + INBOX_LAST_COLUMN)) return {values: rows.map(rendered)};
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
  batchUpdate: ({valueInputOption, data}) => {
    check(locked && valueInputOption === 'RAW', 'routing backfill must use lock and RAW');
    data.forEach(({range, values}) => {
      const number = Number(range.match(/!AB(\d+):/)[1]);
      rows[number - 2].splice(LEGACY_HEADERS.length, 4, ...values[0]);
    });
  },
}}};

const now = new Date();
const textEvent = {webhookEventId: 'event1', type: 'message', timestamp: 1700000000000,
  replyToken: 'mock-token', source: {type: 'group', groupId: 'group'}, message: {type: 'text', text: '=1+2'}};
const fileEvent = {webhookEventId: 'event2', type: 'message', message: {type: 'file', id: '1234', fileName: 'same.pdf'}};
const secondFile = {webhookEventId: 'event3', type: 'message', message: {type: 'file', id: '1235', fileName: 'same.pdf'}};
const unknown = {webhookEventId: 'event4', type: 'unknown'};
const sample = [textEvent, fileEvent, secondFile, unknown].map(event => eventRow_(event, now));

const groupId = 'C' + 'a'.repeat(32);
const ownerId = 'U' + 'b'.repeat(32);
const forwarding = {TEST_FORWARD_GROUP_ID: groupId, TEST_FORWARD_OWNER_ID: ownerId};
const received = source => eventRow_({...textEvent, source}, now, forwarding);
check(validInboxHeader_(HEADERS) && validInboxHeader_([...LEGACY_HEADERS, '', '', '', '']) &&
  !validInboxHeader_([...LEGACY_HEADERS, '既存の別用途', '', '', '']), 'append-only schema migration');
check(received({type: 'user', userId: ownerId})[COL['受信経路詳細']] === '公式直接', 'direct receipt');
check(received({type: 'group', groupId: 'other', userId: ownerId})[COL['受信経路詳細']] === '一般グループ', 'generic group');
check(received({type: 'group', groupId})[COL['転送受付']] === '未認可', 'missing author must not authorize');
check(received({type: 'group', groupId, userId: 'someone-else'})[COL['転送受付']] === '未認可', 'unverified group member');
const forwarded = received({type: 'group', groupId, userId: ownerId});
check(forwarded[COL['受信経路詳細']] === '専用グループ転送' && forwarded[COL['転送受付']] === '認可済み' &&
  forwarded[COL['LINE送信元種別']] === 'group' && forwarded[COL['LINEグループID']] === groupId &&
  forwarded[COL['送信者ID']] === ownerId && forwarded[COL['LINE発生日時']] === new Date(textEvent.timestamp).toISOString() &&
  forwarded[COL['受信日時']] === now.toISOString(), 'forwarding metadata preserves webhook and receipt facts');

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
const nonce = '1'.repeat(32);
const probe = {kind: 'receiver_health', timestamp: String(Math.floor(Date.now() / 1000)), signature: 'a'.repeat(44),
  payload: JSON.stringify({kind: 'receiver_health', nonce})};
const healthConfig = {...config, RECEIVER_HEALTH_SECRET: 'health-key'.repeat(4)};
check(!receiverHealth_(config, probe).ok &&
  !receiverHealth_({...healthConfig, RECEIVER_HEALTH_SECRET: config.WORKER_SECRET}, probe).ok &&
  !receiverHealth_(healthConfig, {...probe, signature: 'b'.repeat(44)}).ok, 'require independent probe authorization');
const healthy = receiverHealth_(healthConfig, probe);
check(healthy.ok && healthy.nonce === nonce && healthy.pending === 0 && rows.length === 4, 'probe writes outside inbox');
const nextNonce = '2'.repeat(32);
rows[1][COL['保存状態']] = '再試行待ち';
rows[1][COL['受信日時']] = new Date(Date.now() - 3600000).toISOString();
const backlog = receiverHealth_(healthConfig, {...probe, payload: JSON.stringify({kind: 'receiver_health', nonce: nextNonce})});
check(backlog.ok && backlog.nonce === nextNonce && backlog.pending === 1 && backlog.oldestPendingSeconds >= 3600 &&
  rows.length === 4 && filesWritten === 7, 'probe uses one isolated marker and measures backlog');
Object.assign(config, forwarding);
rows[0].splice(LEGACY_HEADERS.length);
const oldColumns = JSON.stringify(rows[0]);
check(backfillTestInboxRouting() === 1 && backfillTestInboxRouting() === 0, 'routing migration must be idempotent');
check(JSON.stringify(rows[0].slice(0, LEGACY_HEADERS.length)) === oldColumns &&
  rows[0][COL['受信経路詳細']] === '一般グループ', 'migration reconstructed source without changing original columns');
print('Inbox tests passed');
