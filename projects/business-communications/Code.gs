const INBOX = '受信履歴';
const ARCHIVE = '原文ログ';
const ATTACHMENTS = '受信ファイル';
const LEGACY_HEADERS = [
  '受信ID', '経路', '環境', '受信日時', '記録日時', '送信者ID', '表示名', '会話ID',
  'イベントID', 'メッセージID', 'メッセージ種別', '本文', '元ファイル名', 'DriveファイルID',
  'URL', '保存状態', '振り分け状態', '案件ID', '移動先ID', 'エラー', '試行回数',
  '原文ファイルID', '原文保存待ち', '次回試行時刻', 'イベント種別', 'LINE発生日時', '処理リース',
];
const HEADERS = [...LEGACY_HEADERS, '受信経路詳細', 'LINE送信元種別', 'LINEグループID', '転送受付'];
const INBOX_LAST_COLUMN = 'AE';
const HEALTH_FOLDER = '死活監視（テスト専用）';
const HEALTH_MARKER = 'synthetic-receiver-check.txt';
const COL = Object.fromEntries(HEADERS.map((name, index) => [name, index]));
const ATTACHMENT_TYPES = ['image', 'video', 'audio', 'file'];
const MAX_ATTEMPTS = 6;

function storageSettings_() {
  const p = PropertiesService.getScriptProperties().getProperties();
  if (p.ENVIRONMENT !== 'test' || !p.TEST_ROOT_ID) throw new Error('test_config_required');
  return p;
}

function postResult_(ok) {
  return ContentService.createTextOutput(JSON.stringify({ok})).setMimeType(ContentService.MimeType.JSON);
}

function configureTestBotIdentity() {
  const config = storageSettings_();
  if (!/^@[A-Za-z0-9_-]+$/.test(config.TEST_BOT_BASIC_ID || '') || !config.LINE_TEST_ACCESS_TOKEN) {
    throw new Error('test_bot_credentials_required');
  }
  const response = UrlFetchApp.fetch('https://api.line.me/v2/bot/info', {
    headers: {Authorization: 'Bearer ' + config.LINE_TEST_ACCESS_TOKEN},
    followRedirects: false, muteHttpExceptions: true,
  });
  if (response.getResponseCode() !== 200) throw new Error('line_bot_info_unavailable');
  const bot = JSON.parse(response.getContentText());
  if (bot.basicId !== config.TEST_BOT_BASIC_ID || !/^U[0-9a-f]{32}$/.test(bot.userId || '')) {
    throw new Error('line_test_bot_mismatch');
  }
  PropertiesService.getScriptProperties().setProperty('TEST_BOT_ID', bot.userId);
}

function doPost(e) {
  try {
    const config = storageSettings_();
    const body = e && e.postData && e.postData.contents;
    if (!body || body.length > 710000) return postResult_(false);
    const envelope = JSON.parse(body);
    if (envelope && envelope.kind === 'receiver_health') return receiverHealth_(config, envelope);
    if (!config.TEST_BOT_ID || !config.WORKER_SECRET || config.WORKER_SECRET.length < 32) return postResult_(false);
    if (!validEnvelope_(envelope, config.WORKER_SECRET, Date.now())) return postResult_(false);
    const raw = Utilities.newBlob(Utilities.base64Decode(envelope.payload)).getDataAsString('UTF-8');
    const payload = JSON.parse(raw);
    if (payload.destination !== config.TEST_BOT_ID || !Array.isArray(payload.events)) return postResult_(false);
    const rows = payload.events.map(event => eventRow_(event, new Date(), config));
    if (rows.length) withLock_(() => appendEvents_(config.TEST_SHEET_ID, rows));
    return postResult_(true);
  } catch (_) {
    return postResult_(false);
  }
}

