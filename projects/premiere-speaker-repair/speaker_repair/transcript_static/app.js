const state = {
  rows: [], decisions: {}, speakerMappings: {}, speakerOptions: [],
  visibleRows: [],
  speakerFilterSegments: null,
  editHistory: {can_undo: false, can_redo: false},
  jobId: '', sourceMediaName: '',
};
const audio = document.querySelector('#audio');
const video = document.querySelector('#video');
let player = audio;
let activePlayButton = null;
let activePlayContext = null;
let reverseTimer = null;
let reverseSpeed = 0;
let activeSyncedCard = null;
let suppressPlaybackScrollUntil = 0;

function resetPlay() { if (activePlayButton) activePlayButton.textContent = '▶ 試聴'; activePlayButton = null; }
function stopPlayback() {
  clearTimeout(player._stopTimer);
  clearInterval(reverseTimer);
  reverseTimer = null;
  reverseSpeed = 0;
  player.pause();
  resetPlay();
}
function capturePlaybackState() {
  return {
    currentTime: Number(player.currentTime || 0),
    playbackRate: Number(player.playbackRate || 1),
    wasPlaying: !player.paused,
    reverseSpeed: Number(reverseSpeed || 0),
  };
}
function beginReversePlayback(speed) {
  player.pause();
  clearTimeout(player._stopTimer);
  clearInterval(reverseTimer);
  reverseSpeed = Math.max(1, Math.min(Number(speed || 1), 4));
  reverseTimer = setInterval(() => {
    player.currentTime = Math.max(0, player.currentTime - reverseSpeed * 0.1);
    if (player.currentTime <= 0) stopPlayback();
  }, 100);
}
function restorePlaybackState(snapshot) {
  if (!snapshot || (!snapshot.wasPlaying && !snapshot.reverseSpeed)) return;
  if (Math.abs(Number(player.currentTime || 0) - snapshot.currentTime) > 0.02) {
    player.currentTime = snapshot.currentTime;
  }
  const row = rowAtPlaybackTime(snapshot.currentTime);
  const card = row
    ? document.querySelector(`.card[data-segment-index="${row.segment_index}"]`)
    : null;
  activePlayButton = card?.querySelector('.play') || null;
  if (activePlayButton) activePlayButton.textContent = '■ 停止';
  if (snapshot.reverseSpeed) {
    beginReversePlayback(snapshot.reverseSpeed);
    return;
  }
  player.playbackRate = snapshot.playbackRate;
  player.play();
  if (!isFullTranscriptMode() && row) {
    scheduleVisibleRowBoundary(row);
  }
}
function isFullTranscriptMode() {
  return document.querySelector('#filter')?.value === 'all'
    && !document.querySelector('#speaker-filter')?.value
    && !document.querySelector('#search')?.value.trim();
}
function isUnapprovedQueueMode() {
  return document.querySelector('#filter')?.value === 'unapproved';
}
function activatePlaybackCard(row) {
  resetPlay();
  const card = document.querySelector(`.card[data-segment-index="${row.segment_index}"]`);
  activePlayButton = card?.querySelector('.play') || null;
  if (activePlayButton) activePlayButton.textContent = '■ 停止';
  if (card) {
    if (activeSyncedCard && activeSyncedCard !== card) {
      activeSyncedCard.classList.remove('is-playback-position');
    }
    activeSyncedCard = card;
    card.classList.add('is-playback-position');
    scrollCardHeaderIntoView(card);
  }
}
function playNextVisibleRow(row) {
  const rows = state.visibleRows || [];
  let position = rows.findIndex(item => item.segment_index === row.segment_index);
  if (position < 0) {
    position = rows.findIndex(item => Number(item.start_seconds || 0) > Number(player.currentTime || 0)) - 1;
  }
  const next = rows[position + 1];
  if (!next) return stopPlayback();
  player.currentTime = Number(next.start_seconds || 0);
  activatePlaybackCard(next);
  player.play();
  scheduleVisibleRowBoundary(next);
}
function scheduleVisibleRowBoundary(row) {
  clearTimeout(player._stopTimer);
  const end = Number(row.start_seconds || 0) + Number(row.duration_seconds || 0);
  const checkBoundary = () => {
    if (reverseTimer) return;
    if (player.paused) return;
    if (Number(player.currentTime || 0) >= end - 0.04) {
      if (isUnapprovedQueueMode()) playNextVisibleRow(row);
      else stopPlayback();
      return;
    }
    player._stopTimer = setTimeout(checkBoundary, 80);
  };
  player._stopTimer = setTimeout(checkBoundary, 80);
}
function play(row, button, text = '', characterPosition = 0) {
  if (activePlayButton === button) return stopPlayback();
  stopPlayback();
  const ratio = text.length ? Math.max(0, Math.min(characterPosition / text.length, 1)) : 0;
  const targetTime = row.start_seconds + row.duration_seconds * ratio;
  const playbackStart = Math.max(row.start_seconds, targetTime - 1.0);
  player.currentTime = playbackStart;
  player.playbackRate = 1;
  activePlayButton = button;
  button.textContent = '■ 停止';
  player.play();
  if (!isFullTranscriptMode()) {
    scheduleVisibleRowBoundary(row);
  }
}

