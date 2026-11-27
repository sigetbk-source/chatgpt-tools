const SEND_QUEUE = '送信キュー（テスト）';
const SEND_COLUMNS = ['送信ID', '依頼元', '冪等性キー', '依頼内容', '登録日時', '状態', '承認署名',
  '試行回数', '次回試行', '処理担当', 'リース期限', '最初の試行', '履歴'];
const SEND_STATE = 5, SEND_APPROVAL = 6, SEND_ATTEMPTS = 7, SEND_NEXT = 8, SEND_OWNER = 9,
  SEND_LEASE = 10, SEND_FIRST = 11, SEND_HISTORY = 12;

function senderConfig_() {
  const p = PropertiesService.getScriptProperties().getProperties();
  if (p.ENVIRONMENT !== 'test' || !p.TEST_ROOT_ID || !p.TEST_SHEET_ID || !p.TEST_BOT_ID ||
      !/^U[0-9a-f]{32}$/.test(p.TEST_SELF_LINE_ID || '') || (p.SEND_APPROVAL_SECRET || '').length < 32) {
    throw new Error('sender_setup_required');
  }
  return p;
}

function senderLock_(fn) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) throw new Error('sender_busy');
  try {
    return fn();
  } finally {
    lock.releaseLock();
  }
}

function senderProof_(secret, text) {
  return Utilities.base64Encode(Utilities.computeHmacSha256Signature(text, secret, Utilities.Charset.UTF_8));
}

function senderProofMatches_(expected, actual) {
  if (typeof actual !== 'string' || expected.length !== actual.length) return false;
  let mismatch = 0;
  for (let i = 0; i < expected.length; i++) mismatch |= expected.charCodeAt(i) ^ actual.charCodeAt(i);
  return mismatch === 0;
}

function senderReply_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    const config = senderConfig_();
    const raw = e && e.postData && e.postData.contents;
    if (!raw || raw.length > 24000) return senderReply_({ok: false, error: 'invalid_request'});
    const envelope = JSON.parse(raw);
    const {client, timestamp, payload, signature} = envelope;
    const clients = JSON.parse(config.SEND_CLIENTS_JSON || '{}');
    if (typeof client !== 'string' || !/^[A-Za-z0-9_-]{1,32}$/.test(client) ||
        !Object.prototype.hasOwnProperty.call(clients, client) || typeof clients[client] !== 'string' ||
        clients[client].length < 32 || typeof timestamp !== 'string' || !/^\d{10}$/.test(timestamp) ||
        Math.abs(Date.now() / 1000 - Number(timestamp)) > 120 || typeof payload !== 'string' ||
        !senderProofMatches_(senderProof_(clients[client], client + '.' + timestamp + '.' + payload), signature)) {
      return senderReply_({ok: false, error: 'unauthorized'});
    }
    const request = JSON.parse(Utilities.newBlob(Utilities.base64Decode(payload)).getDataAsString('UTF-8'));
    if (request.action === 'enqueue') {
      return senderReply_(senderLock_(() => enqueueSend_(config, client, request, Date.now())));
    }
    if (request.action === 'status') {
      const record = senderRows_(config).find(row => row[0] === request.sendId && row[1] === client);
      if (record) return senderReply_({ok: true, sendId: record[0], status: record[SEND_STATE], attempts: record[SEND_ATTEMPTS]});
      return senderReply_({ok: false, error: 'not_found'});
    }
    return senderReply_({ok: false, error: 'invalid_action'});
  } catch (_) {
    return senderReply_({ok: false, error: 'request_rejected'});
  }
}

