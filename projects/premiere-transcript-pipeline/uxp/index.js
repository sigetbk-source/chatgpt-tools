const { storage, shell } = require('uxp');
const fs = require('fs');
const ppro = require('premierepro');
const { runRequest } = require('./workflow-bridge');
const { runnerRequest, assertLoopbackUrl, makeNonce } = require('./workflow-launcher');
const status = document.getElementById('status');
const workspaceLabel = document.getElementById('workspace');
const targetLabel = document.getElementById('target');
const runnerLabel = document.getElementById('runner');
let root = '';
let busy = false;
let workspace = '';
let selectedTarget = null;
let reviewUrl = '';
const handled = new Set();

function message(value) { status.textContent = value; }
async function choose() {
  const folder = await storage.localFileSystem.getFolder();
  if (!folder) return;
  await connect(await storage.localFileSystem.getNativePath(folder));
}

async function connect(path) {
  root = path;
  if (!root) throw Error('Folder native path unavailable');
  for (const name of ['inbox', 'outbox', 'backups', 'claims',
    'launcher-inbox', 'launcher-outbox', 'launcher-claims']) {
    const child = `${root}/${name}`;
    try { await fs.mkdir(child, { recursive: true }); }
    catch (error) {
      // Premiere's UXP fs may report EEXIST even with recursive:true.
      // Confirm that the existing entry is a readable directory.
      try { await fs.readdir(child); } catch (_) { throw error; }
    }
  }
  message(`待受中: ${root}`);
}

async function chooseWorkspace() {
  const folder = await storage.localFileSystem.getFolder();
  if (!folder) return;
  const path = await storage.localFileSystem.getNativePath(folder);
  if (!path || !path.startsWith('/')) throw Error('作業フォルダのパスを取得できません');
  workspace = path;
  reviewUrl = '';
  document.getElementById('open').disabled = true;
  workspaceLabel.textContent = workspace;
  runnerLabel.textContent = '状態更新を押してください';
}

async function inspectTarget() {
  let result;
  try {
    result = await runRequest({ version: 1, nonce: makeNonce(), operation: 'selected_target',
      deadlineMs: Date.now() + 10000, payload: {} }, { ppro });
  } catch (error) {
    selectedTarget = null;
    targetLabel.textContent = 'Premiere のプロジェクトパネルで元素材を1件選んでください。';
    throw error;
  }
  selectedTarget = result.target;
  targetLabel.textContent = `${selectedTarget.clipName}\n${selectedTarget.mediaPath}\n${selectedTarget.projectPath}`;
}

function renderRunner(result) {
  const details = [result.state || 'unknown', `画面: ${result.serverRunning ? '起動中' : '停止中'}`];
  if (typeof result.uxpBridgeConfigured === 'boolean')
    details.push(`Premiere UXP: ${result.uxpBridgeConfigured ? '連携フォルダ設定済み' : '未設定'}`);
  if (typeof result.cepBridgeConfigured === 'boolean')
    details.push(`字幕 CEP: ${result.cepBridgeConfigured ? '連携フォルダ設定済み' : '未設定'}`);
  if (result.detail) details.push(String(result.detail));
  runnerLabel.textContent = details.join('\n');
  reviewUrl = result.serverRunning && result.url ? assertLoopbackUrl(result.url) : '';
  document.getElementById('open').disabled = !reviewUrl;
}

async function refreshRunner() {
  if (!root) throw Error('連携フォルダを選んでください');
  if (!workspace) throw Error('作業フォルダを選んでください');
  runnerLabel.textContent = '状態確認中…';
  renderRunner(await runnerRequest(fs, root, 'status', { workspace }));
}

async function openReview() {
  if (!reviewUrl) throw Error('修正画面が起動していません');
  const result = await shell.openExternal(assertLoopbackUrl(reviewUrl), '字幕修正画面をブラウザで開きます');
  if (result) throw Error(String(result));
}

async function startReview() {
  if (!root) throw Error('連携フォルダを選んでください');
  if (!workspace) throw Error('作業フォルダを選んでください');
  // Refresh at the click, so the target cannot silently change since inspection.
  await inspectTarget();
  runnerLabel.textContent = '起動・再開中…';
  const result = await runnerRequest(fs, root, 'start_or_resume', {
    workspace, mode: document.getElementById('mode').value, target: selectedTarget,
  }, { timeoutMs: 30000 });
  renderRunner(result);
  if (reviewUrl) await openReview();
}

