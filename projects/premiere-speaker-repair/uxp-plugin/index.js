// Complete-file reload sentinel: HTML, CSS, and JS are now in sync.
try {
const { storage } = require('uxp');
const fs = require('fs');

function showBootStatus(message, isError = false) {
  const node = document.querySelector('#boot-status');
  if (!node) return;
  node.textContent = message;
  node.style.background = isError ? '#4b2421' : '#143e30';
  node.style.color = isError ? '#ffb1a8' : '#83e4bd';
}

function reportError(context, error, details = {}) {
  try { window.uxpDiagnostics?.report(error, context, details); } catch (_) {}
  showBootStatus(`エラーを記録しました: ${error?.message || error}`, true);
  setMessage(error?.message || String(error));
}

let ppro;
try {
  ppro = require('premierepro');
} catch (error) {
  showBootStatus(`Premiere連携の読込エラー: ${error.message}`, true);
  try { window.uxpDiagnostics?.report(error, 'premierepro.require'); } catch (_) {}
}

function readSavedReferenceDir() {
  try {
    const resetToken = 'test-reset-20260719-01';
    if (localStorage.getItem('lastAppliedReset') !== resetToken) {
      localStorage.removeItem('referenceDir');
      localStorage.setItem('lastAppliedReset', resetToken);
    }
    return localStorage.getItem('referenceDir') || '';
  } catch (_) {
    return '';
  }
}

const IPC_ROOT = '/LOCAL_BUILD/premiere-speaker-repair/work/uxp/ipc';
const IPC_INBOX = `${IPC_ROOT}/inbox`;
const IPC_OUTBOX = `${IPC_ROOT}/outbox`;
const BRIDGE_START_TIMEOUT_MS = 12000;
const CEP_NARRATION_EXPORT = '/LOCAL_BUILD/Library/Application Support/Adobe/CEP/extensions/jp.tsubaki.narration.timeline/data/current_narration_export.json';
const MEDIA_EXTENSIONS = new Set([
  'mov', 'mp4', 'mxf', 'm4v', 'avi', 'wav', 'wave', 'mp3', 'm4a', 'aac', 'aif', 'aiff', 'flac'
]);
const VIDEO_EXTENSIONS = new Set(['mov', 'mp4', 'mxf', 'm4v', 'avi']);
const REVIEW_PRESET_CANDIDATES = [
  '/Applications/Adobe Premiere Pro 2025/Adobe Premiere Pro 2025.app/Contents/Settings/Frame.io/EncoderPresets/shareforreview-web720.epr',
  '/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/Settings/Frame.io/EncoderPresets/shareforreview-web720.epr',
  '/Applications/Adobe Premiere Pro 2024/Adobe Premiere Pro 2024.app/Contents/Settings/Frame.io/EncoderPresets/shareforreview-web720.epr',
];
let cachedReviewPreset = '';
const state = {
  project: null,
  assets: [],
  assetRefs: new Map(),
  referenceDir: readSavedReferenceDir(),
  watcher: null,
  writebacksInProgress: new Set(),
  panelPromptsShown: new Set(),
};
let visibleNarrationDiffId = '';
let narrationDiffRestoreSequence = 0;
let narrationRefreshInFlight = null;
let bridgeStartInFlight = null;

const $ = selector => document.querySelector(selector);

function setMessage(text) { $('#message').textContent = text || ''; }

function selectWorkspace(name) {
  const selected = name === 'transcript' ? 'transcript' : 'narration';
  for (const workspace of document.querySelectorAll('[data-workspace]')) {
    workspace.hidden = workspace.dataset.workspace !== selected;
  }
  for (const tab of document.querySelectorAll('[data-workspace-tab]')) {
    tab.classList.toggle('active', tab.dataset.workspaceTab === selected);
  }
  try { localStorage.setItem('productionWorkspace', selected); } catch (_) {}
}

function setupWorkspaceTabs() {
  for (const tab of document.querySelectorAll('[data-workspace-tab]')) {
    tab.addEventListener('click', () => selectWorkspace(tab.dataset.workspaceTab));
  }
  let saved = 'narration';
  try { saved = localStorage.getItem('productionWorkspace') || 'narration'; } catch (_) {}
  selectWorkspace(saved);
}

async function api(path, options = {}) {
  const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const requestPath = `${IPC_INBOX}/${requestId}.json`;
  const responsePath = `${IPC_OUTBOX}/${requestId}.json`;
  let payload = {};
  if (options.body) payload = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
  await fs.writeFile(requestPath, JSON.stringify({id: requestId, path, payload}), {encoding: 'utf-8'});
  const deadline = Date.now() + (options.timeoutMs || 15000);
  while (Date.now() < deadline) {
    let text = '';
    try {
      text = await fs.readFile(responsePath, 'utf8');
    } catch (_) {}
    if (text) {
      const response = JSON.parse(text);
      try { await fs.unlink(responsePath); } catch (_) {}
      if (!response.ok) throw new Error(response.error || '連携処理に失敗しました');
      return response;
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('UXP連携サーバーから応答がありません');
}

function setBridgeState(connected, label = '') {
  const status = $('#bridge-status');
  const start = $('#start-bridge');
  if (status) {
    status.textContent = label || (connected ? '接続済み' : '未接続');
    status.className = `connection ${connected ? 'on' : 'off'}`;
  }
  if (start) start.hidden = connected;
}

async function checkBridge(timeoutMs = 1500) {
  try {
    await api('/api/health', {timeoutMs});
    setBridgeState(true);
    return true;
  } catch (_) {
    setBridgeState(false);
    return false;
  }
}

async function bridgeLauncherPath() {
  const folder = await storage.localFileSystem.getPluginFolder();
  const base = String(folder.nativePath || '').replace(/\/$/, '');
  if (!base) throw new Error('プラグインの保存場所を取得できません');
  return `${base}/start-bridge`;
}

async function startBridgeServer({automatic = false} = {}) {
  if (bridgeStartInFlight) return bridgeStartInFlight;
  bridgeStartInFlight = (async () => {
    if (await checkBridge(700)) {
      await refreshNarrationJobs();
      return true;
    }
    const button = $('#start-bridge');
    if (button) {
      button.disabled = true;
      button.textContent = '起動中…';
      button.hidden = false;
    }
    setBridgeState(false, '起動中');
    try {
      const result = await require('uxp').shell.openPath(
        await bridgeLauncherPath(),
        'Premiere制作支援のローカル連携サーバーを起動します。'
      );
      if (result) throw new Error(result);
      const deadline = Date.now() + BRIDGE_START_TIMEOUT_MS;
      while (Date.now() < deadline) {
        if (await checkBridge(900)) {
          await refreshNarrationJobs();
          showBootStatus('パネル起動済み');
          return true;
        }
        await new Promise(resolve => setTimeout(resolve, 350));
      }
      throw new Error('連携サーバーの起動を確認できませんでした');
    } catch (error) {
      setBridgeState(false);
      if (!automatic) setMessage(`連携サーバーを起動できませんでした: ${error.message || error}`);
      try { window.uxpDiagnostics?.report(error, 'bridge-start', {automatic}); } catch (_) {}
      return false;
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = '連携サーバーを起動';
      }
    }
  })();
  try {
    return await bridgeStartInFlight;
  } finally {
    bridgeStartInFlight = null;
  }
}

async function asFolder(item) {
  try {
    const folder = ppro.FolderItem.cast(item);
    const children = await folder.getItems();
    return {folder, children};
  } catch (_) {
    return null;
  }
}

async function collectItems(items, output = new Map()) {
  for (const item of items) {
    const folderResult = await asFolder(item);
    if (folderResult) {
      await collectItems(folderResult.children, output);
      continue;
    }
    try {
      const clip = ppro.ClipProjectItem.cast(item);
      const path = await clip.getMediaFilePath();
      const extension = String(path).split('.').pop().toLowerCase();
      if (!path || !MEDIA_EXTENSIONS.has(extension)) continue;
      const id = String(item.getId());
      output.set(id, {id, name: item.name, path, extension, clip, transcript: null, transcriptStatus: 'checking'});
    } catch (_) {
      // Sequences, graphics, stills and unsupported project items are intentionally skipped.
    }
  }
  return [...output.values()];
}

async function loadTranscript(asset) {
  try {
    const raw = await ppro.Transcript.exportToJSON(asset.clip);
    const transcript = JSON.parse(raw);
    const hasWords = transcript.segments?.some(segment =>
      segment.words?.some(word => String(word.text || word.word || '').trim())
    );
    if (!hasWords) throw new Error('Premiere文字起こしに本文単語がまだありません');
    asset.transcript = transcript;
    asset.transcriptStatus = 'ready';
  } catch (error) {
    asset.transcript = null;
    asset.transcriptStatus = 'wait';
    try {
      window.uxpDiagnostics?.report(error, 'premiere-transcript.export', {
        asset_id: asset.id,
        asset_name: asset.name,
        expected_wait_state: true,
      });
    } catch (_) {}
  }
}

async function scanSelection() {
  setMessage('選択内容を確認しています…');
  state.project = await ppro.Project.getActiveProject();
  if (!state.project) throw new Error('開いているPremiereプロジェクトがありません');
  const selection = await ppro.ProjectUtils.getSelection(state.project);
  const selectedItems = await selection.getItems();
  state.assets = await collectItems(selectedItems);
  state.assetRefs.clear();
  for (const asset of state.assets) {
    state.assetRefs.set(asset.id, asset.clip);
    await loadTranscript(asset);
  }
  renderSelection();
  updateStartState();
  setMessage('');
}

function renderSelection() {
  const ready = state.assets.filter(asset => asset.transcriptStatus === 'ready').length;
  const wait = state.assets.filter(asset => asset.transcriptStatus === 'wait').length;
  $('#selection-summary').textContent = state.assets.length
    ? `対象 ${state.assets.length}件／文字起こし済み ${ready}件／待ち ${wait}件`
    : '対象となる動画・音声素材がありません';
  const container = $('#selected-assets');
  container.replaceChildren();
  for (const asset of state.assets) {
    const row = document.createElement('div');
    row.className = 'asset';
    const label = asset.transcriptStatus === 'ready' ? '文字起こし取得可能' : 'Premiere文字起こし待ち';
    row.innerHTML = `<div class="asset-name" title="${asset.path}">${asset.name}</div>
      <div class="asset-state ${asset.transcriptStatus === 'ready' ? 'ready' : 'wait'}">${label}</div>`;
    container.append(row);
  }
}

function updateStartState() {
  $('#start').disabled = !state.assets.length || !state.referenceDir;
}

async function chooseReferenceFolder() {
  const folder = await storage.localFileSystem.getFolder();
  if (!folder) return;
  state.referenceDir = folder.nativePath;
  try { localStorage.setItem('referenceDir', state.referenceDir); } catch (_) {}
  $('#reference-path').textContent = state.referenceDir;
  updateStartState();
}

async function findReviewPreset() {
  if (cachedReviewPreset) return cachedReviewPreset;
  for (const path of REVIEW_PRESET_CANDIDATES) {
    try {
      await fs.readFile(path);
      cachedReviewPreset = path;
      return path;
    } catch (_) {}
  }
  throw new Error('Media Encoder用のH.264 720pプリセットが見つかりません');
}

async function queueReviewProxy(asset, job) {
  try {
    const manager = await ppro.EncoderManager.getManager();
    if (!manager || !manager.isAMEInstalled) {
      throw new Error('Adobe Media Encoderが見つかりません');
    }
    const preset = await findReviewPreset();
    const queued = await manager.encodeProjectItem(
      asset.clip,
      job.review_proxy_path,
      preset,
      0,
      true,
      true,
    );
    if (!queued) throw new Error('Media Encoderが処理を受け付けませんでした');
    await api(`/api/jobs/${job.id}/proxy-queued`, {method: 'POST', body: '{}'});
    return true;
  } catch (error) {
    try {
      await api(`/api/jobs/${job.id}/proxy-error`, {
        method: 'POST',
        body: JSON.stringify({error: `${error?.name || 'Error'}: ${error?.message || error}`}),
      });
    } catch (bridgeError) {
      reportError('encoder-fallback', bridgeError, {asset: asset.name, job_id: job.id});
    }
    try { window.uxpDiagnostics?.report(error, 'encoder.queue', {asset: asset.name, output: job.review_proxy_path}); } catch (_) {}
    return false;
  }
}

async function submitAssets(assets) {
  if (!assets.length) return;
  const response = await api('/api/batch', {
    method: 'POST',
    body: JSON.stringify({
      project_guid: String(state.project.guid),
      project_name: state.project.name,
      project_path: state.project.path,
      reference_dir: state.referenceDir,
      assets: assets.map(asset => ({
        id: asset.id,
        name: asset.name,
        path: asset.path,
        transcript: asset.transcript,
        proxy_mode: VIDEO_EXTENSIONS.has(asset.extension) ? 'ame' : 'direct',
      })),
    }),
  });
  const jobsByAsset = new Map((response.jobs || []).map(job => [String(job.asset_id), job]));
  let encoderQueued = 0;
  let fallback = 0;
  for (const asset of assets) {
    const job = jobsByAsset.get(String(asset.id));
    if (!job || job.status !== 'awaiting_proxy') continue;
    if (await queueReviewProxy(asset, job)) encoderQueued += 1;
    else fallback += 1;
  }
  for (const asset of assets) asset.submitted = true;
  if (encoderQueued) setMessage(`${encoderQueued}件をMedia Encoderに投入しました。`);
  if (fallback) setMessage(`${fallback}件はMedia Encoder失敗のため予備変換で続行します。`);
}

async function startBatch() {
  if (!(await checkBridge())) throw new Error('先に「UXP連携サーバーを開く」を起動してください');
  const ready = state.assets.filter(asset => asset.transcriptStatus === 'ready' && !asset.submitted);
  await submitAssets(ready);
  const waiting = state.assets.filter(asset => asset.transcriptStatus === 'wait').length;
  setMessage(waiting
    ? `${ready.length}件を開始しました。残り${waiting}件はPremiere文字起こし完了後に自動開始します。`
    : `${ready.length}件の解析を開始しました。`);
  startTranscriptWatcher();
  await refreshJobs();
}

function startTranscriptWatcher() {
  clearInterval(state.watcher);
  state.watcher = setInterval(async () => {
    const pending = state.assets.filter(asset => !asset.submitted && asset.transcriptStatus === 'wait');
    if (!pending.length) return clearInterval(state.watcher);
    for (const asset of pending) await loadTranscript(asset);
    const becameReady = pending.filter(asset => asset.transcriptStatus === 'ready');
    if (becameReady.length) {
      try {
        await submitAssets(becameReady);
        renderSelection();
        await refreshJobs();
      } catch (error) { reportError('transcript-watcher.submit', error); }
    }
  }, 5000);
}

function statusClass(status) {
  if (status === 'review_ready' || status === 'ready_to_writeback' || status === 'confirmed') return 'ready';
  if (status === 'applied_pending_verification') return 'wait';
  if (status === 'error' || status === 'invalid_transcript' || status === 'writeback_failed' || status === 'restore_failed') return 'error';
  if (status === 'queued' || status === 'awaiting_proxy' || status === 'proxy_queued' || status === 'stopped') return 'wait';
  return '';
}

function narrationStateLabel(job) {
  if (job.captionState === 'requested') return 'SRT貼り直し待ち';
  const labels = {
    editing: 'ブラウザで新規制作中',
    audio_editing: '生成済み音声を修正中',
    audio_update_ready: '音声修正の反映待ち',
    diff_review: '差分音声の確認待ち',
    approved: 'Premiere反映待ち',
    update_ready: '台本差分の反映待ち',
    applying: 'Premiereへ反映中',
    applied: 'Premiere反映済み',
    error: 'エラー',
  };
  return labels[job.state] || job.state || '状態不明';
}

function narrationStateClass(job) {
  if (job.captionState === 'requested') return 'wait';
  const state = job.state;
  if (state === 'approved' || state === 'update_ready' || state === 'audio_update_ready' || state === 'applied') return 'ready';
  if (state === 'editing' || state === 'audio_editing' || state === 'diff_review' || state === 'applying') return 'wait';
  if (state === 'error') return 'error';
  return '';
}

function narrationJobSortKey(job) {
  return String(job.updatedAt || job.id || '');
}

function formatNarrationTimestamp(value) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return '';
  return `${Number(match[1])}年${Number(match[2])}月${Number(match[3])}日 ${Number(match[4])}:${match[5]}`;
}

function latestNarrationJobPerCase(jobs) {
  const latest = new Map();
  for (const job of jobs) {
    const caseKey = normalizedPath(job.caseFolder || `job:${job.id}`);
    const current = latest.get(caseKey);
    if (!current || narrationJobSortKey(job) > narrationJobSortKey(current)) latest.set(caseKey, job);
  }
  return Array.from(latest.values()).sort((left, right) =>
    narrationJobSortKey(right).localeCompare(narrationJobSortKey(left)));
}

function visibleNarrationJobs(jobs) {
  const latestByCase = latestNarrationJobPerCase(jobs);
  const projectPath = normalizedPath(state.project?.path || '');
  if (!projectPath) return latestByCase.slice(0, 8);

  // A Premiere project may sit in a subfolder such as 03_プロジェクトファイル.
  // Select the most specific case folder that contains it. This excludes
  // accidental selections of a parent folder and old jobs from other cases.
  const matching = latestByCase.filter(job => {
    const casePath = normalizedPath(job.caseFolder || '').replace(/\/$/, '');
    return casePath && (projectPath === casePath || projectPath.startsWith(`${casePath}/`));
  });
  if (!matching.length) return latestByCase.slice(0, 8);
  const mostSpecificLength = Math.max(...matching.map(job => normalizedPath(job.caseFolder || '').length));
  return matching.filter(job => normalizedPath(job.caseFolder || '').length === mostSpecificLength).slice(0, 1);
}

async function loadNarrationJobs() {
  const container = $('#narration-jobs');
  if (!container) return [];
  let response;
  try { response = await api('/api/narration/jobs'); }
  catch (_) { container.innerHTML = '<p class="empty">ナレーション連携サーバーを起動すると、ここに状態が表示されます。</p>'; return []; }
  const jobs = response.jobs || [];
  const visibleJobs = visibleNarrationJobs(jobs);
  container.replaceChildren();
  if (!visibleJobs.length) { container.innerHTML = '<p class="empty">ナレーション制作はまだ開始していません</p>'; return []; }
  for (const job of visibleJobs) {
    const row = document.createElement('div');
    row.className = 'asset';
    const approvedAt = job.state === 'update_ready' ? formatNarrationTimestamp(job.updatedAt) : '';
    row.innerHTML = `<div class="asset-top"><div class="asset-name">${job.caseName || '案件名不明'}</div></div>
      <div class="asset-state ${narrationStateClass(job)}">${narrationStateLabel(job)} — ${job.message || ''}</div>
      ${approvedAt ? `<div class="asset-meta">ブラウザ承認：${approvedAt}</div>` : ''}
      <div class="asset-actions"></div>`;
    const actions = row.querySelector('.asset-actions');
    if (job.browserUrl && (job.state === 'editing' || job.state === 'audio_editing')) {
      const open = document.createElement('button');
      open.textContent = 'ブラウザを開く';
      open.addEventListener('click', () => require('uxp').shell.openExternal(job.browserUrl));
      actions.append(open);
    }
    if (job.state === 'diff_review') {
      const review = document.createElement('button');
      review.textContent = '差分音声を確認';
      review.addEventListener('click', async () => {
        review.disabled = true;
        setMessage('差分音声の確認画面を開いています…');
        try {
          await api(`/api/narration/jobs/${job.id}/diff-review-open`, {method: 'POST', body: '{}'});
          setMessage('ブラウザで音声を確認し、必要なら修正・再生成してから「差分音声を承認」を押してください。');
          setTimeout(refreshNarrationJobs, 1000);
        } catch (error) {
          review.disabled = false;
          reportError('narration-diff-review-open', error, {job_id: job.id});
        }
      });
      actions.append(review);
    }
    if (job.state === 'approved' || job.state === 'update_ready' || job.state === 'audio_update_ready' || job.state === 'applied') {
      const ready = document.createElement('span');
      ready.className = 'hint';
      if (job.captionState === 'requested') {
        ready.classList.add('caption-pending');
        ready.textContent = '必須：古いキャプショントラックを削除し、「ナレーションTimeline配置」で「差分更新後のSRTを反映」を押してください。';
      } else {
        ready.textContent = '反映データ準備済み。SRT配置のみ互換パネルを使用します。';
      }
      actions.append(ready);
      const transcript = document.createElement('button');
      transcript.textContent = '個別WAVへ文字起こしを登録';
      transcript.addEventListener('click', () => importNarrationTranscripts(job).catch(error => reportError('narration-transcript-import', error, {job_id: job.id})));
      actions.append(transcript);
      const update = document.createElement('button');
      update.textContent = '修正版台本を比較';
      update.addEventListener('click', () => chooseNarrationRevision(job).catch(error => reportError('narration-diff-plan', error, {job_id: job.id})));
      actions.append(update);
      if (job.state === 'update_ready') {
        const applyUpdate = document.createElement('button');
        applyUpdate.textContent = 'Premiereへ差分反映';
        applyUpdate.addEventListener('click', () => applyNarrationDiffToPremiere(job).catch(error => reportError('narration-diff-premiere', error, {job_id: job.id})));
        actions.append(applyUpdate);
      }
      if (job.state === 'audio_update_ready') {
        const applyAudioUpdate = document.createElement('button');
        applyAudioUpdate.textContent = 'Premiereへ音声修正を反映';
        applyAudioUpdate.addEventListener('click', () => applyNarrationAudioRevisionToPremiere(job).catch(error => reportError('narration-audio-revision-premiere', error, {job_id: job.id})));
        actions.append(applyAudioUpdate);
      }
      if (job.state === 'applied' || job.state === 'audio_update_ready') {
        const editAudio = document.createElement('button');
        editAudio.textContent = job.state === 'audio_update_ready' ? '音声修正画面を開き直す' : '生成済み音声を修正';
        editAudio.addEventListener('click', () => openNarrationAudioRevision(job).catch(error => reportError('narration-audio-revision-open', error, {job_id: job.id})));
        actions.append(editAudio);
      }
      if (job.state === 'applied') {
        const compactAudio = document.createElement('button');
        compactAudio.textContent = '音声とSRTの隙間を詰める';
        compactAudio.addEventListener('click', () => compactAppliedNarration(job).catch(error => reportError('narration-compact-audio', error, {job_id: job.id})));
        actions.append(compactAudio);
      }
    }
    container.append(row);
  }
  const planned = visibleJobs.find(job => job.diffPlanId);
  if (planned && visibleNarrationDiffId !== planned.diffPlanId) {
    const restoreSequence = ++narrationDiffRestoreSequence;
    try {
      const saved = await api(`/api/narration/jobs/${planned.id}/diff-latest`, {method: 'POST', body: '{}'});
      if (restoreSequence !== narrationDiffRestoreSequence) return jobs;
      const restoredId = String(saved.plan?.updateId || '');
      if (restoredId && restoredId === visibleNarrationDiffId) return jobs;
      renderNarrationDiff(planned, saved.plan);
    } catch (_) {}
  }
  return jobs;
}

async function refreshNarrationJobs() {
  // Browser approval changes the job outside Premiere. Share one in-flight
  // request so the periodic refresh and a manual refresh cannot race and
  // repaint the panel with an older response.
  if (narrationRefreshInFlight) return narrationRefreshInFlight;
  narrationRefreshInFlight = loadNarrationJobs();
  try {
    return await narrationRefreshInFlight;
  } finally {
    narrationRefreshInFlight = null;
  }
}

async function chooseNarrationRevision(job) {
  narrationDiffRestoreSequence += 1;
  const file = await storage.localFileSystem.getFileForOpening({types: ['docx', 'pdf']});
  if (!file) return;
  const result = await api(`/api/narration/jobs/${job.id}/diff-plan`, {
    method: 'POST', body: JSON.stringify({scriptPath: file.nativePath}), timeoutMs: 30000,
  });
  const changes = result.plan?.changes || [];
  renderNarrationDiff(job, result.plan);
  if (!changes.length) alert('ナレーション差分はありません。');
  setMessage(`修正版台本との差分を作成しました: ${changes.length}件`);
}

function renderNarrationDiff(job, plan) {
  const panel = $('#narration-diff');
  const changes = plan?.changes || [];
  // Premiere 25.6のUXPではreplaceChildren/innerHTMLによる全消去が
  // 再描画時に取りこぼされることがあるため、子要素を明示的に外す。
  while (panel.firstChild) panel.removeChild(panel.firstChild);
  panel.hidden = false;
  visibleNarrationDiffId = String(plan?.updateId || '');
  try { panel.scrollIntoView({block: 'start'}); } catch (_) {}
  const toolbar = document.createElement('div');
  toolbar.className = 'diff-toolbar';
  const heading = document.createElement('h3');
  heading.textContent = `${job.caseName || '案件'}：修正版台本の差分 ${changes.length}件`;
  const chooseAgain = document.createElement('button');
  chooseAgain.className = 'secondary';
  chooseAgain.textContent = '修正版台本を選び直す';
  chooseAgain.addEventListener('click', async () => {
    chooseAgain.disabled = true;
    setMessage('修正版台本を選択してください…');
    try {
      await chooseNarrationRevision(job);
    } finally {
      chooseAgain.disabled = false;
    }
  });
  toolbar.append(heading, chooseAgain);
  panel.append(toolbar);
  if (!changes.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = '更新対象はありません。';
    panel.append(empty);
    return;
  }
  const labels = {modified: '変更', added: '追加', deleted: '削除'};
  changes.forEach((change, index) => {
    const row = document.createElement('div');
    row.className = 'diff-row';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = true;
    check.dataset.diffIndex = String(index);
    const kind = document.createElement('span');
    kind.className = 'diff-kind';
    kind.textContent = `${labels[change.type] || change.type} ${change.id || ''}`;
    const text = document.createElement('span');
    text.className = 'diff-text';
    if (change.type === 'modified') {
      const before = document.createElement('span');
      before.className = 'diff-before';
      before.textContent = change.before || '';
      text.append(before, document.createElement('br'), document.createTextNode(change.after || ''));
    } else {
      text.textContent = change.text || change.before || '';
    }
    row.append(check, kind, text);
    row.addEventListener('click', event => {
      if (event.target !== check) check.checked = !check.checked;
    });
    panel.append(row);
  });
  const apply = document.createElement('button');
  apply.className = 'primary';
  apply.textContent = '選択した差分の音声を生成';
  apply.addEventListener('click', async () => {
    const selected = [...panel.querySelectorAll('[data-diff-index]:checked')]
      .map(input => Number(input.dataset.diffIndex));
    if (!selected.length) { alert('更新対象を1件以上選んでください。'); return; }
    if (!confirm(`選択した${selected.length}件を管理データへ反映し、必要な音声だけ生成します。実行しますか？`)) return;
    apply.disabled = true;
    apply.textContent = '差分音声を生成中…';
    setMessage('差分を適用し、変更・追加分の音声を生成しています…');
    try {
      const response = await api(`/api/narration/jobs/${job.id}/diff-apply`, {
        method: 'POST',
        body: JSON.stringify({updateId: plan.updateId, selected}),
        timeoutMs: 900000,
      });
      const count = response.result?.generatedIds?.length || 0;
      panel.hidden = true;
      visibleNarrationDiffId = '';
      setMessage(`差分${selected.length}件を適用し、${count}件の音声を更新しました。ブラウザで1件ずつ確認してください。承認するまでPremiereには反映されません。`);
      await refreshNarrationJobs();
    } catch (error) {
      apply.disabled = false;
      apply.textContent = '選択した差分の音声を生成';
      reportError('narration-diff-apply', error, {job_id: job.id, update_id: plan.updateId});
    }
  });
  panel.append(apply);
}

async function narrationTrackItems(track) {
  const items = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
  const timed = [];
  for (const trackItem of items || []) {
    const start = await trackItem.getStartTime();
    const end = await trackItem.getEndTime();
    const projectItem = ppro.ClipProjectItem.cast(await trackItem.getProjectItem());
    timed.push({trackItem, path: await projectItem.getMediaFilePath(), start: Number(start.seconds), startTime: start, endTime: end});
  }
  timed.sort((first, second) => first.start - second.start);
  return timed;
}

async function compactNarrationTrack(project, audioTrack) {
  const before = await narrationTrackItems(audioTrack);
  if (before.length < 2) return {moved: 0, items: before};
  const moves = [];
  let cursor = before[0].endTime;
  for (let index = 1; index < before.length; index += 1) {
    const item = before[index];
    const duration = item.endTime.subtract(item.startTime);
    const offset = cursor.subtract(item.startTime);
    if (String(offset.ticks) !== '0') moves.push({trackItem: item.trackItem, offset});
    cursor = cursor.add(duration);
  }
  if (moves.length) {
    let transactionResult = false;
    project.lockedAccess(() => {
      const actions = moves.map(move => move.trackItem.createMoveAction(move.offset));
      transactionResult = project.executeTransaction(compound => {
        for (const action of actions) compound.addAction(action);
      }, 'ナレーション音声の隙間を詰める');
    });
    if (transactionResult === false) throw new Error('Premiereが音声の隙間補正を受け付けませんでした');
  }
  const after = await narrationTrackItems(audioTrack);
  for (let index = 1; index < after.length; index += 1) {
    if (String(after[index - 1].endTime.ticks) !== String(after[index].startTime.ticks)) {
      throw new Error(`${index + 1}番目のナレーション前に隙間が残っています`);
    }
  }
  return {moved: moves.length, items: after};
}

function narrationTimingPayload(audioItems, trackItems) {
  if (!Array.isArray(audioItems) || audioItems.length !== trackItems.length) {
    throw new Error('SRT同期用のナレーション数が一致しません');
  }
  return audioItems.map((item, index) => ({
    id: item.id,
    startSeconds: Number(trackItems[index].startTime.seconds),
    endSeconds: Number(trackItems[index].endTime.seconds),
  }));
}

async function synchronizeNarrationSrt(job, manifest, trackItems) {
  return api(`/api/narration/jobs/${job.id}/sync-srt`, {
    method: 'POST',
    body: JSON.stringify({timings: narrationTimingPayload(manifest.audioItems || [], trackItems)}),
    timeoutMs: 30000,
  });
}

async function publishNarrationExport(job, manifest, binding) {
  const outputDirectory = String(manifest.srtPath || '').split('/').slice(0, -1).join('/');
  await fs.writeFile(CEP_NARRATION_EXPORT, JSON.stringify({
    ...manifest,
    schemaVersion: 3,
    mode: 'audio',
    audioPath: '',
    jobPath: `${String(job.caseFolder || '').replace(/\/$/, '')}/.premiere-tools/jobs/${job.id}`,
    outputDirectory,
    premiereBinding: binding,
  }, null, 2), {encoding: 'utf-8'});
}

async function compactAppliedNarration(job) {
  const payload = await api(`/api/narration/jobs/${job.id}/manifest`, {method: 'POST', body: '{}'});
  let manifest = payload.manifest || {};
  try {
    const currentExport = JSON.parse(await fs.readFile(CEP_NARRATION_EXPORT, 'utf8'));
    if (String(currentExport.jobPath || '').endsWith(`/jobs/${job.id}`)) {
      manifest = {...manifest, ...currentExport};
    }
  } catch (_) {}
  const binding = manifest.premiereBinding || {};
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error('Premiereプロジェクトを開いてください');
  if (binding.projectPath && normalizedPath(project.path) !== normalizedPath(binding.projectPath)) {
    throw new Error('開いているPremiereプロジェクトが配置済みデータと異なります');
  }
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error('ナレーションが配置されたシーケンスを開いてください');
  const trackIndex = Number(binding.audioTrack) - 1;
  const trackCount = await sequence.getAudioTrackCount();
  if (!Number.isInteger(trackIndex) || trackIndex < 0 || trackIndex >= trackCount) {
    throw new Error('登録されたナレーション音声トラックがありません');
  }
  const audioTrack = await sequence.getAudioTrack(trackIndex);
  const current = await narrationTrackItems(audioTrack);
  const expectedPaths = (manifest.audioItems || []).map(item => item.audioPath);
  if (!samePathList(current.map(item => item.path), expectedPaths)) {
    throw new Error('Aトラックの内容が反映済みナレーションと異なるため、安全のため変更しません');
  }
  if (!confirm(`A${trackIndex + 1}の${current.length}件を、直前クリップの実際の終端へ詰め、SRTも同じ時刻へ同期します。\n音声の順序と内容は変更しません。実行しますか？`)) return;
  setMessage(`A${trackIndex + 1}の音声間隔とSRTを同期しています…`);
  const result = await compactNarrationTrack(project, audioTrack);
  const synchronized = await synchronizeNarrationSrt(job, manifest, result.items);
  const synchronizedManifest = synchronized.manifest || {...manifest, srtPath: synchronized.srtPath};
  await publishNarrationExport(job, synchronizedManifest, binding);
  if (!await project.save()) throw new Error('Premiereプロジェクトを保存できませんでした');
  setMessage(`A${trackIndex + 1}とSRTを同期しました（${result.moved}件移動）。旧キャプショントラックを削除してSRTを反映してください。`);
  alert(`音声とSRTの同期が完了しました。\n${result.moved}件を移動し、同期済みSRTを作成しました。\n\n旧キャプショントラックを削除後、ナレーションTimeline配置パネルでSRTを反映してください。`);
}

async function findDirectNarrationBin(parent, name) {
  for (const item of await parent.getItems()) {
    const folderResult = await asFolder(item);
    if (folderResult && String(item.name) === String(name)) return folderResult.folder;
  }
  return null;
}

async function ensureDirectNarrationBin(project, parent, name) {
  const existing = await findDirectNarrationBin(parent, name);
  if (existing) return existing;
  let transactionResult = false;
  project.lockedAccess(() => {
    const createAction = parent.createBinAction(name, false);
    transactionResult = project.executeTransaction(compound => {
      compound.addAction(createAction);
    }, `ナレーションビン「${name}」を作成`);
  });
  if (transactionResult === false) throw new Error(`「${name}」ビンを作成できませんでした`);
  const created = await findDirectNarrationBin(parent, name);
  if (!created) throw new Error(`作成した「${name}」ビンを確認できませんでした`);
  return created;
}

function projectItemId(item) {
  try { return String(ppro.ProjectItem.cast(item).getId()); } catch (_) { return ''; }
}

function moveNarrationClipsToBin(project, clips, targetBin) {
  const targetId = projectItemId(targetBin);
  const moves = [];
  for (const clip of clips) {
    const projectItem = ppro.ProjectItem.cast(clip);
    const parent = projectItem.getParentBin();
    if (!parent || projectItemId(parent) === targetId) continue;
    moves.push({parent, projectItem});
  }
  if (!moves.length) return;
  let transactionResult = false;
  project.lockedAccess(() => {
    const actions = moves.map(move => move.parent.createMoveItemAction(move.projectItem, targetBin));
    transactionResult = project.executeTransaction(compound => {
      for (const action of actions) compound.addAction(action);
    }, '差分ナレーションWAVを修正ビンへ移動');
  });
  if (transactionResult === false) throw new Error('差分ナレーションWAVを修正ビンへ移動できませんでした');
}

function samePathList(first, second) {
  return first.length === second.length && first.every((value, index) => normalizedPath(value) === normalizedPath(second[index]));
}

async function applyNarrationUpdateToPremiere(job, updateKind) {
  const audioRevision = updateKind === 'audio-revision';
  const manifestEndpoint = audioRevision ? 'audio-update-manifest' : 'diff-manifest';
  const payload = await api(`/api/narration/jobs/${job.id}/${manifestEndpoint}`, {method: 'POST', body: '{}'});
  const manifest = payload.manifest || {};
  const binding = manifest.premiereBinding || {};
  if (!Array.isArray(binding.items) || !binding.items.length) {
    throw new Error('このタイムラインは更新対象として未登録です。先にナレーションTimeline配置パネルで現在のタイムラインを登録してください。');
  }
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error('Premiereプロジェクトを開いてください');
  if (binding.projectPath && normalizedPath(project.path) !== normalizedPath(binding.projectPath)) {
    throw new Error('開いているPremiereプロジェクが差分更新対象と異なります');
  }
  const sequence = await project.getActiveSequence();
  if (!sequence) throw new Error('ナレーション修正を反映するシーケンスを開いてください');
  const trackIndex = Number(binding.audioTrack) - 1;
  const trackCount = await sequence.getAudioTrackCount();
  if (!Number.isInteger(trackIndex) || trackIndex < 0 || trackIndex >= trackCount) {
    throw new Error('登録されたナレーション音声トラックがありません');
  }
  const audioTrack = await sequence.getAudioTrack(trackIndex);
  const current = await narrationTrackItems(audioTrack);
  const expectedPaths = [...binding.items]
    .sort((first, second) => Number(first.order) - Number(second.order))
    .map(item => item.mediaPath);
  const recoveringEmptyTrack = current.length === 0;
  if (!recoveringEmptyTrack && !samePathList(current.map(item => item.path), expectedPaths)) {
    throw new Error('登録後にナレーショントラックの中身が変わっています。安全のため変更しません。');
  }
  const updatedItems = manifest.audioItems || [];
  if (!updatedItems.length) throw new Error('更新後のナレーション音声がありません');
  const recoveryNote = recoveringEmptyTrack ? '\n前回の失敗で空になったトラックを復旧します。' : '';
  if (!confirm(`A${trackIndex + 1}のナレーション${current.length}件を、修正後の${updatedItems.length}件で再構成します。${recoveryNote}\n他の音声トラックは変更しません。実行しますか？`)) return;

  state.project = project;
  setMessage(`${audioRevision ? '修正音声' : '差分音声'}をPremiereへ読み込んでいます…`);
  let root = ppro.FolderItem.cast(await project.getRootItem());
  const narrationBin = await ensureDirectNarrationBin(project, root, '仮ナレーション');
  const revisionBin = await ensureDirectNarrationBin(project, narrationBin, manifest.audioBinName || `${job.caseName || '案件'}_差分`);
  let rootItems = await root.getItems();
  const missingPaths = [];
  for (const item of updatedItems) {
    if (!await findNarrationClipByMediaPath(rootItems, item.audioPath)) missingPaths.push(item.audioPath);
  }
  if (missingPaths.length) {
    if (!await project.importFiles(missingPaths, true, revisionBin, false)) {
      throw new Error('差分音声をPremiereプロジェクへ読み込めませんでした');
    }
    root = ppro.FolderItem.cast(await project.getRootItem());
    rootItems = await root.getItems();
  }

  const projectItems = [];
  const changedIds = new Set(audioRevision
    ? (manifest.changedIds || []).map(value => String(value || ''))
    : (manifest.operations || [])
      .filter(operation => operation.type === 'modified' || operation.type === 'added')
      .map(operation => String(operation.id || '')));
  for (const item of updatedItems) {
    const clip = await findNarrationClipByMediaPath(rootItems, item.audioPath);
    if (!clip) throw new Error(`${item.id}のWAVをPremiereプロジェク内で確認できません`);
    projectItems.push(clip);
    if (changedIds.has(String(item.id)) && item.transcriptPath) {
      const transcript = await readNarrationTranscript(item.transcriptPath);
      applyTranscriptJson(clip, transcript, `ナレーション ${item.id} の文字起こしを登録`);
      if (!await waitForNarrationTranscript(clip, transcript)) {
        throw new Error(`${item.id}の文字起こし登録を確認できませんでした`);
      }
    }
  }
  moveNarrationClipsToBin(
    project,
    projectItems.filter((_, index) => changedIds.has(String(updatedItems[index].id))),
    revisionBin,
  );

  setMessage(`A${trackIndex + 1}のナレーションを${audioRevision ? '音声修正' : '差分更新'}中…`);
  let transactionResult = false;
  project.lockedAccess(() => {
    const editor = ppro.SequenceEditor.getEditor(sequence);
    const overwriteActions = [];
    for (let index = 0; index < projectItems.length; index += 1) {
      const at = ppro.TickTime.createWithSeconds(Number(updatedItems[index].startSeconds));
      // Premiere 25.6 rejects -1 for videoTrackIndex even for audio-only WAV.
      // Track 0 is harmless for an audio-only ProjectItem and the audio goes to trackIndex.
      overwriteActions.push(editor.createOverwriteItemAction(ppro.ProjectItem.cast(projectItems[index]), at, 0, trackIndex));
    }
    let removeAction = null;
    if (current.length) {
      let selection = null;
      ppro.TrackItemSelection.createEmptySelection(value => { selection = value; });
      if (!selection) throw new Error('ナレーションクリップの選択を作成できません');
      for (const item of current) selection.addItem(item.trackItem, false);
      removeAction = editor.createRemoveItemsAction(selection, false, ppro.Constants.MediaType.AUDIO, false);
    }
    transactionResult = project.executeTransaction(compound => {
      if (removeAction) compound.addAction(removeAction);
      for (const action of overwriteActions) compound.addAction(action);
    }, audioRevision ? '生成済みナレーション音声を修正' : 'ナレーション差分更新');
  });
  if (transactionResult === false) throw new Error(`Premiereがナレーション${audioRevision ? '音声修正' : '差分更新'}を受け付けませんでした`);

  const compacted = await compactNarrationTrack(project, await sequence.getAudioTrack(trackIndex));
  const after = compacted.items;
  const updatedPaths = updatedItems.map(item => item.audioPath);
  if (!samePathList(after.map(item => item.path), updatedPaths)) {
    throw new Error('更新後のナレーショントラックが更新データと一致しません');
  }
  if (!await project.save()) throw new Error('Premiereプロジェクトを保存できませんでした');
  const newBinding = {
    schemaVersion: 1,
    projectPath: project.path,
    audioTrack: trackIndex + 1,
    items: updatedItems.map((item, index) => ({id: item.id, mediaPath: item.audioPath, order: index})),
    recordedAt: new Date().toISOString(),
  };
  const completeEndpoint = audioRevision ? 'audio-update-complete' : 'diff-complete';
  await api(`/api/narration/jobs/${job.id}/${completeEndpoint}`, {
    method: 'POST', body: JSON.stringify({premiereBinding: newBinding}), timeoutMs: 30000,
  });
  const synchronized = await synchronizeNarrationSrt(job, manifest, after);
  const synchronizedManifest = synchronized.manifest || {...manifest, srtPath: synchronized.srtPath};
  await publishNarrationExport(job, synchronizedManifest, newBinding);
  const completedLabel = audioRevision ? '生成済み音声の修正' : 'ナレーション音声の差分更新';
  setMessage(`${completedLabel}と文字起こし登録を完了し、Premiereプロジェクトを保存しました。`);
  alert(`${completedLabel}が完了しました。\n旧キャプショントラックを削除後、ナレーションTimeline配置パネルで新しいSRTを反映してください。`);
  await refreshNarrationJobs();
}

async function applyNarrationDiffToPremiere(job) {
  return applyNarrationUpdateToPremiere(job, 'script-diff');
}

async function applyNarrationAudioRevisionToPremiere(job) {
  return applyNarrationUpdateToPremiere(job, 'audio-revision');
}

async function openNarrationStudio() {
  await api('/api/narration/open', {method: 'POST', body: '{}'});
  setMessage('案件フォルダの選択画面を開きました。選択後にブラウザ制作画面が表示されます。');
  setTimeout(() => refreshNarrationJobs().catch(() => {}), 1200);
}

async function openNarrationAudioRevision(job) {
  setMessage(`${job.caseName || '案件'}の生成済み音声を開いています…`);
  await api(`/api/narration/jobs/${job.id}/audio-revision-open`, {method: 'POST', body: '{}'});
  setMessage('ブラウザで音声を再生し、必要な行だけ修正・再生成して「音声修正を承認」を押してください。');
  setTimeout(() => refreshNarrationJobs().catch(() => {}), 1000);
}

async function readNarrationTranscript(filePath) {
  const entry = await storage.localFileSystem.getEntryWithUrl('file:' + String(filePath).replace(/\\/g, '/'));
  return JSON.parse(await entry.read({format: storage.formats.utf8}));
}

async function findClipByMediaPath(items, mediaPath) {
  for (const item of items) {
    const folderResult = await asFolder(item);
    if (folderResult) {
      const found = await findClipByMediaPath(folderResult.children, mediaPath);
      if (found) return found;
      continue;
    }
    try {
      const clip = ppro.ClipProjectItem.cast(item);
      if (normalizedPath(await clip.getMediaFilePath()) === normalizedPath(mediaPath)) return clip;
    } catch (_) {}
  }
  return null;
}

async function findNarrationClipByMediaPath(items, mediaPath) {
  const exact = await findClipByMediaPath(items, mediaPath);
  if (exact) return exact;
  const expectedName = String(mediaPath).split('/').pop();
  const candidates = [];
  async function collectMatches(currentItems) {
    for (const item of currentItems) {
      const folderResult = await asFolder(item);
      if (folderResult) { await collectMatches(folderResult.children); continue; }
      try {
        const clip = ppro.ClipProjectItem.cast(item);
        const candidatePath = await clip.getMediaFilePath();
        if (String(candidatePath).split('/').pop() === expectedName) candidates.push({clip, candidatePath});
      } catch (_) {}
    }
  }
  await collectMatches(items);
  // 同名でも、ファイル内容まで一致する旧版だけを使用する。別内容なら誤反映しない。
  for (const candidate of candidates) {
    const result = await api('/api/narration/media-equivalent', {
      method: 'POST', body: JSON.stringify({firstPath: mediaPath, secondPath: candidate.candidatePath}),
    });
    if (result.equivalent) return candidate.clip;
  }
  return null;
}

function transcriptHasWords(raw) {
  const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
  return (value.segments || []).some(segment => (segment.words || []).some(word => String(word.text || word.word || '').trim()));
}

function narrationTranscriptText(raw) {
  const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
  let text = '';
  for (const segment of value.segments || []) {
    for (const word of segment.words || []) text += String(word.text || word.word || '');
  }
  return text;
}

async function waitForNarrationTranscript(clip, expectedJson) {
  const expectedText = narrationTranscriptText(expectedJson);
  let lastError = null;
  // import Actionの完了直後はexportが旧状態を返すことがあるため、最大6秒待つ。
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const actual = await ppro.Transcript.exportToJSON(clip);
      if (narrationTranscriptText(actual) === expectedText) return true;
    } catch (error) { lastError = error; }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  if (lastError) throw new Error(`登録後の文字起こしを読めません: ${lastError?.message || lastError}`);
  return false;
}

