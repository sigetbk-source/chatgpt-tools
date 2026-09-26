const fs = require('fs');
const path = require('path');
const status = document.getElementById('status');
const root = window.CAPTION_BRIDGE_ROOT;
let busy = false;
function show(value) { status.textContent = value; }
function jsx(name, data) {
  return new Promise((resolve, reject) => {
    const call = `${name}(${JSON.stringify(JSON.stringify(data))})`;
    window.__adobe_cep__.evalScript(call, raw => {
      try { const value = JSON.parse(raw); value.ok ? resolve(value) : reject(Error(value.error)); }
      catch (error) { reject(error); }
    });
  });
}
function equalBytes(a, b) { return a.length === b.length && a.equals(b); }
async function handle(request) {
  const now = Date.now();
  if (!request || request.version !== 1 || !/^[a-f0-9]{32}$/.test(request.nonce || '') ||
      request.operation !== 'place_srt' || !Number.isFinite(request.deadlineMs) ||
      request.deadlineMs < now || request.deadlineMs > now + 120000) throw Error('Invalid or expired caption request');
  const p = request.payload || {};
  if (!p.projectPath || !p.sequenceGuid || !p.sequenceName || !p.srtPath ||
      !p.srtPath.toLowerCase().endsWith('.srt') || typeof p.expectedText !== 'string' ||
      !p.expectedText.trim() || p.sourceTimeVerified !== true ||
      p.expectedCaptionTrackCount !== 0) throw Error('Incomplete caption target');
  if (await fs.promises.readFile(p.srtPath, 'utf8') !== p.expectedText) throw Error('SRT content mismatch');
  await jsx('captionPrepare', p);
  const backupPath = path.join(root, 'caption-backups', `${request.nonce}.prproj`);
  await fs.promises.copyFile(p.projectPath, backupPath, fs.constants.COPYFILE_EXCL);
  if (!equalBytes(await fs.promises.readFile(p.projectPath), await fs.promises.readFile(backupPath)))
    throw Error('Project backup readback mismatch');
  if (Date.now() > request.deadlineMs) throw Error('Request expired before caption placement');
  try {
    const placed = await jsx('captionPlace', p);
    return { state: 'placement_pending_verification', created: placed.created === true,
      saved: placed.saved === true, backupPath, projectPath: p.projectPath,
      sequenceGuid: p.sequenceGuid, srtPath: p.srtPath };
  } catch (cause) {
    const error = Error('Caption placement failed: ' + String(cause));
    error.result = { backupPath, needsManualRecovery: true };
    throw error;
  }
}
async function poll() {
  if (!root || busy) return;
  busy = true;
  try {
    const inbox = path.join(root, 'caption-inbox');
    const names = (await fs.promises.readdir(inbox)).filter(name => /^[a-f0-9]{32}\.json$/.test(name)).sort();
    for (const name of names) {
      const nonce = name.slice(0, -5), input = path.join(inbox, name);
      let request;
      try { request = JSON.parse(await fs.promises.readFile(input, 'utf8')); } catch (_) { continue; }
      const claim = path.join(root, 'caption-claims', `${nonce}.json`);
      try { await fs.promises.writeFile(claim, JSON.stringify({nonce,claimedAt:Date.now()}), {flag:'wx'}); }
      catch (_) { show(`既処理または結果不明: ${nonce}`); continue; }
      let response;
      try {
        if (request.nonce !== nonce) throw Error('Nonce mismatch');
        response = {nonce,ok:true,result:await handle(request)};
        show(`字幕を配置しました。画面確認待ち: ${nonce}`);
      } catch (error) {
        response = {nonce,ok:false,error:String(error),result:error.result || null};
        show(`配置失敗: ${String(error)}`);
      }
      const output = path.join(root, 'caption-outbox', `${nonce}.json`);
      await fs.promises.writeFile(`${output}.tmp`, JSON.stringify(response));
      await fs.promises.rename(`${output}.tmp`, output);
      await fs.promises.unlink(input).catch(() => {});
    }
  } catch (error) { show(`連携エラー: ${String(error)}`); }
  finally { busy = false; }
}
if (root) { show(`待受中: ${root}`); setInterval(poll, 250); }
else show('連携設定がありません');