function uiAction(fn) { return () => fn().catch(error => { runnerLabel.textContent = String(error); }); }

async function loadLocalConfig() {
  try {
    const config = JSON.parse(await fs.readFile('plugin:/bridge-config.json', 'utf-8'));
    if (config && typeof config.root === 'string' && config.root.startsWith('/')) {
      await connect(config.root);
      if (typeof config.workspace === 'string' && config.workspace.startsWith('/')) {
        workspace = config.workspace;
        workspaceLabel.textContent = workspace;
      }
    }
    else throw Error('連携フォルダの設定が不正です');
  } catch (error) { message(`自動接続できません: ${String(error)}。連携フォルダを選んでください。`); }
}

async function backup(nonce, data) {
  const path = `${root}/backups/${nonce}.json`;
  const content = JSON.stringify({ createdAt: new Date().toISOString(), ...data });
  await fs.writeFile(path, content, { encoding: 'utf-8', flag: 'wx' });
  const stored = JSON.parse(await fs.readFile(path, 'utf-8'));
  if (JSON.stringify(stored.target) !== JSON.stringify(data.target) ||
      JSON.stringify(stored.transcript) !== JSON.stringify(data.transcript)) throw Error('Backup readback mismatch');
  return path;
}

async function backupProject(nonce, projectPath) {
  if (!projectPath || !projectPath.toLowerCase().endsWith('.prproj')) throw Error('Invalid project backup source');
  const path = `${root}/backups/${nonce}.prproj`;
  await fs.copyFile(projectPath, path, 1); // exclusive destination
  const source = new Uint8Array(await fs.readFile(projectPath));
  const stored = new Uint8Array(await fs.readFile(path));
  if (source.length !== stored.length || source.some((byte, i) => byte !== stored[i]))
    throw Error('Project backup readback mismatch');
  return path;
}

async function poll() {
  if (!root || busy) return;
  busy = true;
  try {
    const files = (await fs.readdir(`${root}/inbox`)).filter(name => /^[a-f0-9]{32}\.json$/.test(name)).sort();
    for (const name of files) {
      const nonce = name.slice(0, -5);
      if (handled.has(nonce)) continue;
      const input = `${root}/inbox/${name}`;
      let request;
      try { request = JSON.parse(await fs.readFile(input, 'utf-8')); }
      catch (_) { continue; }
      handled.add(nonce);
      // A claim survives panel reload. Never replay a transaction whose prior
      // outcome may be unknown after a crash or timeout.
      try {
        await fs.writeFile(`${root}/claims/${nonce}.json`,
          JSON.stringify({ nonce, claimedAt: new Date().toISOString() }),
          { encoding: 'utf-8', flag: 'wx' });
      } catch (_) {
        message(`処理済みまたは結果不明: ${nonce}。新しいスナップショットで確認してください。`);
        continue;
      }
      let response;
      try {
        if (request.nonce !== nonce) throw Error('Request filename/nonce mismatch');
        const result = await runRequest(request, { ppro, persistBackup: backup,
          persistProjectBackup: backupProject,
          readSrt: path => fs.readFile(path, 'utf-8') });
        response = { nonce, ok: true, result };
        message(`処理完了: ${request.operation} (${nonce})`);
      } catch (error) {
        response = { nonce, ok: false, error: String(error), result: error.result || null };
        message(`処理失敗: ${String(error)} (${nonce})`);
      }
      const tmp = `${root}/outbox/${nonce}.tmp`;
      await fs.writeFile(tmp, JSON.stringify(response), { encoding: 'utf-8' });
      await fs.rename(tmp, `${root}/outbox/${nonce}.json`);
      try { await fs.unlink(input); } catch (_) {}
    }
  } catch (error) { message(`連携エラー: ${String(error)}`); }
  finally { busy = false; }
}

document.getElementById('choose').addEventListener('click', () => choose().catch(error => message(String(error))));
document.getElementById('choose-workspace').addEventListener('click', uiAction(chooseWorkspace));
document.getElementById('inspect-target').addEventListener('click', uiAction(inspectTarget));
document.getElementById('refresh').addEventListener('click', uiAction(refreshRunner));
document.getElementById('start').addEventListener('click', uiAction(startReview));
document.getElementById('open').addEventListener('click', uiAction(openReview));
loadLocalConfig().catch(error => message(String(error)));
setInterval(() => poll().catch(error => message(String(error))), 250);
