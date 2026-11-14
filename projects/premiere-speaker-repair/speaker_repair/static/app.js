const state = { payload: null, rows: [] };
const audio = document.querySelector('#audio');
let activePlayButton = null;

function similarity(value) {
  return value == null ? '未解析' : value.toFixed(2);
}

function confidenceLabel(value) {
  return value === 'strong' ? '強い候補' : value === 'review' ? '要確認' : '不明瞭';
}

function buildRows(payload) {
  const suggestions = new Map((payload.analysis.segment_suggestions || []).map(x => [x.segment_index, x]));
  return payload.manifest.segments.map(segment => ({
    ...segment,
    suggestion: suggestions.get(segment.index) || null,
    decision: payload.decisions[String(segment.index)] || null,
  }));
}

async function saveDecision(row, status, element) {
  const payload = {
    segment_index: row.index,
    status,
    original_speaker: row.speaker_name,
    candidate: row.suggestion?.candidate || null,
    updated_at: new Date().toISOString(),
  };
  await fetch('/api/decision', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  row.decision = payload;
  element.textContent = status === 'approved' ? '承認済み' : status === 'hold' ? '保留中' : '却下済み';
}

function resetPlayButton() {
  if (activePlayButton) activePlayButton.textContent = '▶ 試聴';
  activePlayButton = null;
}

function stopPlayback() {
  clearTimeout(audio._stopTimer);
  audio.pause();
  resetPlayButton();
}

function play(row, button) {
  if (activePlayButton === button && !audio.paused) {
    stopPlayback();
    return;
  }
  stopPlayback();
  audio.currentTime = row.start_seconds;
  activePlayButton = button;
  button.textContent = '■ 停止';
  audio.play();
  audio._stopTimer = setTimeout(stopPlayback, Math.min(row.duration_seconds, 15) * 1000);
}

function render() {
  const filter = document.querySelector('#filter').value;
  const query = document.querySelector('#search').value.trim().toLowerCase();
  const container = document.querySelector('#segments');
  container.replaceChildren();
  const rows = state.rows.filter(row => {
    if (filter === 'unlocked' && row.locked) return false;
    if (filter === 'locked' && !row.locked) return false;
    if (filter === 'change' && !row.suggestion?.possible_internal_change) return false;
    return !query || `${row.speaker_name} ${row.text} ${row.suggestion?.candidate || ''}`.toLowerCase().includes(query);
  });
  for (const row of rows) {
    const card = document.querySelector('#segment-template').content.firstElementChild.cloneNode(true);
    card.querySelector('.time').textContent = `${row.premiere_start}  /  ${row.start_seconds.toFixed(2)}秒`;
    card.querySelector('.original').textContent = `元: ${row.speaker_name}${row.locked ? '（確定）' : ''}`;
    const candidate = row.suggestion;
    card.querySelector('.candidate').textContent = candidate ? `候補: ${candidate.candidate}／${confidenceLabel(candidate.confidence)}／類似度 ${similarity(candidate.similarity)}` : '候補: 未解析';
    card.querySelector('.text').textContent = row.text;
    const playButton = card.querySelector('.play');
    playButton.addEventListener('click', () => play(row, playButton));
    const saved = card.querySelector('.saved');
    if (row.decision) saved.textContent = row.decision.status;
    card.querySelectorAll('[data-status]').forEach(button => button.addEventListener('click', () => saveDecision(row, button.dataset.status, saved)));
    container.append(card);
  }
}

async function bulkApprove(diagnostic, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '保存中…';
  const response = await fetch('/api/bulk-decision', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      speaker_id: diagnostic.speaker_id,
      candidate: diagnostic.dominant_candidate,
      updated_at: new Date().toISOString(),
    }),
  }).then(r => r.json());
  const payload = await fetch('/api/project').then(r => r.json());
  state.payload = payload;
  state.rows = buildRows(payload);
  button.textContent = `${response.count}件を承認済み`;
  render();
  setTimeout(() => { button.textContent = original; button.disabled = false; }, 2500);
}

function renderDiagnostics(payload) {
  const container = document.querySelector('#diagnostics');
  container.replaceChildren();
  const diagnostics = (payload.analysis.label_diagnostics || []).filter(x => !payload.manifest.known_speakers.includes(x.speaker_name));
  if (!diagnostics.length) {
    container.textContent = '音声解析前、または未確定ラベルがありません。';
    return;
  }
  for (const item of diagnostics) {
    const row = document.createElement('div');
    row.className = 'diagnostic-row';
    const label = document.createElement('div');
    const share = Math.round(item.dominant_share * 100);
    label.innerHTML = `<strong>${item.speaker_name}</strong><span>主候補: ${item.dominant_candidate || '未判定'}／音声区間の一致 ${share}%／例外 ${item.outlier_count}件</span>`;
    row.append(label);
    if (item.dominant_candidate && item.dominant_share >= 0.98) {
      const button = document.createElement('button');
      button.textContent = '強い候補だけまとめて承認';
      button.addEventListener('click', () => bulkApprove(item, button));
      row.append(button);
    } else {
      const badge = document.createElement('span');
      badge.className = 'warning';
      badge.textContent = '個別確認';
      row.append(badge);
    }
    container.append(row);
  }
}

async function init() {
  const payload = await fetch('/api/project').then(r => r.json());
  state.payload = payload;
  state.rows = buildRows(payload);
  document.querySelector('#title').textContent = payload.manifest.project_name;
  document.querySelector('#summary').textContent = `${payload.manifest.segment_count}区間・確定話者 ${payload.manifest.known_speakers.join('／')}`;
  renderDiagnostics(payload);
  render();
}

document.querySelector('#filter').addEventListener('change', render);
document.querySelector('#search').addEventListener('input', render);
audio.addEventListener('ended', resetPlayButton);
audio.addEventListener('pause', resetPlayButton);
init();