async function importNarrationTranscripts(job, askConfirmation = true) {
  const payload = await api(`/api/narration/jobs/${job.id}/manifest`, {method: 'POST', body: '{}'});
  const items = (payload.manifest?.audioItems || []).filter(item => item.audioPath && item.transcriptPath);
  if (!items.length) throw new Error('この書き出しには個別WAV用の文字起こしJSONがありません。新しい方式で承認し直してください。');
  if (askConfirmation && !confirm(`${items.length}件の個別WAVへ、テロップ本文をPremiere文字起こしとして登録します。\n既に文字起こしがあるWAVは上書きせず停止します。実行しますか？`)) return {cancelled: true};
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error('Premiereプロジェクトを開いてください');
  state.project = project;
  const root = ppro.FolderItem.cast(await project.getRootItem());
  const rootItems = await root.getItems();
  const targets = [];
  let alreadyRegistered = 0;
  for (const item of items) {
    const clip = await findNarrationClipByMediaPath(rootItems, item.audioPath);
    if (!clip) throw new Error(`${item.id} のWAVがPremiereプロジェクトにありません。先にナレーションを配置してください。`);
    const json = await readNarrationTranscript(item.transcriptPath);
    try {
      const existing = await ppro.Transcript.exportToJSON(clip);
      if (transcriptHasWords(existing)) {
        if (narrationTranscriptText(existing) === narrationTranscriptText(json)) {
          alreadyRegistered += 1;
          continue;
        }
        throw new Error(`${item.id} には異なるPremiere文字起こしがあります。安全のため上書きしません。`);
      }
    } catch (error) {
      if (String(error?.message || error).includes('異なるPremiere文字起こし')) throw error;
      // 文字起こし未作成のWAVではexport失敗が正常。
    }
    targets.push({item, clip, json});
  }
  setMessage(`${targets.length}件の個別WAVへ文字起こしを登録中…`);
  for (const target of targets) {
    applyTranscriptJson(target.clip, target.json, `ナレーション ${target.item.id} の文字起こしを登録`);
    if (!await waitForNarrationTranscript(target.clip, target.json)) {
      throw new Error(`${target.item.id} の文字起こし登録を確認できませんでした。`);
    }
  }
  if (!await project.save()) throw new Error('Premiereプロジェクトを保存できませんでした');
  setMessage(`${targets.length}件を新規登録、${alreadyRegistered}件を確認して保存しました。`);
  await refreshNarrationJobs();
  return {count: targets.length, alreadyRegistered};
}