function receiverHealth_(config, envelope) {
  const now = Date.now();
  const secret = config.RECEIVER_HEALTH_SECRET || '';
  if (secret.length < 32 || secret === config.WORKER_SECRET ||
      typeof envelope.payload !== 'string' || envelope.payload.length > 256 ||
      !validEnvelope_(envelope, secret, now)) return postResult_(false);
  const payload = JSON.parse(Utilities.newBlob(Utilities.base64Decode(envelope.payload)).getDataAsString('UTF-8'));
  if (payload.kind !== 'receiver_health' || !/^[a-f0-9]{32}$/.test(payload.nonce || '') ||
      !config.TEST_SHEET_ID || !config.ARCHIVE_ID || !config.ATTACHMENTS_ID) return postResult_(false);

  return withLock_(() => {
    const root = DriveApp.getFolderById(config.TEST_ROOT_ID);
    if (root.getName() !== '業務連絡管理（テスト専用）' || root.getSharingAccess() !== DriveApp.Access.PRIVATE) {
      return postResult_(false);
    }
    const dates = Sheets.Spreadsheets.Values.get(config.TEST_SHEET_ID, INBOX + '!D2:D',
      {valueRenderOption: 'UNFORMATTED_VALUE'}).values || [];
    const states = Sheets.Spreadsheets.Values.get(config.TEST_SHEET_ID, INBOX + '!P2:P').values || [];
    let pending = 0;
    let oldestPendingSeconds = 0;
    const pendingStates = ['受信済み', '添付取得待ち', '再試行待ち'];
    for (let i = 0; i < states.length; i++) {
      if (!pendingStates.includes(states[i][0])) continue;
      const received = Date.parse(dates[i]?.[0] || '');
      if (!Number.isFinite(received)) return postResult_(false);
      pending++;
      oldestPendingSeconds = Math.max(oldestPendingSeconds, Math.floor((now - received) / 1000));
    }

    const markerFolder = folder_(root, HEALTH_FOLDER);
    const lookup = markerFolder.getFilesByName(HEALTH_MARKER);
    const content = 'receiver-health-v1:' + payload.nonce;
    let marker;
    if (lookup.hasNext()) {
      marker = lookup.next();
      if (lookup.hasNext() || !/^receiver-health-v1:[a-f0-9]{32}$/.test(marker.getBlob().getDataAsString())) {
        return postResult_(false);
      }
      marker.setContent(content);
    } else {
      marker = markerFolder.createFile(HEALTH_MARKER, content, MimeType.PLAIN_TEXT);
    }
    if (marker.getBlob().getDataAsString() !== content) return postResult_(false);
    return ContentService.createTextOutput(JSON.stringify({ok: true, nonce: payload.nonce, pending, oldestPendingSeconds}))
      .setMimeType(ContentService.MimeType.JSON);
  });
}

function validEnvelope_(envelope, key, now) {
  if (!envelope || typeof envelope.timestamp !== 'string' || !/^\d{10}$/.test(envelope.timestamp) ||
      Math.abs(now / 1000 - Number(envelope.timestamp)) > 120 || typeof envelope.payload !== 'string' ||
      typeof envelope.signature !== 'string' || envelope.signature.length !== 44) return false;
  const check = Utilities.base64Encode(Utilities.computeHmacSha256Signature(
    envelope.timestamp + '.' + envelope.payload, key, Utilities.Charset.UTF_8));
  let mismatch = 0;
  for (let i = 0; i < check.length; i++) mismatch |= check.charCodeAt(i) ^ envelope.signature.charCodeAt(i);
  return mismatch === 0;
}

function eventRow_(event, now, config = {}) {
  const id = event.webhookEventId;
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(id)) throw new Error('invalid_event_id');
  const source = event.source || {};
  const message = event.message || {};
  const attachment = event.type === 'message' && ATTACHMENT_TYPES.includes(message.type);
  if (attachment && (typeof message.id !== 'string' || !/^\d+$/.test(message.id))) {
    throw new Error('invalid_attachment_id');
  }
  const raw = Object.assign({}, event);
  delete raw.replyToken;
  const encoded = JSON.stringify(raw);
  if (encoded.length > 45000) throw new Error('event_too_large');
  const conversation = source.groupId || source.roomId || (source.type === 'user' ? source.userId : '');
  const eventDate = new Date(event.timestamp);
  return [id, 'LINE', 'test', now.toISOString(), now.toISOString(), source.userId || '', '', conversation || '',
    id, message.id || '', message.type || '', message.type === 'text' ? message.text || '' : '',
    message.fileName || '', '', '', attachment ? '添付取得待ち' : '受信済み', '未処理', '', '', '',
    0, '', encoded, 0, event.type || '', Number.isFinite(eventDate.getTime()) ? eventDate.toISOString() : '', 0,
    ...forwardRoute_(source, config)];
}

function forwardRoute_(source, config) {
  const groupId = source.type === 'group' ? source.groupId || '' : '';
  const dedicated = /^C[0-9a-f]{32}$/.test(groupId) && groupId === config.TEST_FORWARD_GROUP_ID;
  const verifiedOwner = dedicated && /^U[0-9a-f]{32}$/.test(config.TEST_FORWARD_OWNER_ID || '') &&
    source.userId === config.TEST_FORWARD_OWNER_ID;
  const route = dedicated ? '専用グループ転送' : source.type === 'group' ? '一般グループ' :
    source.type === 'user' ? '公式直接' : 'その他';
  return [route, source.type || '', groupId, dedicated ? (verifiedOwner ? '認可済み' : '未認可') : ''];
}