function shuttleForward() {
  clearInterval(reverseTimer);
  reverseTimer = null;
  reverseSpeed = 0;
  const speeds = [1, 2, 4];
  const currentIndex = speeds.indexOf(player.playbackRate);
  player.playbackRate = player.paused ? 1 : speeds[Math.min(currentIndex + 1, speeds.length - 1)];
  player.play();
}

function shuttleReverse() {
  beginReversePlayback(reverseSpeed === 0 ? 1 : Math.min(reverseSpeed * 2, 4));
}

function isEditingTarget(event) {
  const target = event.target;
  return event.isComposing || target?.matches?.('textarea,input,select,[contenteditable="true"]');
}

function rowAtPlaybackTime(currentTime) {
  const rows = [...state.rows].sort((left, right) => (
    Number(left.start_seconds || 0) - Number(right.start_seconds || 0)
    || Number(left.segment_index || 0) - Number(right.segment_index || 0)
  ));
  if (!rows.length) return null;
  let selected = rows[0];
  for (const row of rows) {
    if (Number(row.start_seconds || 0) > currentTime) break;
    selected = row;
    const end = Number(row.start_seconds || 0) + Number(row.duration_seconds || 0);
    if (currentTime < end) break;
  }
  return selected;
}

function scrollCardHeaderIntoView(card) {
  const header = document.querySelector('header');
  const stickyHeaderHeight = header && getComputedStyle(header).position === 'sticky'
    ? header.getBoundingClientRect().height
    : 0;
  const top = window.scrollY + card.getBoundingClientRect().top - stickyHeaderHeight - 12;
  window.scrollTo({top: Math.max(0, top), behavior: 'smooth'});
}

function captureViewportAnchor() {
  const cards = Array.from(document.querySelectorAll('.card'));
  const headerBottom = document.querySelector('header')?.getBoundingClientRect().bottom || 0;
  const focusedCard = document.activeElement?.closest?.('.card');
  const visibleCard = cards.find(card => card.getBoundingClientRect().bottom > headerBottom + 1);
  const card = focusedCard && cards.includes(focusedCard) ? focusedCard : visibleCard;
  return {
    segmentIndex: card?.dataset.segmentIndex || '',
    cardTop: card?.getBoundingClientRect().top ?? null,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
  };
}

function restoreViewportAnchor(anchor) {
  if (!anchor) return;
  const card = anchor.segmentIndex
    ? document.querySelector(`.card[data-segment-index="${anchor.segmentIndex}"]`)
    : null;
  const topAdjustment = card && anchor.cardTop != null
    ? card.getBoundingClientRect().top - anchor.cardTop
    : 0;
  window.scrollTo({
    left: anchor.scrollX,
    top: Math.max(0, anchor.scrollY + topAdjustment),
    behavior: 'auto',
  });
}

function moveToAdjacentPanel(direction) {
  const cards = Array.from(document.querySelectorAll('.card'));
  if (!cards.length) return;
  const headerBottom = document.querySelector('header')?.getBoundingClientRect().bottom || 0;
  const focusedCard = document.activeElement?.closest?.('.card');
  const playingCard = activePlayButton?.closest?.('.card');
  const visibleCard = cards.find(card => card.getBoundingClientRect().bottom > headerBottom + 1);
  const currentCard = playingCard
    || (focusedCard && cards.includes(focusedCard) ? focusedCard : null)
    || (activeSyncedCard && cards.includes(activeSyncedCard) ? activeSyncedCard : null)
    || visibleCard
    || cards[0];
  const currentIndex = Math.max(0, cards.indexOf(currentCard));
  const targetIndex = Math.max(0, Math.min(cards.length - 1, currentIndex + direction));
  const targetCard = cards[targetIndex];
  if (!targetCard || targetCard === currentCard) return;
  const segmentIndex = Number(targetCard.dataset.segmentIndex);
  const row = state.visibleRows.find(item => Number(item.segment_index) === segmentIndex);
  if (!row) return;

  const wasPlaying = !player.paused || Boolean(reverseTimer);
  const playbackRate = reverseTimer ? 1 : Number(player.playbackRate || 1);
  activePlayContext = targetCard._playFromEditor || null;
  if (!wasPlaying) {
    activatePlaybackCard(row);
    resetPlay();
    targetCard.querySelector('textarea')?.focus({preventScroll: true});
    return;
  }

  clearTimeout(player._stopTimer);
  clearInterval(reverseTimer);
  reverseTimer = null;
  reverseSpeed = 0;
  player.currentTime = Number(row.start_seconds || 0);
  player.playbackRate = playbackRate;
  activatePlaybackCard(row);
  player.play();
  if (!isFullTranscriptMode()) scheduleVisibleRowBoundary(row);
}

function scrollToPlaybackPosition(force = false) {
  if (performance.now() < suppressPlaybackScrollUntil) return;
  if (reverseTimer || !isFullTranscriptMode()) return;
  const row = rowAtPlaybackTime(Number(player.currentTime || 0));
  if (!row) return;
  let card = document.querySelector(`.card[data-segment-index="${row.segment_index}"]`);
  if (!card) {
    const filter = document.querySelector('#filter');
    const search = document.querySelector('#search');
    filter.value = 'all';
    search.value = '';
    render({preservePlayback: true});
    card = document.querySelector(`.card[data-segment-index="${row.segment_index}"]`);
  }
  if (!card) return;
  if (!force && activeSyncedCard === card) return;
  if (activeSyncedCard && activeSyncedCard !== card) activeSyncedCard.classList.remove('is-playback-position');
  activeSyncedCard = card;
  card.classList.add('is-playback-position');
  scrollCardHeaderIntoView(card);
}

