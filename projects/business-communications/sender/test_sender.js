const queue = [];
const config = {TEST_BOT_ID:'test-bot', TEST_SELF_LINE_ID:'U' + '1'.repeat(32),
  TEST_SHEET_ID:'test-sheet', SEND_APPROVAL_SECRET:'approval-secret', LINE_TEST_ACCESS_TOKEN:'test-token'};
const now = 1800000000000;
let generated = 0, locked = false, pushCode = 200, pushRequest = null, throwPush = false;
let botId = config.TEST_BOT_ID, usage = 0;
const Utilities = {getUuid: () => 'id-' + ++generated, base64Encode: value => value,
  computeHmacSha256Signature: (text, secret) => secret + '|' + text, Charset: {UTF_8:'UTF-8'}};
const LockService = {getScriptLock: () => ({
  tryLock: () => {assert(!locked, 'nested lock'); locked = true; return true;},
  releaseLock: () => {locked = false;},
})};
const Sheets = {Spreadsheets:{Values:{
  get: () => ({values: queue.map(row => row.slice())}),
  append: (body, id, range, options) => {
    assert(locked && options.valueInputOption === 'RAW', 'append missed lock or RAW');
    queue.push(body.values[0].slice());
  },
  update: (body, id, range, options) => {
    assert(locked && options.valueInputOption === 'RAW', 'update missed lock or RAW');
    queue[Number(range.split('!A')[1]) - 2] = body.values[0].slice();
  },
}}};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const UrlFetchApp = {fetch: (url, options) => {
  assert(!locked, 'network call under lock');
  let code = 200, body;
  if (url.endsWith('/info')) body = {userId:botId};
  if (url.endsWith('/quota')) body = {type:'limited',value:200};
  if (url.endsWith('/consumption')) body = {totalUsage:usage};
  if (url.endsWith('/push')) {
    pushRequest = options;
    if (throwPush) throw new Error('timeout');
    code = pushCode;
  }
  return {getResponseCode: () => code, getContentText: () => JSON.stringify(body),
    getAllHeaders: () => code === 409 ? {'X-Line-Accepted-Request-Id':'prior-request'} : {'X-Line-Request-Id':'request'}};
}};

const input = {environment:'test', recipient:config.TEST_SELF_LINE_ID, type:'text', text:'確認用',
  notBefore:now + 1, idempotencyKey:'repeat'};
const enqueue = (data, client = 'client') => senderLock_(() => enqueueSend_(config, client, data, now));
const claim = (at = now + 10) => senderLock_(() => claimSend_(config, at));
const finish = (job, result, at = now + 20) => senderLock_(() => finishSend_(config, job, result, at));
const approve = row => { row[SEND_APPROVAL] = senderApproval_(config, row); row[SEND_STATE] = '送信待ち'; };

assert(!enqueue({...input, recipient:'wrong'}).ok && !enqueue({...input, environment:'production'}).ok, 'allowed an unverified recipient');
assert(!enqueue({...input, type:'reply'}).ok && !enqueue({...input, text:'https://example.test/private'}).ok, 'bypassed type or link checks');
const queued = enqueue(input);
assert(queued.ok && enqueue(input).sendId === queued.sendId && queue.length === 1, 'duplicate enqueued');
assert(enqueue({...input, text:'edited'}).error === 'idempotency_conflict', 'accepted conflicting duplicate');
assert(claim() === null, 'unapproved job claimed');
approve(queue[0]);
assert(claim(now) === null, 'sent before reservation');
queue[0][3] = queue[0][3].replace('確認用', '別の本文');
assert(claim() === null && queue[0][SEND_STATE] === '承認待ち', 'edited approval survived');
approve(queue[0]);
const other = enqueue({...input, idempotencyKey:'second'});
approve(queue[1]);
const job = claim();
assert(job.id === queued.sendId && claim() === null, 'parallel worker claimed a job while busy');
const blockedLink = pushTestMessage_(config, {...job, body:{...job.body, type:'drive_link', fileId:'mock-unavailable'}});
assert(blockedLink.status === '条件待ち' && !pushRequest, 'unverified link sent');
botId = 'production-bot';
assert(pushTestMessage_(config, job).status === '条件待ち' && !pushRequest, 'wrong bot credentials used');
botId = config.TEST_BOT_ID;
usage = 20;
assert(pushTestMessage_(config, job).status === '上限保留' && !pushRequest, 'ignored quota');
usage = 0;
pushCode = 503;
finish(job, pushTestMessage_(config, job));
assert(queue[0][SEND_STATE] === '再試行待ち', 'temporary failure not scheduled');
assert(pushRequest.headers['X-Line-Retry-Key'] === job.id, 'unstable retry key');
const later = claim(now + 120000);
assert(later.id === job.id && later.owner !== job.owner, 'retry claim mismatch');
pushCode = 409;
const accepted = pushTestMessage_(config, later);
finish(later, accepted, now + 120001);
assert(queue[0][SEND_STATE] === 'API受付成功' && accepted.requestId === 'prior-request', 'duplicate response not reconciled');
const second = claim(now + 130000);
assert(second.id === other.sendId, 'accepted message resent');
throwPush = true;
assert(pushTestMessage_(config, second).status === '結果不明' && !pushTestMessage_(config, second).retry,
  'ambiguous timeout would retry automatically');
assert(claim(now + 600000) === null && queue[1][SEND_STATE] === '結果不明', 'expired lease retransmitted');
print('Sender tests passed');