async function collectMediaPaths(items, paths = new Set()) {
  for (const item of items) {
    const folderResult = await asFolder(item);
    if (folderResult) {
      await collectMediaPaths(folderResult.children, paths);
      continue;
    }
    try {
      const clip = ppro.ClipProjectItem.cast(item);
      paths.add(normalizedPath(await clip.getMediaFilePath()));
    } catch (_) {}
  }
  return paths;
}

async function resolveNarrationJobForActiveProject() {
  const project = await ppro.Project.getActiveProject();
  if (!project) throw new Error('Premiereプロジェクトを開いてください');
  const root = ppro.FolderItem.cast(await project.getRootItem());
  const mediaPaths = await collectMediaPaths(await root.getItems());
  const response = await api('/api/narration/jobs');
  const jobs = (response.jobs || []).filter(job => ['approved', 'applied'].includes(job.state));
  for (const job of jobs) {
    const payload = await api(`/api/narration/jobs/${job.id}/manifest`, {method: 'POST', body: '{}'});
    const items = (payload.manifest?.audioItems || []).filter(item => item.audioPath && item.transcriptPath);
    if (items.length && items.every(item => mediaPaths.has(normalizedPath(item.audioPath)))) return job;
  }
  throw new Error('アクティブなPremiereプロジェクトに、承認済みナレーションの全WAVが見つかりません。先に「ナレーションTimeline配置」で個別WAVを配置してください。');
}

