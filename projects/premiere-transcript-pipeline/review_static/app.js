/* Adapted to the independent master API; layout comes from the existing review UI. */
let project, busy = false, player, stopAt = null;
const $ = selector => document.querySelector(selector);
const notice = (message, error = false) => {
  $('#notice').textContent = message;
  $('#notice').classList.toggle('error', error);
};
function stop() {
  if (player) player.pause();
  stopAt = null;
}
async function load() {
  const response = await fetch('/api/project', {cache: 'no-store'});
  const value = await response.json();
  if (!response.ok || value.ok === false) throw Error(value.error || '読み込めませんでした');
  project = value;
}
function setBusy(value) {
  busy = value;
  document.body.setAttribute('aria-busy', String(value));
  if (value) document.querySelectorAll('button, select, input').forEach(element => { element.disabled = true; });
}
async function operate(path, payload = {}) {
  if (busy) return;
  stop();
  setBusy(true);
  let message, failed = false;
  try {
    const response = await fetch(path, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Review-Token': project.token},
      body: JSON.stringify({...payload, revision: project.revision}),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || '保存できませんでした');
    message = path === '/api/export'
      ? `Premiere用のファイルを保存しました。保存先: ${result.json}`
      : '変更を保存しました。';
  } catch (error) {
    message = error.message; failed = true;
  }
  // Always reconcile the displayed revision, including after a stale-tab rejection.
  try { await load(); } catch (error) { message += ` 最新状態を読み込めません: ${error.message}`; failed = true; }
  setBusy(false);
  render();
  notice(message, failed);
}
function time(seconds) {
  return `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
}
function render() {
  const selected = $('#speaker-filter').value, query = $('#search').value.trim().toLowerCase();
  $('#project-title').textContent = `${project.project_name}｜話者と区間の修正`;
  $('#source-media').textContent = project.source_media_name ? `素材: ${project.source_media_name}` : '音声なしで確認中';
  $('#summary').textContent = `${project.candidates.items.length}区間・変更は作業用データに保存済み`;
  const filter = $('#speaker-filter');
  filter.replaceChildren(new Option('すべての話者', ''));
  for (const name of project.speaker_options) filter.add(new Option(name, name));
  filter.value = selected;
  filter.disabled = false;
  $('#search').disabled = false;
  $('#undo-segment-edit').disabled = !project.edit_history.can_undo;
  $('#redo-segment-edit').disabled = !project.edit_history.can_redo;
  $('#export').disabled = false;
  const rows = project.candidates.items.filter(row => (!selected || row.speaker_name === selected)
    && (!query || `${row.speaker_name} ${row.suggested_text}`.toLowerCase().includes(query)));
  $('#visible-count').textContent = `${rows.length}件`;
  $('#items').replaceChildren();
  for (const row of rows) {
    const card = $('#item-template').content.firstElementChild.cloneNode(true);
    card.dataset.segmentIndex = row.segment_index;
    card.querySelector('.time').textContent = `${time(row.start_seconds)} – ${time(row.end_seconds)}`;
    const textarea = card.querySelector('textarea');
    textarea.value = row.suggested_text;
    const speaker = card.querySelector('.speaker-select');
    for (const name of project.speaker_options) speaker.add(new Option(name, name));
    speaker.value = row.speaker_name;
    speaker.addEventListener('change', () => operate('/api/speaker', {
      segment_index: row.segment_index, speaker_name: speaker.value, scope: 'segment',
    }));
    const split = card.querySelector('.split-segment');
    const boundary = card.querySelector('.split-boundary');
    for (const offset of row.word_boundaries) {
      const left = row.suggested_text.slice(Math.max(0, offset - 8), offset);
      const right = row.suggested_text.slice(offset, offset + 8);
      boundary.add(new Option(`${left} ｜ ${right}`, String(offset)));
    }
    const updateSplit = () => {
      split.disabled = busy || textarea.selectionStart !== textarea.selectionEnd
        || !row.word_boundaries.includes(textarea.selectionStart);
      boundary.value = split.disabled ? '' : String(textarea.selectionStart);
    };
    boundary.disabled = !row.word_boundaries.length;
    boundary.addEventListener('change', () => {
      if (!boundary.value) { split.disabled = true; return; }
      const offset = Number(boundary.value);
      textarea.focus();
      textarea.setSelectionRange(offset, offset);
      updateSplit();
    });
    for (const event of ['select', 'keyup', 'pointerup', 'focus']) textarea.addEventListener(event, updateSplit);
    split.addEventListener('click', () => operate('/api/segments/split', {
      segment_index: row.segment_index, caret: textarea.selectionStart, current_text: row.suggested_text,
    }));
    const next = project.candidates.items[row.segment_index + 1];
    const merge = card.querySelector('.merge-next');
    merge.disabled = !next || !!selected || !!query;
    merge.title = selected || query ? '全文表示で結合できます' : '次の区間を、この区間の話者として結合します';
    merge.addEventListener('click', () => operate('/api/segments/merge-next', {
      segment_index: row.segment_index, current_text: row.suggested_text, next_text: next.suggested_text,
    }));
    const play = card.querySelector('.play');
    play.disabled = !player;
    play.addEventListener('click', async () => {
      if (!player.paused) { stop(); return; }
      player.currentTime = row.start_seconds; stopAt = row.end_seconds;
      try { await player.play(); } catch (_) { notice('この形式の音声はブラウザーで再生できません。', true); }
    });
    $('#items').append(card);
    textarea.style.height = `${Math.max(80, textarea.scrollHeight + 2)}px`;
  }
}
async function init() {
  await load();
  if (project.source_media_name) {
    player = project.has_video ? $('#video') : $('#audio');
    player.src = project.has_video ? '/media' : '/audio.wav';
    player.hidden = false;
    player.addEventListener('timeupdate', () => { if (stopAt !== null && player.currentTime >= stopAt) stop(); });
    player.addEventListener('error', () => notice('音声を再生できません。話者と区間の編集は続けられます。', true));
  }
  render();
}
$('#search').addEventListener('input', render);
$('#speaker-filter').addEventListener('change', render);
$('#undo-segment-edit').addEventListener('click', () => operate('/api/segments/undo'));
$('#redo-segment-edit').addEventListener('click', () => operate('/api/segments/redo'));
$('#export').addEventListener('click', () => operate('/api/export'));
document.addEventListener('keydown', event => { if (event.key === 'Escape') stop(); });
init().catch(error => notice(error.message, true));