function termBadges(row) {
  const badges = [];
  for (const change of row.applied_reference_terms || []) badges.push(`<span class="applied">資料反映: ${change.from} → ${change.to}</span>`);
  for (const item of row.possible_reference_terms || []) badges.push(`<span>資料候補: ${item.term}</span>`);
  for (const term of row.exact_reference_terms || []) badges.push(`<span>${term}</span>`);
  return badges.join('');
}

function identityHints(row) {
  return (row.identity_candidates || []).map(item => {
    const links = (item.sources || []).map(source =>
      `<a href="${source.url}" target="_blank" rel="noreferrer">${source.label}</a>`
    ).join('・');
    return `<aside class="identity-hint">
      <div><strong>${item.category}候補：${item.candidate}</strong><span class="confidence">確信度 ${item.confidence}</span></div>
      <p>${item.reason}</p>
      <p class="identity-policy">本文方針：${item.transcript_policy}</p>
      ${links ? `<p class="identity-sources">根拠：${links}</p>` : ''}
    </aside>`;
  }).join('');
}

function verificationNotes(row) {
  return (row.verification_notes || []).map(item => {
    const links = (item.sources || []).map(source =>
      `<a href="${source.url}" target="_blank" rel="noreferrer">${source.label}</a>`
    ).join('・');
    const origins = item.origins?.length ? item.origins.join('・') : '本文内で検出';
    return `<aside class="verification-note">
      <div><strong>${item.status}：${item.term}</strong><span>${origins}</span><span>${item.judgment}</span></div>
      <p>${item.reason}</p>
      ${links ? `<p>根拠：${links}</p>` : ''}
    </aside>`;
  }).join('');
}

function showToast(message, isError = false) {
  const toast = document.querySelector('#toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3500);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

function highlightedHtml(text, ranges = []) {
  const safeRanges = ranges
    .map(range => ({
      start: Math.max(0, Math.min(Number(range.start), text.length)),
      end: Math.max(0, Math.min(Number(range.end), text.length)),
      kind: String(range.kind || ''),
    }))
    .filter(range => range.start < range.end)
    .sort((left, right) => left.start - right.start || left.end - right.end);
  let position = 0;
  const output = [];
  for (const range of safeRanges) {
    if (range.start < position) continue;
    output.push(escapeHtml(text.slice(position, range.start)));
    output.push(`<mark class="change-${escapeHtml(range.kind)}">${escapeHtml(text.slice(range.start, range.end))}</mark>`);
    position = range.end;
  }
  output.push(escapeHtml(text.slice(position)));
  return output.join('');
}

function isPlaceholderSpeaker(name) {
  return /^(?:不明|unknown|speaker\s*\d*|話者(?:\s*[@＠#]?\s*\d*)?)$/i.test(String(name || '').trim());
}

function rebuildSpeakerMappings() {
  state.speakerMappings = {};
  for (const decision of Object.values(state.decisions)) {
    if (decision.speaker_scope === 'bulk' && decision.speaker_original && decision.speaker_name) {
      state.speakerMappings[decision.speaker_original] = decision.speaker_name;
    }
  }
}

function effectiveSpeakerName(row) {
  const decision = state.decisions[String(row.segment_index)];
  return decision?.speaker_name || state.speakerMappings[row.speaker_name] || row.speaker_name;
}

function populateSpeakerFilter() {
  const select = document.querySelector('#speaker-filter');
  const selected = select.value;
  const assetSpeakers = Array.from(new Set(
    state.rows.map(effectiveSpeakerName).filter(Boolean)
  ));
  const assetSpeakerSet = new Set(assetSpeakers);
  const projectSpeakers = state.speakerOptions.filter(
    name => name && !assetSpeakerSet.has(name)
  );

  select.replaceChildren();
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = 'すべての話者';
  select.append(allOption);

  const appendGroup = (label, names) => {
    if (!names.length) return;
    const group = document.createElement('optgroup');
    group.label = label;
    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      group.append(option);
    }
    select.append(group);
  };
  appendGroup('この素材の話者', assetSpeakers);
  appendGroup('プロジェクト共通の登録済み話者', projectSpeakers);
  const manageOption = document.createElement('option');
  manageOption.value = '__manage_speakers__';
  manageOption.textContent = '⚙ 話者名を管理…';
  select.append(manageOption);
  select.value = [...assetSpeakers, ...projectSpeakers].includes(selected) ? selected : '';
  select.dataset.previousValue = select.value;
}

function speakerUsageCounts() {
  const counts = {};
  for (const row of state.rows) {
    const name = effectiveSpeakerName(row);
    if (name) counts[name] = (counts[name] || 0) + 1;
  }
  return counts;
}

function allKnownSpeakerNames() {
  return Array.from(new Set([
    ...state.rows.map(effectiveSpeakerName),
    ...state.speakerOptions,
  ].filter(Boolean)));
}

const speakerManager = document.querySelector('#speaker-manager');
const speakerManagerSource = document.querySelector('#speaker-manager-source');
const speakerManagerTarget = document.querySelector('#speaker-manager-target');
const speakerManagerNewName = document.querySelector('#speaker-manager-new-name');
const speakerManagerUsage = document.querySelector('#speaker-manager-usage');
const speakerManagerDelete = document.querySelector('#speaker-manager-delete');

function refreshSpeakerManager(preferredSource = speakerManagerSource.value) {
  const usage = speakerUsageCounts();
  const manageable = allKnownSpeakerNames().filter(name => !isPlaceholderSpeaker(name));
  speakerManagerSource.replaceChildren();
  for (const name of manageable) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = `${name}（${usage[name] || 0}区間）`;
    speakerManagerSource.append(option);
  }
  speakerManagerSource.value = manageable.includes(preferredSource) ? preferredSource : (manageable[0] || '');
  const sourceName = speakerManagerSource.value;
  speakerManagerTarget.replaceChildren();
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = '新しい話者名を入力';
  speakerManagerTarget.append(blank);
  for (const name of allKnownSpeakerNames().filter(name => name !== sourceName)) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    speakerManagerTarget.append(option);
  }
  const count = usage[sourceName] || 0;
  speakerManagerUsage.textContent = sourceName
    ? `「${sourceName}」はこの素材の ${count} 区間で使用されています。`
    : '管理できる登録済み話者名がありません。';
  speakerManagerDelete.textContent = count ? '置換して削除' : '候補から削除';
  speakerManagerDelete.disabled = !sourceName;
  document.querySelector('#speaker-manager-replace').disabled = !sourceName;
}

function openSpeakerManager(sourceName = '') {
  speakerManagerNewName.value = '';
  refreshSpeakerManager(sourceName);
  if (!speakerManager.open) speakerManager.showModal();
}

async function submitSpeakerManagement(action) {
  const oldName = speakerManagerSource.value;
  const newName = speakerManagerTarget.value || speakerManagerNewName.value.trim();
  try {
    const response = await fetch('/api/speakers/manage', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        action,
        old_name: oldName,
        new_name: newName,
        updated_at: new Date().toISOString(),
      }),
    });
    const result = await response.json();
    if (!result.ok) throw new Error(result.error || '話者名を変更できませんでした');
    state.decisions = result.decisions;
    state.speakerOptions = result.speaker_options || [];
    state.editHistory = result.edit_history || {can_undo: true, can_redo: false};
    rebuildSpeakerMappings();
    updateHistoryButtons();
    const filter = document.querySelector('#speaker-filter');
    if (filter.value === oldName) filter.value = newName;
    populateSpeakerFilter();
    refreshSpeakerFilterSegments();
    speakerManager.close();
    render({preservePlayback: true});
    const message = newName
      ? `「${oldName}」を「${newName}」へ統合しました（${result.changed.length}区間）`
      : `未使用の「${oldName}」を候補から削除しました`;
    showToast(message);
  } catch (error) {
    showToast(error.message, true);
  }
}