async function runNarrationTranscriptCommand() {
  const job = await resolveNarrationJobForActiveProject();
  const result = await importNarrationTranscripts(job, true);
  if (result?.cancelled) return;
  alert(`${result.count}件の個別ナレーションWAVへ文字起こしを登録しました。`);
}

window.runNarrationTranscriptCommand = runNarrationTranscriptCommand;

// CEPの「最新データを反映」がWAVを一括読込した後、ここへ依頼を記録する。
// パネルが開いている間は依頼を監視し、手動コマンドなしで文字起こしを登録する。
let narrationTranscriptRequestRunning = false;
async function runPendingNarrationTranscriptRequests() {
  if (narrationTranscriptRequestRunning) return;
  narrationTranscriptRequestRunning = true;
  try {
    const response = await api('/api/narration/jobs');
    const requested = (response.jobs || []).filter(job => job.transcriptState === 'requested');
    if (!requested.length) return;
    const project = await ppro.Project.getActiveProject();
    if (!project) return;
    const root = ppro.FolderItem.cast(await project.getRootItem());
    const rootItems = await root.getItems();
    for (const job of requested) {
      const payload = await api(`/api/narration/jobs/${job.id}/manifest`, {method: 'POST', body: '{}'});
      const items = (payload.manifest?.audioItems || []).filter(item => item.audioPath && item.transcriptPath);
      // 現在のプロジェクトに全WAVが揃った案件だけを処理する。別案件を誤って反映しないための照合。
      if (!items.length) continue;
      let allPresent = true;
      for (const item of items) {
        if (!await findNarrationClipByMediaPath(rootItems, item.audioPath)) { allPresent = false; break; }
      }
      if (!allPresent) continue;
      try {
        await importNarrationTranscripts(job, false);
        await api(`/api/narration/jobs/${job.id}/transcript-complete`, {method: 'POST', body: '{}'});
      } catch (error) {
        const message = String(error?.message || error);
        await api(`/api/narration/jobs/${job.id}/transcript-failed`, {
          method: 'POST', body: JSON.stringify({error: message}),
        });
        setMessage(`ナレーション文字起こし登録を中止しました: ${message}`);
      }
    }
  } catch (_) {
    // サーバーの起動途中などは次回の監視で再試行する。
  } finally {
    narrationTranscriptRequestRunning = false;
  }
}