function withLock_(work) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(4000)) throw new Error('worker_busy');
  try {
    return work();
  } finally {
    lock.releaseLock();
  }
}

function appendEvents_(sheetId, rows) {
  const result = Sheets.Spreadsheets.Values.get(sheetId, INBOX + '!A2:A');
  const seen = new Set((result.values || []).map(row => row[0]));
  const fresh = rows.filter(row => {
    if (seen.has(row[0])) return false;
    seen.add(row[0]);
    return true;
  });
  if (fresh.length) Sheets.Spreadsheets.Values.append({values: fresh}, sheetId, INBOX + '!A1', {
    valueInputOption: 'RAW', insertDataOption: 'INSERT_ROWS',
  });
}

function setupTestStorage() {
  const config = storageSettings_();
  const props = PropertiesService.getScriptProperties();
  withLock_(() => {
    const root = DriveApp.getFolderById(config.TEST_ROOT_ID);
    if (root.getName() !== '業務連絡管理（テスト専用）' || root.getSharingAccess() !== DriveApp.Access.PRIVATE) {
      throw new Error('test_root_must_be_private');
    }
    const name = '業務連絡台帳（テスト専用）';
    const existing = root.getFilesByName(name);
    let ledger;
    if (existing.hasNext()) {
      ledger = SpreadsheetApp.openById(existing.next().getId());
      if (existing.hasNext()) throw new Error('duplicate_test_spreadsheets');
    } else {
      ledger = SpreadsheetApp.create(name);
      DriveApp.getFileById(ledger.getId()).moveTo(root);
    }
    const sheet = ledger.getSheetByName(INBOX) || ledger.insertSheet(INBOX);
    const columns = sheet.getMaxColumns();
    if (columns < HEADERS.length) sheet.insertColumnsAfter(columns, HEADERS.length - columns);
    const header = sheet.getRange(1, 1, 1, HEADERS.length);
    const current = header.getValues()[0];
    if (!validInboxHeader_(current)) {
      throw new Error('unexpected_sheet_schema');
    }
    header.setValues([HEADERS]);
    sheet.setFrozenRows(1);
    const archive = folder_(root, ARCHIVE);
    const attachments = folder_(root, ATTACHMENTS);
    SpreadsheetApp.flush();
    props.setProperties({TEST_SHEET_ID: ledger.getId(), ARCHIVE_ID: archive.getId(), ATTACHMENTS_ID: attachments.getId()});
    if (config.TEST_BOT_ID && config.LINE_TEST_ACCESS_TOKEN && config.WORKER_SECRET && config.WORKER_SECRET.length >= 32 &&
        !ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'processTestInbox')) {
      ScriptApp.newTrigger('processTestInbox').timeBased().everyMinutes(1).create();
    }
  });
}

function validInboxHeader_(header) {
  return header.every(value => value === '') || JSON.stringify(header) === JSON.stringify(HEADERS) ||
    (LEGACY_HEADERS.every((name, index) => header[index] === name) &&
      header.slice(LEGACY_HEADERS.length).every(value => value === ''));
}

function backfillTestInboxRouting() {
  const config = storageSettings_();
  if (!/^C[0-9a-f]{32}$/.test(config.TEST_FORWARD_GROUP_ID || '') ||
      !/^U[0-9a-f]{32}$/.test(config.TEST_FORWARD_OWNER_ID || '')) throw new Error('verified_forward_settings_required');
  return withLock_(() => {
    const values = Sheets.Spreadsheets.Values;
    const prefix = INBOX + '!';
    const rows = values.get(config.TEST_SHEET_ID, prefix + 'A1:' + INBOX_LAST_COLUMN,
      {valueRenderOption: 'UNFORMATTED_VALUE'}).values || [];
    if (JSON.stringify(rows[0]) !== JSON.stringify(HEADERS)) throw new Error('setup_test_storage_first');
    const updates = [];
    for (let i = 1; i < rows.length && updates.length < 40; i++) {
      const row = rows[i];
      const archive = row[COL['原文ファイルID']];
      if (row[COL['経路']] !== 'LINE' || row[COL['環境']] !== 'test' || row[COL['受信経路詳細']] || !archive) continue;
      const saved = JSON.parse(DriveApp.getFileById(archive).getBlob().getDataAsString());
      if (saved.webhookEventId !== row[COL['受信ID']]) throw new Error('archived_event_id_mismatch');
      updates.push({range: prefix + 'AB' + (i + 1) + ':' + INBOX_LAST_COLUMN + (i + 1),
        values: [forwardRoute_(saved.source || {}, config)]});
    }
    if (updates.length) values.batchUpdate({valueInputOption: 'RAW', data: updates}, config.TEST_SHEET_ID);
    return updates.length;
  });
}