speakerManagerSource.addEventListener('change', () => {
  speakerManagerNewName.value = '';
  refreshSpeakerManager(speakerManagerSource.value);
});
speakerManagerTarget.addEventListener('change', () => {
  if (speakerManagerTarget.value) speakerManagerNewName.value = '';
});
speakerManagerNewName.addEventListener('input', () => {
  if (speakerManagerNewName.value.trim()) speakerManagerTarget.value = '';
});
document.querySelector('#speaker-manager-replace').addEventListener('click', () => submitSpeakerManagement('replace'));
speakerManagerDelete.addEventListener('click', () => submitSpeakerManagement('delete'));

function refreshSpeakerFilterSegments() {
  const selectedSpeaker = document.querySelector('#speaker-filter').value;
  state.speakerFilterSegments = selectedSpeaker
    ? new Set(
      state.rows
        .filter(row => effectiveSpeakerName(row) === selectedSpeaker)
        .map(row => row.segment_index)
    )
    : null;
}

async function updateSpeaker(row, speakerName) {
  const originalName = row.speaker_name;
  const firstPlaceholderRename = isPlaceholderSpeaker(originalName) && !state.speakerMappings[originalName];
  const scope = firstPlaceholderRename ? 'bulk' : 'segment';
  const result = await fetch('/api/speaker', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      segment_index: row.segment_index,
      original_speaker_name: originalName,
      speaker_name: speakerName,
      scope,
      updated_at: new Date().toISOString(),
    }),
  }).then(response => response.json());
  if (!result.ok) throw new Error(result.error || '話者名を保存できませんでした');
  state.decisions = result.decisions;
  state.speakerOptions = result.speaker_options || state.speakerOptions;
  state.editHistory = {can_undo: true, can_redo: false};
  rebuildSpeakerMappings();
  updateHistoryButtons();
  showToast(scope === 'bulk'
    ? `「${originalName}」の${result.changed.length}区間を「${speakerName}」へ一括変更しました`
    : `この区間だけ「${speakerName}」へ変更しました`);
  render({preservePlayback: true});
}