async function refreshJobs() {
  if (!(await checkBridge())) return [];
  try { state.project = await ppro.Project.getActiveProject(); } catch (_) { state.project = null; }
  const {jobs} = await api('/api/jobs');
  refreshNarrationJobs().catch(() => {});
  const projectJobs = state.project
    ? jobs.filter(job => job.project_path
      ? normalizedPath(job.project_path) === normalizedPath(state.project.path)
      : job.project_guid === String(state.project.guid))
    : jobs;
  const container = $('#jobs');
  container.replaceChildren();
  if (!projectJobs.length) {
    container.innerHTML = '<p class="empty">まだ処理はありません</p>';
    return [];
  }
  for (const job of projectJobs) {
    const row = document.createElement('div');
    row.className = 'asset';
    row.innerHTML = `<div class="asset-top"><div class="asset-name">${job.asset_name}</div></div>
      <div class="asset-state ${statusClass(job.status)}">${job.message}</div>
      <div class="asset-actions"></div>`;
    const actions = row.querySelector('.asset-actions');
    if (['review_ready', 'ready_to_writeback', 'writeback_requested', 'applied_pending_verification', 'confirmed'].includes(job.status)) {
      const review = document.createElement('button');
      review.textContent = '映像・文字を確認';
      review.addEventListener('click', () => api(`/api/jobs/${job.id}/review`, {method:'POST', body:'{}'}).catch(error => reportError('open-review', error, {asset: job.asset_name})));
      actions.append(review);
      if (job.status === 'applied_pending_verification') {
        const verify = document.createElement('button');
        verify.textContent = 'Premiere反映を確認';
        verify.addEventListener('click', () => verifyAppliedJob(job, true).catch(error => reportError('verify-write-back', error, {asset: job.asset_name})));
        actions.append(verify);
      } else if (['ready_to_writeback', 'writeback_requested', 'confirmed'].includes(job.status)) {
        const writeback = document.createElement('button');
        writeback.textContent = job.status === 'confirmed' ? '再度Premiereへ書き戻す' : 'Premiereへ書き戻す';
        writeback.addEventListener('click', () => performWriteBack(job, true));
        actions.append(writeback);
      }
    }
    container.append(row);
  }
  return projectJobs;
}