function folder_(parent, name) {
  const matches = parent.getFoldersByName(name);
  if (!matches.hasNext()) return parent.createFolder(name);
  const folder = matches.next();
  if (matches.hasNext()) throw new Error('duplicate_folder');
  return folder;
}

function processTestInbox() {
  const config = storageSettings_();
  processInbox_(config, row => attachment_(row, config.LINE_TEST_ACCESS_TOKEN));
}

function processInbox_(config, loadAttachment, allowedIds = null) {
  const stop = Date.now() + 45000;
  while (Date.now() < stop) {
    const claim = withLock_(() => claimInbox_(config.TEST_SHEET_ID, Date.now(), allowedIds));
    if (!claim) return;
    let content;
    let error = '';
    if (claim.row[COL['保存状態']] === '添付取得待ち' || claim.row[COL['保存状態']] === '再試行待ち') {
      try {
        if (!existingAttachment_(config, claim.row)) content = loadAttachment(claim.row);
      } catch (_) {
        error = 'LINE添付の取得に失敗';
      }
    }
    withLock_(() => finishInbox_(config, claim, content, error));
  }
}

function claimInbox_(sheetId, now, allowedIds = null) {
  const rows = Sheets.Spreadsheets.Values.get(sheetId, INBOX + '!A2:' + INBOX_LAST_COLUMN, {
    valueRenderOption: 'UNFORMATTED_VALUE',
  }).values || [];
  const index = rows.findIndex(row => (!allowedIds || allowedIds.has(row[0])) &&
    ['受信済み', '添付取得待ち', '再試行待ち'].includes(row[COL['保存状態']]) &&
    Number(row[COL['次回試行時刻']] || 0) <= now && Number(row[COL['処理リース']] || 0) <= now);
  if (index < 0) return null;
  const row = rows[index];
  while (row.length < HEADERS.length) row.push('');
  const claim = {row, number: index + 2, lease: now + 7 * 60000};
  row[COL['処理リース']] = claim.lease;
  writeRow_(sheetId, claim);
  return claim;
}

function writeRow_(sheetId, claim) {
  Sheets.Spreadsheets.Values.update({values: [claim.row]}, sheetId, INBOX + '!A' + claim.number,
    {valueInputOption: 'RAW'});
}

function finishInbox_(config, claim, content, error) {
  const range = INBOX + '!A' + claim.number + ':' + INBOX_LAST_COLUMN + claim.number;
  const current = Sheets.Spreadsheets.Values.get(config.TEST_SHEET_ID, range, {
    valueRenderOption: 'UNFORMATTED_VALUE',
  }).values;
  if (!current || current[0][0] !== claim.row[0] || Number(current[0][COL['処理リース']]) !== claim.lease) return;
  const row = claim.row;
  const month = receiptMonth_(row);
  try {
    if (!row[COL['原文ファイルID']]) {
      const folder = folder_(DriveApp.getFolderById(config.ARCHIVE_ID), month);
      const name = row[0] + '.json';
      const files = folder.getFilesByName(name);
      const archive = files.hasNext() ? files.next() : folder.createFile(name, row[COL['原文保存待ち']], MimeType.PLAIN_TEXT);
      row[COL['原文ファイルID']] = archive.getId();
      row[COL['原文保存待ち']] = '';
    }
    let file = existingAttachment_(config, row);
    if (!file && content) {
      const folder = folder_(DriveApp.getFolderById(config.ATTACHMENTS_ID), month);
      file = folder.createFile(content.setName(attachmentName_(row)));
    }
    if (file) {
      row[COL['DriveファイルID']] = file.getId();
      row[COL['URL']] = file.getUrl();
      if (error === 'LINE添付の取得に失敗') error = '';
    }
  } catch (_) {
    error = 'Driveへの保存に失敗';
  }
  const attempts = Number(row[COL['試行回数']]) + 1;
  row[COL['試行回数']] = attempts;
  row[COL['エラー']] = error;
  row[COL['処理リース']] = 0;
  const retry = error && attempts < MAX_ATTEMPTS;
  row[COL['次回試行時刻']] = retry ? Date.now() + 60000 * 2 ** (attempts - 1) : 0;
  row[COL['保存状態']] = retry ? '再試行待ち' : error ? '保存失敗' : '保存済み';
  writeRow_(config.TEST_SHEET_ID, claim);
}