async function save(row, status, text, source = 'proposal') {
  const payload = {
    segment_index: row.segment_index,
    status,
    text,
    source,
    speaker_name: row._speakerName || row.speaker_name,
    proposal_highlights: row._proposalHighlightRanges || [],
    updated_at: new Date().toISOString(),
  };
  const result = await fetch('/api/decision', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}).then(response => response.json());
  const savedDecision = result.decision || payload;
  state.decisions[String(row.segment_index)] = savedDecision;
  return savedDecision;
}

function updateSummary() {
  const review = state.rows.filter(x => x.needs_review).length;
  const approved = Object.values(state.decisions).filter(x => x.status === 'approved').length;
  document.querySelector('#summary').textContent = `${state.rows.length}区間／比較対象 ${review}件／承認済み ${approved}件`;
}

function updateHistoryButtons() {
  document.querySelector('#undo-segment-edit').disabled = !state.editHistory.can_undo;
  document.querySelector('#redo-segment-edit').disabled = !state.editHistory.can_redo;
}

async function refreshProjectState() {
  const payload = await fetch('/api/project').then(response => response.json());
  state.jobId = payload.job_id;
  state.sourceMediaName = payload.source_media_name;
  state.rows = payload.candidates.items;
  state.decisions = payload.decisions;
  state.speakerOptions = payload.speaker_options || [];
  state.editHistory = payload.edit_history || {can_undo: false, can_redo: false};
  rebuildSpeakerMappings();
  updateSummary();
  updateHistoryButtons();
  return payload;
}

async function postSegmentOperation(path, payload = {}) {
  const result = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).then(response => response.json());
  if (!result.ok) throw new Error(result.error || '区間操作に失敗しました');
  state.editHistory = result.edit_history || state.editHistory;
  updateHistoryButtons();
  return result;
}

function matchesFilter(row, filter) {
  const decision = state.decisions[String(row.segment_index)];
  if (filter === 'terms') return row.needs_review && (row.applied_reference_terms?.length || row.possible_reference_terms?.length || row.identity_candidates?.length || row.verification_notes?.length);
  if (filter === 'large') return row.change_score >= 0.18;
  if (filter === 'review') return row.needs_review;
  if (filter === 'unapproved') return decision?.status !== 'approved';
  return true;
}