function normalizedPath(path) {
  return String(path || '').normalize('NFC');
}

async function findClipInItems(items, job) {
  for (const item of items) {
    if (String(item.getId()) === String(job.asset_id)) {
      try {
        const clip = ppro.ClipProjectItem.cast(item);
        const path = await clip.getMediaFilePath();
        if (normalizedPath(path) === normalizedPath(job.media_path)) return clip;
      } catch (_) {}
    }
    const folderResult = await asFolder(item);
    if (folderResult) {
      const found = await findClipInItems(folderResult.children, job);
      if (found) return found;
    }
  }
  return null;
}

async function resolveWritebackClip(job, forceFresh = false) {
  state.project = await ppro.Project.getActiveProject();
  if (!state.project) throw new Error('Premiereで対象プロジェクトを開いてください');
  if (String(state.project.guid) !== String(job.project_guid)) {
    throw new Error('開いているPremiereプロジェクトが対象と異なります');
  }
  if (job.project_path && normalizedPath(state.project.path) !== normalizedPath(job.project_path)) {
    throw new Error('開いているPremiereプロジェクトの保存場所が書き出し元と異なります');
  }
  const cached = forceFresh ? null : state.assetRefs.get(job.asset_id);
  if (cached) {
    const cachedPath = await cached.getMediaFilePath();
    if (normalizedPath(cachedPath) === normalizedPath(job.media_path)) return cached;
  }
  const root = await state.project.getRootItem();
  const rootFolder = ppro.FolderItem.cast(root);
  const clip = await findClipInItems(await rootFolder.getItems(), job);
  if (!clip) throw new Error('対象素材IDとメディアパスがPremiere内で一致しません');
  state.assetRefs.set(job.asset_id, clip);
  return clip;
}