function normalizeSend_(config, request) {
  const {type, recipient, text, fileId, notBefore, caseId} = request;
  if (request.environment !== 'test' || recipient !== config.TEST_SELF_LINE_ID ||
      !['text', 'drive_link'].includes(type) || typeof text !== 'string' || !text.trim() ||
      text.length > 5000 || (type === 'text' && /https?:\/\//i.test(text)) ||
      (type === 'drive_link' && (!/^[A-Za-z0-9_-]{10,150}$/.test(fileId || '') || /https?:\/\//i.test(text))) ||
      !Number.isSafeInteger(notBefore) || notBefore < 0 ||
      (caseId !== undefined && (typeof caseId !== 'string' || caseId.length > 150))) return null;
  return {environment: 'test', channel: config.TEST_BOT_ID, method: 'push', contact: 'owner', recipient,
    type, text, fileId: type === 'drive_link' ? fileId : '', notBefore, caseId: caseId || ''};
}

function senderRows_(config) {
  return Sheets.Spreadsheets.Values.get(config.TEST_SHEET_ID, "'" + SEND_QUEUE + "'!A2:M",
    {valueRenderOption: 'UNFORMATTED_VALUE'}).values || [];
}

function senderWrite_(config, row, index = null) {
  const prefix = "'" + SEND_QUEUE + "'!";
  if (index === null) {
    Sheets.Spreadsheets.Values.append({values: [row]}, config.TEST_SHEET_ID, prefix + 'A1',
      {valueInputOption: 'RAW', insertDataOption: 'INSERT_ROWS'});
  } else {
    Sheets.Spreadsheets.Values.update({values: [row]}, config.TEST_SHEET_ID, prefix + 'A' + (index + 2),
      {valueInputOption: 'RAW'});
  }
}

function enqueueSend_(config, client, request, now) {
  const key = request.idempotencyKey;
  if (typeof key !== 'string' || !/^[A-Za-z0-9_.:-]{1,100}$/.test(key)) return {ok: false, error: 'invalid_key'};
  const normalized = normalizeSend_(config, request);
  if (!normalized) return {ok: false, error: 'invalid_request'};
  const body = JSON.stringify(normalized);
  const prior = senderRows_(config).find(row => row[1] === client && row[2] === key);
  if (prior) return prior[3] === body ? {ok: true, sendId: prior[0], status: prior[SEND_STATE]} :
    {ok: false, error: 'idempotency_conflict'};
  const id = Utilities.getUuid();
  senderWrite_(config, [id, client, key, body, now, '承認待ち', '', 0, 0, '', 0, 0, '[]']);
  return {ok: true, sendId: id, status: '承認待ち'};
}

function senderApproval_(config, row) {
  return senderProof_(config.SEND_APPROVAL_SECRET, JSON.stringify(row.slice(0, 5)));
}

function approveTestSend() {
  const config = senderConfig_();
  const props = PropertiesService.getScriptProperties();
  senderLock_(() => {
    const id = props.getProperty('SEND_APPROVE_ID');
    if (!id) throw new Error('send_approval_id_required');
    const rows = senderRows_(config);
    const index = rows.findIndex(row => row[0] === id && row[SEND_STATE] === '承認待ち');
    if (index < 0) throw new Error('send_not_pending_approval');
    const row = rows[index];
    const body = JSON.parse(row[3]);
    if (JSON.stringify(normalizeSend_(config, body)) !== row[3]) throw new Error('send_invalid_content');
    row[SEND_APPROVAL] = senderApproval_(config, row);
    row[SEND_STATE] = '送信待ち';
    senderWrite_(config, row, index);
    props.deleteProperty('SEND_APPROVE_ID');
  });
}

function senderHistory_(row, now, status, code = 0, requestId = '') {
  const history = JSON.parse(row[SEND_HISTORY] || '[]');
  history.push({at: new Date(now).toISOString(), attempt: row[SEND_ATTEMPTS], status, code, requestId});
  row[SEND_HISTORY] = JSON.stringify(history);
  row[SEND_STATE] = status;
}

function claimSend_(config, now) {
  const rows = senderRows_(config);
  if (rows.some(row => row[SEND_STATE] === '送信中' && Number(row[SEND_LEASE]) > now)) return null;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (row[SEND_STATE] === '送信中' && Number(row[SEND_LEASE]) <= now) {
      senderHistory_(row, now, '結果不明');
      senderWrite_(config, row, i);
    }
    if (!['送信待ち', '再試行待ち'].includes(row[SEND_STATE]) || row[SEND_NEXT] > now) continue;
    if (!senderProofMatches_(senderApproval_(config, row), row[SEND_APPROVAL])) {
      row[SEND_APPROVAL] = '';
      senderHistory_(row, now, row[SEND_ATTEMPTS] ? '結果不明' : '承認待ち');
      senderWrite_(config, row, i);
      continue;
    }
    const body = JSON.parse(row[3]);
    if (JSON.stringify(normalizeSend_(config, body)) !== row[3]) {
      senderHistory_(row, now, '失敗');
      senderWrite_(config, row, i);
      continue;
    }
    if (body.notBefore > now) continue;
    if (row[SEND_FIRST] && now - row[SEND_FIRST] > 20 * 60 * 60 * 1000) {
      senderHistory_(row, now, '結果不明');
      senderWrite_(config, row, i);
      continue;
    }
    row[SEND_ATTEMPTS] = Number(row[SEND_ATTEMPTS]) + 1;
    row[SEND_FIRST] = row[SEND_FIRST] || now;
    row[SEND_OWNER] = Utilities.getUuid();
    row[SEND_LEASE] = now + 7 * 60000;
    senderHistory_(row, now, '送信中');
    senderWrite_(config, row, i);
    return {body, id: row[0], owner: row[SEND_OWNER]};
  }
  return null;
}

function finishSend_(config, claim, outcome, now) {
  const rows = senderRows_(config);
  const index = rows.findIndex(row => row[0] === claim.id && row[SEND_OWNER] === claim.owner && row[SEND_STATE] === '送信中');
  if (index < 0) return;
  const row = rows[index];
  const retry = outcome.retry && row[SEND_ATTEMPTS] < 3 &&
    senderProofMatches_(senderApproval_(config, row), row[SEND_APPROVAL]);
  row[SEND_NEXT] = retry ? now + 60000 * 2 ** (row[SEND_ATTEMPTS] - 1) : 0;
  row[SEND_OWNER] = '';
  row[SEND_LEASE] = 0;
  senderHistory_(row, now, retry ? '再試行待ち' : outcome.status, outcome.code, outcome.requestId);
  senderWrite_(config, row, index);
}

function testDriveMessage_(config, body) {
  if (body.type === 'text') return body.text;
  const file = DriveApp.getFileById(body.fileId);
  const parents = file.getParents();
  if (!parents.hasNext() || parents.next().getId() !== config.TEST_ROOT_ID) return null;
  const email = config.TEST_SELF_EMAIL;
  const owner = file.getOwner();
  const allowed = email && ((owner && owner.getEmail().toLowerCase() === email.toLowerCase()) ||
    [DriveApp.Permission.VIEW, DriveApp.Permission.EDIT, DriveApp.Permission.COMMENT, DriveApp.Permission.OWNER]
      .includes(file.getAccess(email)));
  if (!allowed || file.isTrashed()) return null;
  const url = file.getUrl();
  if (!/^https:\/\/(?:drive|docs)\.google\.com\//.test(url)) return null;
  const message = body.text + '\n' + url;
  return message.length <= 5000 ? message : null;
}

function pushTestMessage_(config, claim) {
  let text;
  try {
    text = testDriveMessage_(config, claim.body);
    if (!text) return {status: '条件待ち'};
    const options = {headers: {Authorization: 'Bearer ' + config.LINE_TEST_ACCESS_TOKEN},
      muteHttpExceptions: true, followRedirects: false};
    const bot = UrlFetchApp.fetch('https://api.line.me/v2/bot/info', options);
    if (bot.getResponseCode() !== 200 || JSON.parse(bot.getContentText()).userId !== config.TEST_BOT_ID) {
      return {status: '条件待ち'};
    }
    const quota = UrlFetchApp.fetch('https://api.line.me/v2/bot/message/quota', options);
    const used = UrlFetchApp.fetch('https://api.line.me/v2/bot/message/quota/consumption', options);
    if (quota.getResponseCode() !== 200 || used.getResponseCode() !== 200) return {status: '条件待ち'};
    const limit = JSON.parse(quota.getContentText());
    const total = JSON.parse(used.getContentText()).totalUsage;
    const cap = Number(config.TEST_MONTHLY_LIMIT || 20);
    const ceiling = limit.type === 'limited' ? Math.min(cap, Number(limit.value)) : cap;
    if (!Number.isSafeInteger(total) || !Number.isSafeInteger(ceiling) || total >= ceiling) return {status: '上限保留'};
  } catch (_) {
    return {status: '条件待ち'};
  }
  try {
    const response = UrlFetchApp.fetch('https://api.line.me/v2/bot/message/push', {
      method: 'post', contentType: 'application/json', followRedirects: false, muteHttpExceptions: true,
      headers: {Authorization: 'Bearer ' + config.LINE_TEST_ACCESS_TOKEN, 'X-Line-Retry-Key': claim.id},
      payload: JSON.stringify({to: config.TEST_SELF_LINE_ID, messages: [{type: 'text', text}]}),
    });
    const code = response.getResponseCode();
    const headers = Object.fromEntries(Object.entries(response.getAllHeaders()).map(([k, v]) => [k.toLowerCase(), v]));
    const accepted = headers['x-line-accepted-request-id'];
    const requestId = String(accepted || headers['x-line-request-id'] || '').slice(0, 150);
    if (code === 200 || (code === 409 && accepted)) return {status: 'API受付成功', code, requestId};
    if (code === 429 || code >= 500) {
      return {status: code >= 500 ? '結果不明' : '失敗', code, requestId, retry: true};
    }
    return {status: '失敗', code, requestId};
  } catch (_) {
    return {status: '結果不明'};
  }
}

function processTestSend() {
  const config = senderConfig_();
  if (config.ENABLE_TEST_PUSH !== 'yes' || !config.LINE_TEST_ACCESS_TOKEN) throw new Error('test_push_disabled');
  const claim = senderLock_(() => claimSend_(config, Date.now()));
  if (!claim) return;
  const outcome = pushTestMessage_(config, claim);
  senderLock_(() => finishSend_(config, claim, outcome, Date.now()));
}

function setupTestSendQueue() {
  const config = senderConfig_();
  senderLock_(() => {
    const root = DriveApp.getFolderById(config.TEST_ROOT_ID);
    if (root.getName() !== '業務連絡管理（テスト専用）' || root.getSharingAccess() !== DriveApp.Access.PRIVATE ||
        !root.getFilesByName('業務連絡台帳（テスト専用）').hasNext()) throw new Error('test_root_required');
    const file = DriveApp.getFileById(config.TEST_SHEET_ID);
    const parents = file.getParents();
    if (!parents.hasNext() || parents.next().getId() !== root.getId() ||
        file.getName() !== '業務連絡台帳（テスト専用）') throw new Error('test_sheet_required');
    const book = SpreadsheetApp.openById(config.TEST_SHEET_ID);
    const sheet = book.getSheetByName(SEND_QUEUE) || book.insertSheet(SEND_QUEUE);
    const header = sheet.getRange(1, 1, 1, SEND_COLUMNS.length);
    const current = header.getValues()[0];
    if (current.some(value => value !== '') && JSON.stringify(current) !== JSON.stringify(SEND_COLUMNS)) {
      throw new Error('unexpected_send_schema');
    }
    header.setValues([SEND_COLUMNS]);
    sheet.setFrozenRows(1);
    SpreadsheetApp.flush();
  });
}