function render({preservePlayback = false} = {}) {
  const playbackSnapshot = preservePlayback ? capturePlaybackState() : null;
  const viewportAnchor = preservePlayback ? captureViewportAnchor() : null;
  if (preservePlayback) suppressPlaybackScrollUntil = performance.now() + 600;
  stopPlayback();
  activePlayContext = null;
  const filter = document.querySelector('#filter').value;
  const speakerFilter = document.querySelector('#speaker-filter').value;
  const query = document.querySelector('#search').value.trim().toLowerCase();
  const rows = state.rows
    .filter(row => {
      const speakerName = effectiveSpeakerName(row);
      return matchesFilter(row, filter)
        && (!speakerFilter || state.speakerFilterSegments?.has(row.segment_index))
        && (!query || `${speakerName} ${row.original_text} ${row.whisper_text} ${row.suggested_text}`.toLowerCase().includes(query));
    })
    .sort((left, right) => (
      Number(left.start_seconds || 0) - Number(right.start_seconds || 0)
      || Number(left.segment_index || 0) - Number(right.segment_index || 0)
    ));
  state.visibleRows = rows;
  document.querySelector('#visible-count').textContent = `${rows.length}件`;
  const container = document.querySelector('#items');
  container.replaceChildren();
  const splitModeEnabled = filter === 'all' && !query;
  const mergeModeEnabled = isFullTranscriptMode();
  for (const [rowPosition, row] of rows.entries()) {
    const card = document.querySelector('#item-template').content.firstElementChild.cloneNode(true);
    card.dataset.segmentIndex = String(row.segment_index);
    card.querySelector('.time').textContent = `${row.premiere_start}／音声 ${row.start_seconds.toFixed(1)}秒`;
    card.querySelector('.difference').textContent = `差分 ${Math.round(row.change_score * 100)}%`;
    const highlights = row.change_highlights || {};
    card.querySelector('.original').innerHTML = highlightedHtml(row.original_text, highlights.premiere);
    const visibleWhisperText = row.whisper_text || '（対応するWhisper文字なし）';
    card.querySelector('.whisper').innerHTML = highlightedHtml(visibleWhisperText, highlights.whisper);
    card.querySelector('.terms').innerHTML = termBadges(row);
    card.querySelector('.identity-hints').innerHTML = identityHints(row);
    card.querySelector('.verification-notes').innerHTML = verificationNotes(row);
    const textarea = card.querySelector('textarea');
    const proposalHighlight = card.querySelector('.proposal-highlight');
    const decision = state.decisions[String(row.segment_index)];
    const speakerSelect = card.querySelector('.speaker-select');
    const speakerEditor = card.querySelector('.speaker-editor');
    const speakerNew = card.querySelector('.speaker-new');
    const speakerSave = card.querySelector('.speaker-save');
    const speakerCancel = card.querySelector('.speaker-cancel');
    row._speakerName = effectiveSpeakerName(row);
    const speakerNames = Array.from(new Set([row._speakerName, ...state.speakerOptions].filter(Boolean)));
    for (const name of speakerNames) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      speakerSelect.append(option);
    }
    const newOption = document.createElement('option');
    newOption.value = '__new_speaker__';
    newOption.textContent = '＋ 新しい話者名…';
    speakerSelect.append(newOption);
    speakerSelect.value = row._speakerName;
    card.querySelector('.speaker-mode').textContent =
      isPlaceholderSpeaker(row.speaker_name) && !state.speakerMappings[row.speaker_name]
        ? '初回は同名を一括'
        : '変更はこの区間のみ';
    textarea.value = decision?.text || row.suggested_text;
    let highlightedText = row.suggested_text;
    let liveProposalRanges = (decision?.proposal_highlights || highlights.proposal || [])
      .map(range => ({...range}));
    if (!decision?.proposal_highlights && textarea.value !== highlightedText) {
      liveProposalRanges = HighlightRanges.remapAfterEdit(
        highlightedText,
        textarea.value,
        liveProposalRanges,
      );
    }
    highlightedText = textarea.value;
    row._proposalHighlightRanges = liveProposalRanges;
    const remapProposalHighlights = nextText => {
      liveProposalRanges = HighlightRanges.remapAfterEdit(
        highlightedText,
        nextText,
        liveProposalRanges,
      );
      highlightedText = nextText;
      row._proposalHighlightRanges = liveProposalRanges;
    };
    const refreshProposalHighlight = () => {
      proposalHighlight.innerHTML = highlightedHtml(textarea.value, liveProposalRanges);
    };
    const resizeProposal = () => {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.max(textarea.scrollHeight + 2, 110)}px`;
      proposalHighlight.style.height = textarea.style.height;
      refreshProposalHighlight();
    };
    const saved = card.querySelector('.saved');
    const statusBadge = card.querySelector('.status');
    const approveButton = card.querySelector('.approve');
    const premiereSelectionButton = card.querySelector('.use-premiere-selection');
    const originalElement = card.querySelector('.original');
    const whisperSelectionButton = card.querySelector('.use-whisper-selection');
    const whisperElement = card.querySelector('.whisper');
    const holdButton = card.querySelector('.hold');
    const splitButton = card.querySelector('.split-segment');
    const mergeNextButton = card.querySelector('.merge-next');
    splitButton.hidden = !splitModeEnabled;
    mergeNextButton.hidden = !mergeModeEnabled;
    mergeNextButton.disabled = !mergeModeEnabled || rowPosition >= rows.length - 1;
    let currentStatus = decision?.status || '';
    let currentSource = decision?.source || (currentStatus === 'approved' ? 'proposal' : '');
    let draftTimer = null;
    const applyCardState = (status, source = currentSource) => {
      currentStatus = status;
      currentSource = source;
      card.classList.toggle('is-approved', status === 'approved');
      card.classList.toggle('is-revising', status === 'revising');
      approveButton.classList.toggle('reedit', status === 'approved' || status === 'revising');
      if (status === 'approved') {
        statusBadge.hidden = false;
        statusBadge.className = 'status';
        statusBadge.textContent = source === 'premiere' ? '✓ Premiereに戻しました' : '✓ 修正案を承認済み';
        approveButton.textContent = '再修正を確定';
        holdButton.textContent = '後で確認に変更';
        textarea.readOnly = false;
        saved.textContent = '';
      } else if (status === 'revising') {
        statusBadge.hidden = false;
        statusBadge.className = 'status revising';
        statusBadge.textContent = '未確定の再修正';
        approveButton.textContent = '再修正を確定';
        holdButton.textContent = '後で確認';
        textarea.readOnly = false;
        saved.textContent = '';
      } else {
        statusBadge.hidden = true;
        statusBadge.className = 'status';
        approveButton.textContent = 'この修正案を承認';
        holdButton.textContent = '後で確認';
        textarea.readOnly = false;
        saved.textContent = status === 'hold' ? '後で確認' : '';
      }
    };
    applyCardState(currentStatus);
    const playButton = card.querySelector('.play');
    let hasPlaybackCue = false;
    const playFromEditor = () => play(
      row,
      playButton,
      textarea.value,
      hasPlaybackCue ? (textarea.selectionStart ?? 0) : 0,
    );
    card._playFromEditor = playFromEditor;
    const activatePlaybackContext = () => { activePlayContext = playFromEditor; };
    const updateSplitButton = () => {
      const start = textarea.selectionStart ?? 0;
      const end = textarea.selectionEnd ?? start;
      splitButton.disabled = !splitModeEnabled
        || start !== end
        || start <= 0
        || start >= textarea.value.length;
    };
    textarea.addEventListener('pointerup', () => {
      hasPlaybackCue = true;
      activatePlaybackContext();
      updateSplitButton();
    });
    textarea.addEventListener('focus', () => {
      activatePlaybackContext();
      updateSplitButton();
    });
    textarea.addEventListener('select', () => {
      hasPlaybackCue = true;
      activatePlaybackContext();
      updateSplitButton();
    });
    textarea.addEventListener('keyup', event => {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
        hasPlaybackCue = true;
        activatePlaybackContext();
        updateSplitButton();
      }
    });
    card.addEventListener('pointerenter', activatePlaybackContext);
    playButton.addEventListener('click', () => {
      activatePlaybackContext();
      playFromEditor();
    });
    textarea.addEventListener('input', () => {
      hasPlaybackCue = true;
      remapProposalHighlights(textarea.value);
      resizeProposal();
      updateSplitButton();
      if (currentStatus === 'approved') {
        applyCardState('revising', 'proposal');
        return;
      }
      if (currentStatus !== 'revising') return;
      clearTimeout(draftTimer);
      draftTimer = setTimeout(() => save(row, 'revising', textarea.value), 500);
    });
    const closeNewSpeaker = () => {
      speakerNew.hidden = true;
      speakerEditor.value = '';
      speakerSelect.hidden = false;
      speakerSelect.value = row._speakerName;
    };
    const commitSpeaker = async value => {
      const speakerName = String(value || '').trim();
      if (!speakerName || speakerName === row._speakerName) {
        closeNewSpeaker();
        return;
      }
      speakerSelect.disabled = true;
      speakerEditor.disabled = true;
      try {
        await updateSpeaker(row, speakerName);
      } catch (error) {
        speakerSelect.disabled = false;
        speakerEditor.disabled = false;
        closeNewSpeaker();
        showToast(error.message, true);
      }
    };
    speakerSelect.addEventListener('change', async () => {
      if (speakerSelect.value === '__new_speaker__') {
        speakerSelect.hidden = true;
        speakerNew.hidden = false;
        speakerEditor.focus();
        return;
      }
      if (speakerSelect.value !== row._speakerName) {
        speakerEditor.value = speakerSelect.value;
        await commitSpeaker(speakerSelect.value);
      }
    });
    speakerSave.addEventListener('click', () => commitSpeaker(speakerEditor.value.trim()));
    speakerCancel.addEventListener('click', closeNewSpeaker);
    speakerEditor.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        commitSpeaker(speakerEditor.value.trim());
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeNewSpeaker();
      }
    });
    approveButton.addEventListener('click', async () => {
      clearTimeout(draftTimer);
      const submittedText = textarea.value;
      const savedDecision = await save(row, 'approved', textarea.value, 'proposal');
      remapProposalHighlights(savedDecision.text);
      textarea.value = savedDecision.text;
      if (savedDecision.text !== submittedText) {
        await save(row, 'approved', savedDecision.text, 'proposal');
      }
      resizeProposal();
      applyCardState('approved', 'proposal');
      updateSummary();
      if (isUnapprovedQueueMode()) render({preservePlayback: true});
    });
    const setupSelectionRestore = (textElement, selectionButton, selectionSource) => {
      let selectedRange = null;
      const captureSelection = () => {
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
          selectedRange = null;
          selectionButton.hidden = true;
          return;
        }
        const range = selection.getRangeAt(0);
        if (!textElement.contains(range.startContainer) || !textElement.contains(range.endContainer)) {
          selectedRange = null;
          selectionButton.hidden = true;
          return;
        }
        const startRange = document.createRange();
        startRange.selectNodeContents(textElement);
        startRange.setEnd(range.startContainer, range.startOffset);
        const endRange = document.createRange();
        endRange.selectNodeContents(textElement);
        endRange.setEnd(range.endContainer, range.endOffset);
        selectedRange = { start: startRange.toString().length, end: endRange.toString().length };
        selectionButton.hidden = selectedRange.start === selectedRange.end;
      };
      textElement.addEventListener('mouseup', () => requestAnimationFrame(captureSelection));
      textElement.addEventListener('keyup', () => requestAnimationFrame(captureSelection));
      selectionButton.addEventListener('click', async () => {
        if (!selectedRange) return;
        clearTimeout(draftTimer);
        const result = await fetch('/api/restore-selection', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            segment_index: row.segment_index,
            current_text: textarea.value,
            selection_source: selectionSource,
            selection_start: selectedRange.start,
            selection_end: selectedRange.end,
          }),
        }).then(response => response.json());
        if (!result.ok) return;
        remapProposalHighlights(result.text);
        textarea.value = result.text;
        resizeProposal();
        await save(row, 'revising', result.text, 'proposal');
        applyCardState('revising', 'proposal');
        selectedRange = null;
        selectionButton.hidden = true;
        window.getSelection()?.removeAllRanges();
        updateSummary();
        textarea.focus();
        textarea.setSelectionRange(
          result.proposal_start,
          result.proposal_start + result.selected_text.length,
        );
      });
    };
    setupSelectionRestore(originalElement, premiereSelectionButton, 'premiere');
    setupSelectionRestore(whisperElement, whisperSelectionButton, 'whisper');
    holdButton.addEventListener('click', async () => {
      clearTimeout(draftTimer);
      await save(row, 'hold', textarea.value, currentSource || 'proposal');
      applyCardState('hold', currentSource || 'proposal');
      updateSummary();
    });
    splitButton.addEventListener('click', async () => {
      clearTimeout(draftTimer);
      try {
        if (currentStatus === 'revising') {
          await save(row, 'revising', textarea.value, currentSource || 'proposal');
        }
        const result = await postSegmentOperation('/api/segments/split', {
          segment_index: row.segment_index,
          caret: textarea.selectionStart,
          current_text: textarea.value,
        });
        if (state.speakerFilterSegments) {
          state.speakerFilterSegments.delete(row.segment_index);
          for (const index of result.new_segment_indices || []) state.speakerFilterSegments.add(index);
        }
        await refreshProjectState();
        render({preservePlayback: true});
        showToast('カーソル位置で区間を分割しました');
      } catch (error) {
        showToast(error.message, true);
      }
    });
    mergeNextButton.addEventListener('click', async () => {
      const nextRow = rows[rowPosition + 1];
      const nextCard = nextRow
        ? document.querySelector(`.card[data-segment-index="${nextRow.segment_index}"]`)
        : null;
      const nextText = nextCard?.querySelector('textarea')?.value;
      if (!nextRow || nextText == null) return;
      clearTimeout(draftTimer);
      try {
        if (currentStatus === 'revising') {
          await save(row, 'revising', textarea.value, currentSource || 'proposal');
        }
        await postSegmentOperation('/api/segments/merge-next', {
          segment_index: row.segment_index,
          current_text: textarea.value,
          next_text: nextText,
        });
        await refreshProjectState();
        render({preservePlayback: true});
        showToast(`次区間を「${row._speakerName}」として結合しました`);
      } catch (error) {
        showToast(error.message, true);
      }
    });
    container.append(card);
    resizeProposal();
    updateSplitButton();
  }
  restoreViewportAnchor(viewportAnchor);
  restorePlaybackState(playbackSnapshot);
}

async function init() {
  const payload = await refreshProjectState();
  document.querySelector('#project-title').textContent = `${payload.project_name}｜文字起こし修正`;
  document.querySelector('#source-media').textContent = `元素材: ${payload.source_media_name}`;
  if (payload.has_video) {
    video.hidden = false;
    audio.hidden = true;
    player = video;
    video.addEventListener('error', () => {
      video.hidden = true;
      audio.hidden = false;
      player = audio;
    }, {once:true});
  }
  populateSpeakerFilter();
  render();
}

document.querySelector('#filter').addEventListener('change', render);
document.querySelector('#speaker-filter').addEventListener('pointerdown', populateSpeakerFilter);
document.querySelector('#speaker-filter').addEventListener('change', () => {
  const speakerFilter = document.querySelector('#speaker-filter');
  if (speakerFilter.value === '__manage_speakers__') {
    const previousValue = speakerFilter.dataset.previousValue || '';
    speakerFilter.value = previousValue;
    openSpeakerManager(previousValue);
    return;
  }
  speakerFilter.dataset.previousValue = speakerFilter.value;
  refreshSpeakerFilterSegments();
  render();
});
document.querySelector('#search').addEventListener('input', render);
async function runSegmentHistory(direction) {
  try {
    await postSegmentOperation(`/api/segments/${direction}`);
    await refreshProjectState();
    if (document.querySelector('#speaker-filter').value) refreshSpeakerFilterSegments();
    render({preservePlayback: true});
    showToast(direction === 'undo' ? '区間操作を元に戻しました' : '区間操作をやり直しました');
  } catch (error) {
    showToast(error.message, true);
  }
}
document.querySelector('#undo-segment-edit').addEventListener('click', () => runSegmentHistory('undo'));
document.querySelector('#redo-segment-edit').addEventListener('click', () => runSegmentHistory('redo'));
document.querySelector('#export').addEventListener('click', async () => {
  if (!confirm(`${state.sourceMediaName} の確認内容をファイルに保存します。\nPremiereへの自動書き戻しは安全確認のため一時停止中です。`)) return;
  const button = document.querySelector('#export');
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = '保存中…';
  try {
    const response = await fetch('/api/export', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const result = await response.json();
    if (!response.ok || !result.ok || !result.json) throw new Error(result.error || result.json_warning || '修正版JSONを保存できませんでした');
    showToast(`確認内容を保存しました（${result.patch_count}件）。自動書き戻しは停止中です`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
});
audio.addEventListener('ended', stopPlayback);
video.addEventListener('ended', stopPlayback);
audio.addEventListener('seeked', () => scrollToPlaybackPosition(true));
video.addEventListener('seeked', () => scrollToPlaybackPosition(true));
audio.addEventListener('timeupdate', () => scrollToPlaybackPosition(false));
video.addEventListener('timeupdate', () => scrollToPlaybackPosition(false));
document.addEventListener('keydown', event => {
  if (
    (event.metaKey || event.ctrlKey)
    && ['ArrowUp', 'ArrowDown'].includes(event.key)
    && !speakerManager.open
  ) {
    event.preventDefault();
    moveToAdjacentPanel(event.key === 'ArrowDown' ? 1 : -1);
  } else if (!isEditingTarget(event) && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    runSegmentHistory(event.shiftKey ? 'redo' : 'undo');
  } else if (event.key === 'Escape') {
    stopPlayback();
  } else if (!isEditingTarget(event) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    stopPlayback();
  } else if (!isEditingTarget(event) && event.key.toLowerCase() === 'l') {
    event.preventDefault();
    shuttleForward();
  } else if (!isEditingTarget(event) && event.key.toLowerCase() === 'j') {
    event.preventDefault();
    shuttleReverse();
  } else if (
    (event.key === 'Enter' || event.code === 'NumpadEnter')
    && (event.metaKey || event.ctrlKey)
    && activePlayContext
  ) {
    event.preventDefault();
    if (!player.paused || reverseTimer) {
      stopPlayback();
    } else {
      // A pause made with the native media controls can leave the old card's
      // button active. Clear that stale state before starting from the current
      // editor caret, so Command+Return has an unambiguous play/stop contract.
      resetPlay();
      activePlayContext();
    }
  }
});
init();