function transcriptFingerprint(raw) {
  const value = typeof raw === 'string' ? JSON.parse(raw) : raw;
  const speakers = new Map((value.speakers || []).map(item => [String(item.id), String(item.name || '')]));
  return JSON.stringify((value.segments || []).map(segment => ({
    start: Number(segment.start),
    duration: Number(segment.duration),
    speaker: speakers.get(String(segment.speaker)) || String(segment.speaker || ''),
    text: (segment.words || []).map(word => String(word.text || word.word || '')).join(''),
  })));
}

function applyTranscriptJson(clip, json, label) {
  const project = state.project;
  if (!project) throw new Error('Premiereプロジェクトへの接続を取得できませんでした');
  // Premiere UXP APIはJSONオブジェクトではなくJSON文字列を受け取る。
  // ブラウザ連携側はオブジェクト、既存の書き戻し側は文字列の両方を渡すため正規化する。
  const jsonString = typeof json === 'string' ? json : JSON.stringify(json);
  let transactionResult = true;
  // TextSegmentsもActionと同じプロジェクトロック内で作る。Premiere 26系では
  // ロック外で作ったPremiereオブジェクトをActionへ渡すと型エラーになることがある。
  project.lockedAccess(() => {
    let textSegments;
    try { textSegments = ppro.Transcript.importFromJSON(jsonString); }
    catch (error) { throw new Error(`文字起こしJSONの読込に失敗しました: ${error?.message || error}`); }
    let action;
    try { action = ppro.Transcript.createImportTextSegmentsAction(textSegments, clip); }
    catch (error) { throw new Error(`Premiereの文字起こし登録Actionを作成できませんでした: ${error?.message || error}`); }
    transactionResult = project.executeTransaction(compound => compound.addAction(action), label);
  });
  if (transactionResult === false) throw new Error('Premiereが文字起こし書き戻し処理を受け付けませんでした');
}

async function verifyAppliedJob(job, showResult = false) {
  if (state.writebacksInProgress.has(job.id)) return {status: 'busy'};
  state.writebacksInProgress.add(job.id);
  try {
    const clip = await resolveWritebackClip(job, true);
    const payload = await api(`/api/jobs/${job.id}/corrected-json`, {method:'POST', body:'{}'});
    const verifiedJson = await ppro.Transcript.exportToJSON(clip);
    if (transcriptFingerprint(verifiedJson) !== transcriptFingerprint(payload.json)) {
      const message = 'Premiere上の文字起こしが修正版JSONと一致しません。自動変更は行っていません。';
      await api(`/api/jobs/${job.id}/writeback-failed`, {
        method: 'POST',
        body: JSON.stringify({error: message, restored: false}),
      });
      if (showResult) alert(`${job.asset_name}\n${message}`);
      return {status: 'mismatch', message};
    }
    await api(`/api/jobs/${job.id}/writeback-complete`, {
      method: 'POST',
      body: JSON.stringify({saved: true}),
    });
    const message = `${job.asset_name} の修正文言・話者名・区間がPremiere上の内容と一致しました。`;
    setMessage(message);
    if (showResult) alert(message);
    return {status: 'confirmed', message};
  } catch (error) {
    const message = `Premiere再読込後の一致確認をまだ実行できません: ${error?.message || error}`;
    try {
      await api(`/api/jobs/${job.id}/writeback-pending-verification`, {
        method: 'POST', body: JSON.stringify({error: message}),
      });
    } catch (_) {}
    try { window.uxpDiagnostics?.report(error, 'verify-applied-write-back', {asset: job.asset_name}); } catch (_) {}
    setMessage(message);
    if (showResult) alert(`${job.asset_name}\n${message}`);
    return {status: 'pending_verification', message};
  } finally {
    state.writebacksInProgress.delete(job.id);
    await refreshJobs();
  }
}