function attachment_(row, token) {
  if (!ATTACHMENT_TYPES.includes(row[COL['メッセージ種別']])) return null;
  const id = row[COL['メッセージID']];
  if (!token || !/^\d+$/.test(id)) throw new Error('attachment_config_required');
  const response = UrlFetchApp.fetch('https://api-data.line.me/v2/bot/message/' + id + '/content', {
    headers: {Authorization: 'Bearer ' + token}, followRedirects: false, muteHttpExceptions: true,
  });
  if (response.getResponseCode() !== 200) throw new Error('attachment_not_available');
  return response.getBlob();
}

function receiptMonth_(row) {
  return Utilities.formatDate(new Date(row[COL['受信日時']]), 'Asia/Tokyo', 'yyyy-MM');
}

function existingAttachment_(config, row) {
  if (!ATTACHMENT_TYPES.includes(row[COL['メッセージ種別']])) return null;
  const folders = DriveApp.getFolderById(config.ATTACHMENTS_ID).getFoldersByName(receiptMonth_(row));
  if (!folders.hasNext()) return null;
  const files = folders.next().getFilesByName(attachmentName_(row));
  return files.hasNext() ? files.next() : null;
}

function attachmentName_(row) {
  const basename = row[COL['元ファイル名']].replace(/[\/\\\x00-\x1f]/g, '_').slice(0, 180);
  return row[0] + '_' + (basename || row[COL['メッセージ種別']]);
}

function runSyntheticTextTest() {
  const config = storageSettings_();
  if (config.TEST_BOT_ID || config.LINE_TEST_ACCESS_TOKEN) throw new Error('synthetic_only');
  setupTestStorage();
  const isolated = storageSettings_();
  const id = 'synthetic-' + Utilities.getUuid();
  const event = {webhookEventId: id, type: 'message', timestamp: Date.now(),
    message: {type: 'text', text: '受信台帳・原文保存の動作確認'}};
  const row = eventRow_(event, new Date());
  withLock_(() => appendEvents_(isolated.TEST_SHEET_ID, [row, row]));
  processTestInbox();
  const rows = Sheets.Spreadsheets.Values.get(isolated.TEST_SHEET_ID, INBOX + '!A2:' + INBOX_LAST_COLUMN).values || [];
  const found = rows.filter(entry => entry[0] === id);
  if (found.length !== 1 || found[0][COL['保存状態']] !== '保存済み' || !found[0][COL['原文ファイルID']]) {
    throw new Error('synthetic_storage_test_failed');
  }
}

function runSyntheticAttachmentTest() {
  const config = storageSettings_();
  if (config.TEST_BOT_ID || config.LINE_TEST_ACCESS_TOKEN) throw new Error('synthetic_only');
  setupTestStorage();
  const isolated = storageSettings_();
  const timestamp = Date.now();
  const now = new Date(timestamp);
  const rows = ['1', '2', '3'].map((suffix, i) => eventRow_({
    webhookEventId: 'synthetic-' + Utilities.getUuid(), timestamp, type: 'message',
    message: i < 2 ? {type: 'file', id: suffix, fileName: 'synthetic.png'} : {type: 'image', id: suffix},
  }, now));
  const ids = new Set(rows.map(row => row[0]));
  const png = Utilities.base64Decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jc0wAAAAASUVORK5CYII=');
  withLock_(() => appendEvents_(isolated.TEST_SHEET_ID, [...rows, rows[0]]));
  processInbox_(isolated, () => Utilities.newBlob(png, 'image/png'), ids);
  const saved = (Sheets.Spreadsheets.Values.get(isolated.TEST_SHEET_ID, INBOX + '!A2:' + INBOX_LAST_COLUMN).values || [])
    .filter(row => ids.has(row[0]));
  const fileIds = new Set(saved.map(row => row[COL['DriveファイルID']]));
  const correct = saved.length === ids.size && fileIds.size === ids.size && saved.every(row => {
    if (row[COL['保存状態']] !== '保存済み' || !row[COL['原文ファイルID']] || !row[COL['DriveファイルID']]) return false;
    const file = DriveApp.getFileById(row[COL['DriveファイルID']]);
    return file.getName() === attachmentName_(row) && file.getMimeType() === 'image/png' &&
      Utilities.base64Encode(file.getBlob().getBytes()) === Utilities.base64Encode(png);
  });
  if (!correct) throw new Error('synthetic_attachment_test_failed');
}