async function performWriteBack(job, askConfirmation = true) {
  if (state.writebacksInProgress.has(job.id)) return {status: 'busy'};
  if (askConfirmation && !confirm(`${job.asset_name} の素材文字起こしを修正済み内容で置き換えます。\n文言・話者名・話者区間が対象です。実行しますか？`)) {
    return {status: 'cancelled'};
  }
  state.writebacksInProgress.add(job.id);
  try {
    const clip = await resolveWritebackClip(job);
    const payload = await api(`/api/jobs/${job.id}/corrected-json`, {method:'POST', body:'{}'});
    const backupJson = await ppro.Transcript.exportToJSON(clip);
    await api(`/api/jobs/${job.id}/writeback-backup`, {
      method: 'POST',
      body: JSON.stringify({json: backupJson}),
    });
    await api(`/api/jobs/${job.id}/writeback-started`, {method:'POST', body:'{}'});
    applyTranscriptJson(clip, payload.json, '修正済み文字起こしを読み込み');
    const saved = await state.project.save();
    if (!saved) throw new Error('Premiereプロジェクトを保存できませんでした。バックアップは保持しています。');

    await new Promise(resolve => setTimeout(resolve, 500));
    let verifiedJson;
    try {
      const verificationClip = await resolveWritebackClip(job, true);
      verifiedJson = await ppro.Transcript.exportToJSON(verificationClip);
    } catch (verificationError) {
      const detail = `${verificationError?.name || 'Error'}: ${verificationError?.message || verificationError}`;
      await api(`/api/jobs/${job.id}/writeback-pending-verification`, {
        method: 'POST', body: JSON.stringify({error: detail}),
      });
      const message = `${job.asset_name} へ反映して保存しました。Premiere再読込後に一致確認してください。`;
      setMessage(message);
      return {status: 'pending_verification', message};
    }
    if (transcriptFingerprint(verifiedJson) !== transcriptFingerprint(payload.json)) {
      const message = '反映後の文字起こしが修正版JSONと一致しません。自動復元は行わず、バックアップを保持しています。';
      await api(`/api/jobs/${job.id}/writeback-failed`, {
        method: 'POST', body: JSON.stringify({error: message, restored: false}),
      });
      setMessage(message);
      return {status: 'mismatch', message};
    }
    await api(`/api/jobs/${job.id}/writeback-complete`, {
      method: 'POST',
      body: JSON.stringify({saved: Boolean(saved)}),
    });
    setMessage(`${job.asset_name} への書き戻し・一致確認・プロジェクト保存が完了しました。`);
    return {status: 'confirmed'};
  } catch (error) {
    try {
      await api(`/api/jobs/${job.id}/writeback-failed`, {
        method: 'POST',
        body: JSON.stringify({error: `${error?.name || 'Error'}: ${error?.message || error}`, restored: false}),
      });
    } catch (_) {}
    try { window.uxpDiagnostics?.report(error, 'write-back', {asset: job.asset_name}); } catch (_) {}
    setMessage(`書き戻しに失敗しました: ${error.message}`);
    return {status: 'error', message: error?.message || String(error)};
  } finally {
    state.writebacksInProgress.delete(job.id);
    await refreshJobs();
  }
}

async function runPendingTranscriptAction() {
  if (!(await checkBridge())) throw new Error('UXP連携サーバーが起動していません');
  const jobs = await refreshJobs();
  const pendingVerification = jobs.filter(job => job.status === 'applied_pending_verification');
  const pendingWriteback = jobs.filter(job => ['ready_to_writeback', 'writeback_requested'].includes(job.status));
  if (!pendingVerification.length && !pendingWriteback.length) {
    alert('このプロジェクトに未確認・未反映の修正版はありません。');
    return;
  }
  const lines = [];
  if (pendingVerification.length) lines.push(`反映済み内容の一致確認: ${pendingVerification.map(job => job.asset_name).join('、')}`);
  if (pendingWriteback.length) lines.push(`Premiereへ反映: ${pendingWriteback.map(job => job.asset_name).join('、')}`);
  if (!confirm(`${lines.join('\n')}\n\nこの順に処理しますか？`)) return;

  const results = [];
  for (const job of pendingVerification) results.push([job, await verifyAppliedJob(job, false)]);
  for (const job of pendingWriteback) results.push([job, await performWriteBack(job, false)]);
  const confirmed = results.filter(([, result]) => result?.status === 'confirmed').length;
  const waiting = results.filter(([, result]) => result?.status === 'pending_verification').length;
  const failed = results.length - confirmed - waiting;
  alert(`処理結果\n完了 ${confirmed}件／再読込後の確認待ち ${waiting}件／要確認 ${failed}件`);
}

window.runApplyPendingTranscriptCommand = runPendingTranscriptAction;
window.onTranscriptPanelShow = async () => {
  if (!(await checkBridge(700))) await startBridgeServer({automatic: true});
  await refreshNarrationJobs();
  const jobs = await refreshJobs();
  const actionable = jobs.filter(job => ['ready_to_writeback', 'writeback_requested', 'applied_pending_verification'].includes(job.status));
  if (!actionable.length) return;
  const promptKey = actionable.map(job => `${job.id}:${job.status}`).sort().join('|');
  if (state.panelPromptsShown.has(promptKey)) return;
  state.panelPromptsShown.add(promptKey);
  if (confirm(`未反映または未確認の修正版が ${actionable.length}件あります。\n今確認しますか？`)) {
    await runPendingTranscriptAction();
  }
};

async function performRestore(job) {
  if (state.writebacksInProgress.has(job.id)) return;
  state.writebacksInProgress.add(job.id);
  let stage = '対象素材の照合';
  try {
    const clip = await resolveWritebackClip(job);
    stage = 'バックアップJSONの取得';
    const payload = await api(`/api/jobs/${job.id}/restore-json`, {method:'POST', body:'{}'});
    stage = 'バックアップJSONの変換';
    const expectedFingerprint = transcriptFingerprint(payload.json);
    stage = 'Premiereへ元データを読み込み';
    applyTranscriptJson(clip, payload.json, '元の文字起こしを復旧');
    await new Promise(resolve => setTimeout(resolve, 500));
    stage = '復旧後の一致確認';
    const verificationClip = await resolveWritebackClip(job, true);
    const verifiedJson = await ppro.Transcript.exportToJSON(verificationClip);
    if (transcriptFingerprint(verifiedJson) !== expectedFingerprint) {
      throw new Error('復旧後の文字起こしがバックアップと一致しません');
    }
    stage = 'Premiereプロジェクトの保存';
    const saved = await state.project.save();
    if (!saved) throw new Error('Premiereプロジェクトを保存できませんでした');
    await api(`/api/jobs/${job.id}/restore-complete`, {
      method:'POST', body:JSON.stringify({saved:true}),
    });
    setMessage(`${job.asset_name} の元の文字起こしを復旧し、一致確認と保存が完了しました。`);
  } catch (error) {
    const detailed = `${stage}: ${error?.name || 'Error'}: ${error?.message || error}`;
    try { await api(`/api/jobs/${job.id}/restore-failed`, {method:'POST', body:JSON.stringify({error:detailed})}); } catch (_) {}
    try { window.uxpDiagnostics?.report(error, 'restore-backup', {asset:job.asset_name, stage}); } catch (_) {}
    setMessage(`自動復旧に失敗しました: ${detailed}`);
  } finally {
    state.writebacksInProgress.delete(job.id);
    await refreshJobs();
  }
}

$('#scan').addEventListener('click', () => scanSelection().catch(error => reportError('scan-selection', error)));
$('#choose-reference').addEventListener('click', () => chooseReferenceFolder().catch(error => reportError('choose-reference-folder', error)));
$('#start').addEventListener('click', () => startBatch().catch(error => reportError('start-analysis', error)));
$('#refresh-jobs').addEventListener('click', () => refreshJobs().catch(error => reportError('refresh-jobs', error)));

$('#reference-path').textContent = state.referenceDir || '未指定';
setupWorkspaceTabs();
updateStartState();
showBootStatus('パネル起動済み');
const narrationButton = $('#open-narration');
if (narrationButton) narrationButton.addEventListener('click', () => openNarrationStudio().catch(error => reportError('open-narration', error)));
const bridgeButton = $('#start-bridge');
if (bridgeButton) bridgeButton.addEventListener('click', () => startBridgeServer().catch(error => reportError('bridge-start-button', error)));
startBridgeServer({automatic: true}).catch(error => reportError('bridge-auto-start', error));
setTimeout(() => window.onTranscriptPanelShow().catch(error => reportError('panel-show-refresh', error)), 0);
setInterval(() => runPendingNarrationTranscriptRequests().catch(() => {}), 1000);
// Approval is performed in the browser, so the Premiere panel must observe
// that external state change without requiring the panel to be reopened.
setInterval(() => refreshNarrationJobs().catch(() => {}), 3000);
// If the helper process stops while the panel remains open, expose the manual
// restart button instead of leaving a stale "connected" indicator behind.
setInterval(() => checkBridge(700).catch(() => setBridgeState(false)), 5000);
} catch (startupError) {
  try { window.uxpDiagnostics?.report(startupError, 'panel-startup'); } catch (_) {}
  try {
    const app = document.querySelector('#app') || document.body;
    app.innerHTML = `<div style="margin:16px;padding:16px;border:2px solid #e66b5b;border-radius:10px;background:#4b2421;color:#ffe0dc;">
      <div style="font-size:21px;font-weight:bold;">パネル起動エラー</div>
      <div style="margin-top:10px;white-space:pre-wrap;">${String(startupError?.message || startupError)}</div>
      <div style="margin-top:10px;font-size:15px;">エラーログへ自動記録しました。</div>
    </div>`;
  } catch (_) {}
}
