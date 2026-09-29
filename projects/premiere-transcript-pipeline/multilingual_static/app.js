let project,busy=false,player,stopAt=null,activeSegmentIndex=null;const drafts=new Map(),openDetails=new Set(),autoAdoptChoices=new Map();const $=s=>document.querySelector(s);
let speakerDialogSegment=null,alignmentRow=null,alignmentTokens=[],alignmentOriginalZeroWords=new Set(),alignmentDraftEnd=null,shiftPreviewPlaying=false;
function reindexAfterInsertion(insertedIndex){
 const shifted=new Map();for(const [key,value] of drafts){const [index,field]=key.split(':');const number=Number(index);shifted.set(`${number>=insertedIndex?number+1:number}:${field}`,value)}drafts.clear();for(const [key,value] of shifted)drafts.set(key,value);
 const details=[...openDetails];openDetails.clear();for(const index of details)openDetails.add(index>=insertedIndex?index+1:index);
 const choices=[...autoAdoptChoices];autoAdoptChoices.clear();for(const [index,value] of choices)autoAdoptChoices.set(index>=insertedIndex?index+1:index,value);
 if(activeSegmentIndex!==null&&activeSegmentIndex>=insertedIndex)activeSegmentIndex++;
}
function reindexAfterRemoval(removedIndex){
 const shifted=new Map();for(const [key,value] of drafts){const [index,field]=key.split(':');const number=Number(index);if(number!==removedIndex)shifted.set(`${number>removedIndex?number-1:number}:${field}`,value)}drafts.clear();for(const [key,value] of shifted)drafts.set(key,value);
 const details=[...openDetails];openDetails.clear();for(const index of details)if(index!==removedIndex)openDetails.add(index>removedIndex?index-1:index);
 const choices=[...autoAdoptChoices];autoAdoptChoices.clear();for(const [index,value] of choices)if(index!==removedIndex)autoAdoptChoices.set(index>removedIndex?index-1:index,value);
 if(activeSegmentIndex!==null)activeSegmentIndex=activeSegmentIndex>=removedIndex?Math.max(0,activeSegmentIndex-1):activeSegmentIndex;
}
async function deleteSegment(row){
 if(drafts.has(`${row.segment_index}:original`)||drafts.has(`${row.segment_index}:translation`)){note('この発言に未保存の本文または訳があります。先に保存してください。',true);return}
 const ok=await op('/api/delete',{segment_index:row.segment_index,current_text:row.text,expected_fingerprint:row.retry_fingerprint},null,()=>reindexAfterRemoval(row.segment_index));
 if(ok)note('区間を削除しました。「元に戻す」で復元できます。');
}
function splitTimeFromCaret(row,caret,text){
 const ratio=caret/text.length;
 return row.start_seconds+(row.end_seconds-row.start_seconds)*ratio;
}
function playbackTimeFromCaret(row,caret,text){
 const start=row.start_seconds,end=row.end_seconds;
 if(!text.length||!Number.isInteger(caret))return start;
 const offset=Math.max(0,Math.min(caret,text.length));
 const boundaries=row.word_boundaries||[],words=row.timing_words||[];
 if(row.alignment_status==='word-timed'&&text===row.text&&words.length===boundaries.length+1){
  const index=boundaries.findIndex(boundary=>boundary>offset);
  const wordIndex=index<0?words.length-1:index;
  const left=wordIndex?boundaries[wordIndex-1]:0;
  const right=wordIndex<boundaries.length?boundaries[wordIndex]:text.length;
  const fraction=right>left?(offset-left)/(right-left):0;
  return Math.max(start,Math.min(end-.001,words[wordIndex].start+(words[wordIndex].end-words[wordIndex].start)*fraction));
 }
 return Math.max(start,Math.min(end-.001,start+(end-start)*offset/text.length));
}
const note=(m,e=false)=>{$('#notice').textContent=m;$('#notice').classList.toggle('error',e)};
async function load(){const r=await fetch('/api/project',{cache:'no-store'});project=await r.json();if(!r.ok)throw Error(project.error)}
function processingLabel(path){
 if(path==='/api/text'||path==='/api/references/iso-adopt-all')return '本文を保存中';
 if(path==='/api/translation')return '訳を保存中';
 if(path.startsWith('/api/split'))return '分割中';
 if(path==='/api/merge')return '結合中';
 if(path==='/api/align/manual')return '単語時刻を保存中';
 if(path==='/api/align/shift'||path==='/api/align/start')return '発話位置を保存中';
 return '処理中';
}
function lockActionButtons(label){
 const clicked=document.activeElement?.closest?.('button');
 const clickedLabel=clicked?.textContent;
 const buttons=[...document.querySelectorAll('button')].map(button=>[button,button.disabled]);
 document.body.classList.add('is-busy');document.body.setAttribute('aria-busy','true');
 buttons.forEach(([button])=>{button.disabled=true});
 if(clicked)clicked.textContent=`${label}…`;
 note(`${label}…`);
 let released=false;
 return ()=>{if(released)return;released=true;buttons.forEach(([button,disabled])=>{button.disabled=disabled});if(clicked)clicked.textContent=clickedLabel;document.body.classList.remove('is-busy');document.body.setAttribute('aria-busy','false')};
}
async function op(path,p={},draftKey=null,onSuccess=null){
 if(busy)return false;
 busy=true;const unlock=lockActionButtons(processingLabel(path));
 let success=false,message='保存しました',messageError=false;
 try{
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':project.token},body:JSON.stringify({...p,revision:project.revision})});
  const v=await r.json();if(!r.ok)throw Error(v.error);
  if(draftKey)(Array.isArray(draftKey)?draftKey:[draftKey]).forEach(key=>drafts.delete(key));
  if(onSuccess)onSuccess();
  if(path.includes('export')){
   lastExport={srt_path:v.srt_path,json_path:v.json_path,srt_status:v.srt_path?'exported':'not_exported',json_status:v.json_path?'exported':'not_exported',premiere_status:'not_applied'};
   const layoutIssues=v.srt_layout_problems||[];
   const layoutNotice=layoutIssues.length?`字幕の行数・幅に未解決あり: ${layoutIssues.join('、')}`:'改行・幅のデータ検査済み（Premiere画面での表示確認は別途必要）';
   message=`SRTを保存しました: ${v.srt_path} ／ ${layoutNotice}${v.json_path?` ／ Premiere JSON: ${v.json_path}`:` ／ Premiere JSONは未作成（${v.premiere_problems.join('、')}）`}`;
   messageError=layoutIssues.length>0;
  }
  success=true;
 }catch(error){message=error.message;messageError=true}
 finally{
  try{await load();unlock();render()}catch(error){message=`状態の再読み込みに失敗しました: ${error.message}`;messageError=true}
  unlock();busy=false;note(message,messageError);
 }
 return success;
}
const time=s=>`${Math.floor(s/60)}:${(s%60).toFixed(2).padStart(5,'0')}`;
const preciseTime=s=>`${Math.floor(s/60)}:${(s%60).toFixed(3).padStart(6,'0')}`;
function isoTime(seconds){
 const start=project.multi_iso_interval?.start_tc;
 if(!start)return time(seconds);
 const parts=start.split(':').map(Number),total=parts[0]*3600+parts[1]*60+parts[2]+seconds;
 return `${String(Math.floor(total/3600)).padStart(2,'0')}:${String(Math.floor(total/60)%60).padStart(2,'0')}:${(total%60).toFixed(3).padStart(6,'0')}`;
}
function renderHighlightedReference(target,value,ranges){
 let cursor=0;const text=Array.from(value);
 for(const range of ranges){target.append(document.createTextNode(text.slice(cursor,range.start).join('')));const mark=document.createElement('mark');mark.textContent=text.slice(range.start,range.end).join('');target.append(mark);cursor=range.end}
 target.append(document.createTextNode(text.slice(cursor).join('')));
}
function reanchorHighlightRanges(source,ranges,edited){
 return ranges.map(span=>{const term=Array.from(source).slice(span.start,span.end).join(''),at=edited.indexOf(term);if(!term||at<0||edited.indexOf(term,at+1)>=0)return null;const start=Array.from(edited.slice(0,at)).length;return {start,end:start+Array.from(term).length}}).filter(Boolean).sort((a,b)=>a.start-b.start).filter((span,index,all)=>!all.some((other,j)=>j!==index&&other.start<span.end&&span.start<other.end));
}
function syncOriginalHighlight(card,row,original){
 const proposal=row.reference_proposal,applied=row.reference_applied;
 const source=proposal||applied,ranges=source?reanchorHighlightRanges(source.text,source.highlights||source.ranges,original.value):[];
 const backdrop=card.querySelector('.reference-proposal');backdrop.replaceChildren();renderHighlightedReference(backdrop,original.value,ranges);
 backdrop.scrollTop=original.scrollTop;backdrop.scrollLeft=original.scrollLeft;
}
function resizeOriginalEditor(original){
 original.style.height='auto';
 original.style.height=`${Math.max(120,original.scrollHeight)}px`;
}
function resizeAllOriginalEditors(){
 const editors=[...document.querySelectorAll('#items textarea.original')];
 editors.forEach(editor=>{editor.style.height='auto'});
 const heights=editors.map(editor=>Math.max(120,editor.scrollHeight));
 editors.forEach((editor,index)=>{editor.style.height=`${heights[index]}px`});
}
function renderReferenceCandidates(card,row){
 const corrections=row.iso_evidence?.reference_corrections||[];
 if(!corrections.length)return;
 const proposal=row.reference_proposal,intro=card.querySelector('.reference-intro');intro.hidden=false;
 card.querySelector('.original-column > h2').textContent='本文';
 intro.textContent=proposal?`資料補正 ${proposal.indexes.length}件を反映・保存前`:corrections.some(x=>x.adopted)?'資料補正を採用済み':'資料補正の候補が重なっています。変更点を開いて個別に確認してください。';
 card.querySelector('.reference-details').hidden=false;
 card.querySelector('.reference-before').textContent=row.iso_evidence?.raw_asr_text||corrections[0].before||'';
 const list=card.querySelector('.reference-candidates');list.replaceChildren();
 for(const [index,item] of corrections.entries()){
  const entry=document.createElement('div');entry.className='reference-candidate';
  const title=document.createElement('h3');title.textContent=`変更 ${index+1}`;entry.append(title);
  const change=document.createElement('p');change.className='reference-change';change.textContent=`変更点: ${item.evidence||'全文を比較してください'}`;entry.append(change);
  const source=document.createElement('p');source.className='reference-source';source.textContent=`参照: ${item.source_name||'不明'}`;entry.append(source);
  const status=document.createElement('p');status.className='reference-status';status.textContent=item.adopted?'採用済み':item.status==='candidate'?'未採用・候補':'保留';entry.append(status);
  if(item.status==='candidate'){const button=document.createElement('button');button.textContent='この補正候補を採用';button.onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから資料補正を採用してください。',true);return}op('/api/references/iso-adopt',{segment_index:row.segment_index,correction_index:index,current_text:row.text})};entry.append(button)}
  list.append(entry);
 }
}
function renderIsoDetails(card,row){
 const data=row.iso_evidence||{},details=card.querySelector('.iso-details');
 if(!Object.keys(data).length)return;
 details.hidden=false;
 card.querySelector('.time').textContent=`${isoTime(row.start_seconds)}–${isoTime(row.end_seconds)}`;
 const lines=[
  ['元ISO',data.source_iso||''],['ISO固定話者',data.source_iso_speaker||''],
  ['最終話者',row.speaker_name],['speaker confidence',data.speaker_confidence],
  ['1位・2位ISO音量差',data.speaker_evidence?.volume?.margin_db===undefined?'':`${data.speaker_evidence.volume.margin_db} dB`],
  ['AAI speaker',data.aai_speaker??'なし'],['AAI confidence',data.aai_confidence],
  ['detected language',data.detected_language||'未検出'],
  ['周波数補助スコア',JSON.stringify(data.speaker_evidence?.frequency_similarity||{})],
  ['ambiguous',String(!!data.ambiguous)],['overlap候補',String(!!data.overlap)],
  ['duplicate候補',JSON.stringify(data.duplicate_candidates||[])],
  ['pyannote使用',String(!!data.speaker_evidence?.pyannote_used)],
  ['資料補正前のASR原文',data.raw_asr_text||''],
  ['確認版の選択',data.selection_note||'自動統合候補'],
  ['話者指定',data.speaker_label_source==='manual'?'手動修正':'自動候補']
 ];
 const box=card.querySelector('.iso-evidence');box.replaceChildren();
 for(const [label,value] of lines){const p=document.createElement('p');const b=document.createElement('strong');b.textContent=`${label}: `;p.append(b,document.createTextNode(String(value??'')));box.append(p)}
}
function updateAlignmentPosition(){if(player&&alignmentRow&&$('#alignment-dialog').open){if(player.paused)shiftPreviewPlaying=false;$('#alignment-position').textContent=preciseTime(player.currentTime);$('#alignment-play').textContent=player.paused?'▶ この区間を再生':'⏸ 境目で一時停止';$('#alignment-shift-preview').textContent=shiftPreviewPlaying&&!player.paused?'⏸ 一時停止':'▶ 新しい開始から試聴'}}
function pruneUnchangedDrafts(){for(const [key,value] of drafts){const [index,field]=key.split(':'),row=project.items[Number(index)];if(!row)continue;const saved=field==='original'?(row.reference_proposal?.text||row.text):row.language==='ja-jp'?'':row.translation.text;if(value===saved)drafts.delete(key)}}
function translationCandidateRows(){
 const rows=new Map();
 const byText=new Map();
 for(const item of project.items){const matches=byText.get(item.text)||[];matches.push(item);byText.set(item.text,matches)}
 for(const candidate of project.translation_group_candidates||[]){
  // Segment indices shift after deletion/merge/split. Resolve each saved source
  // to its unique current text and audio interval before showing a suggestion.
  if(candidate.source?.length!==candidate.segment_translations?.length)continue;
  const current=candidate.source.map(source=>{
   const matches=(byText.get(source.text)||[]).filter(item=>
    Math.abs(item.start_seconds-source.start)<.002&&Math.abs(item.end_seconds-source.end)<.002);
   return matches.length===1?matches[0]:null;
  });
  if(current.some(item=>!item)||new Set(current.map(item=>item.segment_index)).size!==current.length)continue;
  const first=current[0];
  if(!first||first.language==='ja-jp'||first.language==='??-??'||
     current.some(item=>item.speaker_name!==first.speaker_name||item.language!==first.language))continue;
  current.forEach((item,j)=>{if(!item.translation.text?.trim()&&candidate.segment_translations[j]?.trim())rows.set(item.segment_index,{candidate,text:candidate.segment_translations[j],current})});
 }
 return rows;
}
function needsTranslationOrLanguageReview(row){return row.language==='??-??'||(row.language!=='ja-jp'&&row.translation.status!=='ready')}
function languageSourceLabel(source){return {manual:'手動確認',inferred:'推定・要確認','provider-and-text':'AAI＋本文判定',provider:'AAI判定',initial_manual:'開始時に指定'}[source]||'判定元未確認'}
function render(){pruneUnchangedDrafts();const active=document.activeElement,card=active?.closest?.(".card"),focus=card&&active.matches("textarea.original,textarea.translation")?{index:card.dataset.segmentIndex,kind:active.classList.contains("original")?"original":"translation",start:active.selectionStart,end:active.selectionEnd}:null;const scrollY=window.scrollY,sf=$('#speaker-filter').value,lf=$('#language-filter').value,q=$('#search').value.toLowerCase(),candidateRows=translationCandidateRows();renderWorkflow();renderReferences();$('#title').textContent=`${project.project_name}｜多言語レビュー`;const isoAmbiguous=project.items.filter(x=>x.iso_evidence?.ambiguous||x.iso_evidence?.overlap).length;$('#summary').textContent=`${project.items.length}発言・ISO判定の要確認 ${isoAmbiguous}件・下訳候補 ${candidateRows.size}件・本文／話者／言語／日本語訳を独立保存`;
 const sp=$('#speaker-filter');sp.replaceChildren(new Option('すべて',''));project.speaker_options.forEach(x=>sp.add(new Option(x,x)));sp.value=sf;const la=$('#language-filter');la.replaceChildren(new Option('すべて',''));Object.entries(project.language_options).forEach(([k,v])=>la.add(new Option(v,k)));la.value=lf;
 $('#undo').disabled=!project.can_undo;$('#redo').disabled=!project.can_redo;$('#provider').textContent=project.providers.translation?`翻訳: OpenAI / ${project.providers.translation_model}`:'翻訳生成は未設定です。OPENAI_API_KEY と TRANSLATION_MODEL をサーバー環境へ設定してください。手動入力・保存は利用できます。';
 const ja=$('#japanese');if(project.japanese_url){ja.href=project.japanese_url;ja.hidden=false}const needsTranslationReview=$('#candidate-filter').checked;const rows=project.items.filter(x=>(!needsTranslationReview||needsTranslationOrLanguageReview(x))&&(!sf||x.speaker_name===sf)&&(!lf||x.language===lf)&&(!q||`${x.speaker_name} ${x.text} ${x.reference_proposal?.text||''} ${x.translation.text} ${candidateRows.get(x.segment_index)?.text||''}`.toLowerCase().includes(q)));$('#count').textContent=`${rows.length}件`;$('#items').replaceChildren();if(needsTranslationReview&&rows.length===0){const empty=document.createElement('p');empty.className='helper';empty.setAttribute('role','status');const remaining=project.items.filter(needsTranslationOrLanguageReview).length;empty.textContent=remaining===0?'訳の未確定・言語未指定はありません。翻訳が必要な区間の訳は保存済みです。':'該当する区間はありません。話者・言語・検索の絞り込みを解除してください。';$('#items').append(empty)}
 for(const row of rows){const c=$('#card').content.firstElementChild.cloneNode(true);c.dataset.segmentIndex=row.segment_index;c.querySelector('.time').textContent=`${time(row.start_seconds)}–${time(row.end_seconds)}`;const speaker=c.querySelector('.speaker');project.speaker_options.forEach(x=>speaker.add(new Option(x,x)));speaker.value=row.speaker_name;speaker.onchange=()=>op('/api/speaker',{segment_index:row.segment_index,speaker_name:speaker.value});c.querySelector('.manage-card-speakers').onclick=()=>openSpeakerDialog(row.segment_index);const lang=c.querySelector('.language');Object.entries(project.language_options).forEach(([k,v])=>lang.add(new Option(v,k)));lang.value=row.language;lang.onchange=()=>op('/api/language',{segment_index:row.segment_index,language:lang.value});const languageSource=c.querySelector('.language-source');languageSource.textContent=languageSourceLabel(row.language_source);if(row.language_inference?.reason)languageSource.title=row.language_inference.reason;const align=c.querySelector('.alignment');align.textContent=row.alignment_status==='word-timed'?'単語時刻あり':'単語時刻との整列 要確認';align.classList.toggle('unresolved',row.alignment_status!=='word-timed');const alignButton=c.querySelector('.align-manual');alignButton.hidden=false;alignButton.textContent=row.alignment_status==='word-timed'?'単語時刻を調整':'単語時刻を整列';alignButton.onclick=()=>openAlignmentDialog(row);
 const originalKey=`${row.segment_index}:original`,translationKey=`${row.segment_index}:translation`;const original=c.querySelector('.original');original.value=drafts.has(originalKey)?drafts.get(originalKey):(row.reference_proposal?.text||row.text);const split=c.querySelector('.split'),splitHint=c.querySelector('.split-hint');let splitCaret=null,splitSelected=false;split.textContent='入力位置で分割';const updateSplit=()=>{if(document.activeElement===original){splitCaret=original.selectionStart;splitSelected=original.selectionStart!==original.selectionEnd}const caret=splitCaret,reason=row.end_seconds-row.start_seconds<0.003?'分割できる音声の長さがありません。':caret===null?'本文内に入力位置を置いてください。':splitSelected?'文字の選択を解除してください。':!original.value.slice(0,caret).trim()||!original.value.slice(caret).trim()?'本文の途中に入力位置を置いてください。':'';split.disabled=!!reason;splitHint.textContent=reason||`本文の${caret}文字目で分割します。単語境界以外では音声時刻を推定するため、後で確認できます。`;split.title=splitHint.textContent};original.oninput=()=>{resizeOriginalEditor(original);if(original.value===(row.reference_proposal?.text||row.text))drafts.delete(originalKey);else drafts.set(originalKey,original.value);syncOriginalHighlight(c,row,original);updateSplit();updateSaveState()};original.onscroll=()=>syncOriginalHighlight(c,row,original);for(const event of ['select','keyup','pointerup','focus','click','blur'])original.addEventListener(event,updateSplit);updateSplit();const saveOriginal=c.querySelector('.save-original'),saveStatus=c.querySelector('.original-save-status');const updateSaveState=()=>{const changed=drafts.has(originalKey),proposal=!!row.reference_proposal;saveOriginal.disabled=!changed&&!proposal;saveOriginal.textContent=proposal?'補正済み本文を採用・保存':changed?'本文を保存':'保存済み';saveStatus.textContent=changed?'未保存の本文あり':proposal?'資料補正は未採用':'保存済み';saveStatus.classList.toggle('unsaved',changed||proposal)};updateSaveState();saveOriginal.onclick=()=>row.reference_proposal?op('/api/references/iso-adopt-all',{segment_index:row.segment_index,current_text:row.text,proposal_text:row.reference_proposal.text,edited_text:original.value},originalKey):op('/api/text',{segment_index:row.segment_index,text:original.value},originalKey);const tr=c.querySelector('.translation'),groupCandidate=candidateRows.get(row.segment_index);
 const translationBase=groupCandidate?.text||row.translation.text;
 tr.value=drafts.has(translationKey)?drafts.get(translationKey):translationBase;
 const ts=c.querySelector('.translation-status'),saveTranslation=c.querySelector('.save-translation');
 const savedTranslationStatus=groupCandidate?'下訳候補・未確認':row.translation.status==='ready'?(row.translation.provider==='manual'?'保存済み（手動）':'生成済み・内容未確認'):{stale:'更新が必要',missing:'未入力'}[row.translation.status]||row.translation.status;
 const updateTranslationSaveState=()=>{const changed=drafts.has(translationKey);ts.textContent=changed?'修正中・未保存':savedTranslationStatus;ts.classList.toggle('stale',changed||row.translation.status!=='ready');saveTranslation.textContent=groupCandidate?'訳を確定・保存':'訳を保存';saveTranslation.disabled=row.language==='ja-jp'||(!!groupCandidate&&!tr.value.trim())};
 tr.oninput=()=>{if(tr.value===translationBase)drafts.delete(translationKey);else drafts.set(translationKey,tr.value);updateTranslationSaveState()};
 const translationContext=c.querySelector('.translation-context');
 if(groupCandidate){translationContext.hidden=false;const body=translationContext.querySelector('.translation-context-body'),candidate=groupCandidate.candidate;const full=document.createElement('p');full.textContent=`全体訳: ${candidate.full_translation}`;body.append(full);for(const item of groupCandidate.current){const line=document.createElement('p');line.textContent=`${time(item.start_seconds)} ${item.speaker_name}: ${item.text}`;body.append(line)}if(candidate.warnings?.length){const warning=document.createElement('p');warning.className='error';warning.textContent=`要確認: ${candidate.warnings.join(' ／ ')}`;body.append(warning)}}
 if(row.language==='ja-jp'){tr.value='';tr.placeholder='原語が日本語のため訳は不要です';tr.disabled=true}
 updateTranslationSaveState();
 if(row.language==='ja-jp'){ts.textContent='原語が日本語のため訳不要';ts.classList.remove('stale')}
 saveTranslation.onclick=()=>op('/api/translation',{segment_index:row.segment_index,text:tr.value},translationKey);
 const gen=c.querySelector('.generate');gen.disabled=!!groupCandidate||!project.providers.translation||row.language==='??-??'||row.language==='ja-jp'||(!!row.translation.text?.trim()&&(row.translation.provider==='manual'||row.translation.status==='ready'));gen.title=row.language==='??-??'?'発話言語を確認してください':groupCandidate?'文脈単位の下訳候補があります。確認して保存してください。':row.translation.text?.trim()?'既存の訳を保持します':'';gen.onclick=()=>{if(drafts.size){note('未保存の原文または訳をすべて保存してください。',true);return}op('/api/translation/generate',{segment_index:row.segment_index,expected_fingerprint:row.translation_fingerprint})};
 split.onclick=async()=>{const caret=splitCaret;if(caret===null||splitSelected||!original.value.slice(0,caret).trim()||!original.value.slice(caret).trim()){updateSplit();return}const exact=row.alignment_status==='word-timed'&&row.word_boundaries.includes(caret)&&original.value===row.text&&!drafts.has(translationKey)&&!row.reference_proposal;let path,payload;if(exact){path='/api/split';payload={segment_index:row.segment_index,caret,current_text:row.text}}else{const text=original.value;path='/api/split/unaligned';payload={segment_index:row.segment_index,current_text:row.text,expected_fingerprint:row.retry_fingerprint,left_text:text.slice(0,caret),right_text:text.slice(caret),requested_time:splitTimeFromCaret(row,caret,text),draft_text:text,...(drafts.has(translationKey)?{draft_translation:tr.value}:{})}}const ok=await op(path,payload,[originalKey,translationKey],()=>reindexAfterInsertion(row.segment_index+1));if(ok){note(exact?'二つの発言に分割しました。':'入力位置で二つに分割しました。音声の境目は推定値です。単語時刻を確認してください。')}else{const current=[...document.querySelectorAll('#items .card')].find(card=>card.dataset.segmentIndex===String(row.segment_index));const hint=current?.querySelector('.split-hint');if(hint){hint.textContent=`分割できませんでした: ${$('#notice').textContent}`;hint.classList.add('error');hint.setAttribute('role','alert')}}};const next=project.items[row.segment_index+1];const merge=c.querySelector('.merge'),target=c.querySelector('.merge-target');merge.disabled=!next;
 const overlapping=next&&Math.max(...row.timing_words.map(word=>word.end))>next.timing_words[0].start;
 target.textContent=next?`結合先: ${time(next.start_seconds)}・${next.speaker_name}（結合後の話者は ${row.speaker_name}）${overlapping?'・時刻重複あり、結合後に単語時刻を要確認':''}`:'最後の発言です';
 merge.onclick=async()=>{if(!next)return;
  const relatedDrafts=[originalKey,translationKey,`${next.segment_index}:original`,`${next.segment_index}:translation`];
  const payload={segment_index:row.segment_index,current_text:row.text,next_text:next.text,
    left_draft_text:original.value,right_draft_text:drafts.get(`${next.segment_index}:original`)??next.reference_proposal?.text??next.text};
  if(drafts.has(translationKey))payload.left_draft_translation=tr.value;
  if(drafts.has(`${next.segment_index}:translation`))payload.right_draft_translation=drafts.get(`${next.segment_index}:translation`);
  const ok=await op('/api/merge',payload,relatedDrafts,()=>reindexAfterRemoval(next.segment_index));
  if(!ok){const current=[...document.querySelectorAll('#items .card')].find(card=>card.dataset.segmentIndex===String(row.segment_index));const message=current?.querySelector('.merge-target');if(message){message.textContent=$('#notice').textContent;message.classList.add('error')}}
 };const remove=c.querySelector('.delete-segment');remove.disabled=project.items.length<2;remove.title=remove.disabled?'最後の発言は削除できません':'この発言だけを作業データから除く（元に戻せます）';remove.onclick=()=>deleteSegment(row);const details=c.querySelector('.retry-details');details.open=openDetails.has(row.segment_index);details.ontoggle=()=>details.open?openDetails.add(row.segment_index):openDetails.delete(row.segment_index);const auto=c.querySelector('.auto-adopt');auto.checked=autoAdoptChoices.has(row.segment_index)?autoAdoptChoices.get(row.segment_index):true;auto.onchange=()=>autoAdoptChoices.set(row.segment_index,auto.checked);const retry=c.querySelector('.saved-retry');retry.disabled=!project.providers.saved_assemblyai||!!row.reference_proposal;retry.onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから起こし直してください。',true);return}op('/api/retry/saved',{segment_index:row.segment_index,language:row.language,auto_adopt:auto.checked})};const live=c.querySelector('.live-retry');live.disabled=!project.providers.assemblyai_live||!!row.reference_proposal;live.onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから起こし直してください。',true);return}op('/api/retry/live',{segment_index:row.segment_index,language:row.language,auto_adopt:auto.checked})};c.querySelector('.live-note').textContent=project.providers.assemblyai_live?`送信先: AssemblyAI / ${project.providers.assemblyai_model}。送信内容: この発言区間の音声。目的: 再文字起こし。送信はこのボタンを押した時だけ開始します。`:project.providers.assemblyai_setup;const candidates=c.querySelector('.candidates');row.retry_candidates.forEach(x=>{const d=document.createElement('div');d.className='candidate';d.dataset.candidateId=x.id;const model=x.model?` / ${x.model}`:'';const range=x.range?` / ${time(x.range.start)}–${time(x.range.end)}`:'';d.innerHTML=`<strong>${x.provider}${model}・${x.language}${range}</strong><p></p>`;d.querySelector('p').textContent=x.text;if(x.status==='candidate'){const b=document.createElement('button');b.textContent='本文と単語時刻を採用';b.disabled=!!x.adoption_error;b.onclick=async()=>{if(drafts.size){note('未保存の原文または訳を保存してから採用してください。',true);return}const ok=await op('/api/retry/adopt',{segment_index:row.segment_index,candidate_id:x.id,current_text:original.value});if(!ok){const current=[...document.querySelectorAll('.candidate')].find(el=>el.dataset.candidateId===x.id);if(current){const message=document.createElement('small');message.textContent=$('#notice').textContent;current.append(message)}}};d.append(b);if(x.adoption_error){const message=document.createElement('small');message.textContent=x.adoption_error;d.append(message)}}else d.append(x.status==='adopted'?' 採用済み':' 現在の区間には採用できません');candidates.append(d)});
 renderReferenceCandidates(c,row);syncOriginalHighlight(c,row,original);renderIsoDetails(c,row);c.addEventListener('pointerenter',()=>{activeSegmentIndex=row.segment_index});c.addEventListener('focusin',()=>{activeSegmentIndex=row.segment_index});const play=c.querySelector('.play');play.disabled=!player;play.onclick=()=>toggleCardPlayback(row);$('#items').append(c)}if(focus){const restored=[...document.querySelectorAll("#items .card")].find(c=>c.dataset.segmentIndex===focus.index)?.querySelector(`textarea.${focus.kind}`);if(restored){restored.focus({preventScroll:true});restored.setSelectionRange(focus.start,focus.end);if(focus.kind==='original')restored.dispatchEvent(new Event('select'))}}requestAnimationFrame(()=>{resizeAllOriginalEditors();window.scrollTo(0,scrollY)}) }
function mediaSourceForRow(row){const iso=row.iso_evidence?.speaker_evidence?.volume?.primary_iso;return iso?`/media/iso/${encodeURIComponent(iso)}`:'/media'}
async function toggleCardPlayback(row,startAt=row.start_seconds){if(!player)return;if(!player.paused){player.pause();return}const source=mediaSourceForRow(row);if(player.getAttribute('src')!==source){player.src=source;player.load()}player.currentTime=startAt;stopAt=row.end_seconds;try{await player.play()}catch(error){note(`音声を再生できません: ${error.message}`,true)}}
async function init(){await load();if(project.has_media){player=project.has_video?$('#video'):$('#audio');player.src='/media';player.hidden=false;player.ontimeupdate=()=>{if(stopAt!==null&&player.currentTime>=stopAt){player.pause();stopAt=null}updateAlignmentPosition()};player.onpause=updateAlignmentPosition;player.onplay=updateAlignmentPosition;player.onseeked=updateAlignmentPosition}render()}
function initialAlignmentTokens(row){
 if(row.text===row.timed_text&&row.word_boundaries?.length===row.timing_words.length-1){const points=[0,...row.word_boundaries,row.text.length];const tokens=points.slice(0,-1).map((start,i)=>row.text.slice(start,points[i+1]));if(tokens.every(x=>x.trim())&&tokens.join('')===row.text)return tokens}
 if(/\s/.test(row.text))return [...row.text.matchAll(/\s*\S+/g)].map(x=>x[0]);
 if(row.language==='ja-jp'&&Intl.Segmenter)return [...new Intl.Segmenter('ja',{granularity:'word'}).segment(row.text)].map(x=>x.segment);
 return [row.text]
}
function buildAlignmentBoundaries(){
 if(!alignmentRow)return;
 const error=$('#alignment-error');error.textContent='';
 const tokens=$('#alignment-tokens').value.split('\n').map(x=>x.replace(/\r/g,''));
 if(tokens.some(x=>!x.trim())||tokens.join('')!==alignmentRow.text){error.textContent='単語を順番につないだ文字を、現在の本文と完全に一致させてください。空白も確認してください。';return}
 alignmentTokens=tokens;
 const box=$('#alignment-boundaries');box.replaceChildren();
 const old=alignmentRow.timing_words;
 const originalTokens=initialAlignmentTokens(alignmentRow);
 const existing=alignmentRow.text===alignmentRow.timed_text&&old.length===tokens.length&&tokens.every((text,i)=>text===originalTokens[i]);
 alignmentOriginalZeroWords=new Set(existing?old.flatMap((word,i)=>word.start===word.end?[i]:[]):[]);
 tokens.forEach((token,i)=>{
  const row=document.createElement('div');row.className='alignment-word';
  const caption=document.createElement('span');caption.className='alignment-token';
  const tokenText=document.createElement('span');tokenText.textContent=token.trim();
  const preview=document.createElement('button');preview.type='button';preview.textContent='▶ 試聴';preview.disabled=!player;
  preview.title='入力中の開始時刻ちょうどから終了時刻まで再生します';
  preview.setAttribute('aria-label',`${i+1}語目「${token.trim()}」を試聴`);
  preview.onclick=()=>playAlignmentWord(row);
  caption.append(tokenText,preview);row.append(caption);
  for(const edge of ['start','end']){
   const label=document.createElement('label');label.textContent=edge==='start'?'開始':'終了';
   const input=document.createElement('input');input.type='number';input.min=alignmentRow.start_seconds;input.max=alignmentDraftEnd;input.step='0.1';input.dataset.edge=edge;
   input.setAttribute('aria-label',`${token.trim()} ${edge==='start'?'開始':'終了'} 秒`);
   if(existing)input.value=Number(old[i][edge]).toFixed(3);
   else if(edge==='start'&&i===0&&old.length)input.value=Number(old[0].start).toFixed(3);
   else if(edge==='end'&&i===tokens.length-1&&old.length)input.value=Number(old[old.length-1].end).toFixed(3);
   const capture=document.createElement('button');capture.type='button';capture.textContent='再生位置';
   capture.onclick=()=>{if(!player)return;player.pause();const position=player.currentTime;if(position<alignmentRow.start_seconds||position>alignmentDraftEnd){error.textContent='この発話区間内で再生位置を合わせてください。';return}input.value=position.toFixed(3);syncEditedWordBoundary(i,edge);updateAlignmentPosition()};
   input.onchange=()=>syncEditedWordBoundary(i,edge);
   input.onblur=()=>syncEditedWordBoundary(i,edge);
   input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();syncEditedWordBoundary(i,edge);return}if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();const value=Number(input.value);if(Number.isFinite(value)&&input.value!==''){input.value=(Math.round((value+(event.key==='ArrowUp'?-.1:.1))*1000)/1000).toFixed(3);syncEditedWordBoundary(i,edge)}}};
   label.append(input,capture);row.append(label)
  }
  const move=document.createElement('span');move.className='alignment-word-move';
  for(const [delta,label] of [[-.1,'▲ 0.1秒早める'],[.1,'▼ 0.1秒遅らせる']]){
   const button=document.createElement('button');button.type='button';button.textContent=label;
   button.setAttribute('aria-label',`${i+1}語目「${token.trim()}」を${delta<0?'0.1秒早める':'0.1秒遅らせる'}`);
   button.onclick=()=>moveAlignmentWord(i,delta);move.append(button)
  }
  row.append(move);
  box.append(row)
 })
}
function alignmentWordInputs(){return [...document.querySelectorAll('#alignment-boundaries .alignment-word')].map(row=>({
 row,startInput:row.querySelector('input[data-edge="start"]'),endInput:row.querySelector('input[data-edge="end"]')
}))}
function alignmentWordValues(){return alignmentWordInputs().map(({startInput,endInput})=>({
 start:startInput.value===''?NaN:Number(startInput.value),end:endInput.value===''?NaN:Number(endInput.value)
}))}
function wordTimingProblem(values,endSeconds=alignmentDraftEnd){
 for(let i=0;i<values.length;i++){
  const {start,end}=values[i],name=`${i+1}語目「${alignmentTokens[i].trim()}」`;
  if(!Number.isFinite(start)||!Number.isFinite(end))return {index:i,edge:!Number.isFinite(start)?'start':'end',message:`${name}の${!Number.isFinite(start)?'開始':'終了'}時刻が未入力です。`};
  if(start<alignmentRow.start_seconds||start>endSeconds)return {index:i,edge:'start',message:`${name}の開始 ${preciseTime(start)} が発話区間 ${preciseTime(alignmentRow.start_seconds)}–${preciseTime(endSeconds)} の外です。`};
  if(end<alignmentRow.start_seconds||end>endSeconds)return {index:i,edge:'end',message:`${name}の終了 ${preciseTime(end)} が発話区間 ${preciseTime(alignmentRow.start_seconds)}–${preciseTime(endSeconds)} の外です。`};
  if(end<start||(end===start&&!alignmentOriginalZeroWords.has(i)))return {index:i,edge:'end',message:end===start?
   `${name}の開始と終了がどちらも ${preciseTime(start)} です。新たに0秒の単語は作れません。開始か終了を個別に修正してください。`:
   `${name}の終了 ${preciseTime(end)} は開始 ${preciseTime(start)} より後にしてください。`};
  if(i>0&&start<values[i-1].end)return {index:i,edge:'start',message:`${i}語目「${alignmentTokens[i-1].trim()}」の終了 ${preciseTime(values[i-1].end)} と${name}の開始 ${preciseTime(start)} が重なっています。`};
 }
 return null;
}
function showWordTimingProblem(problem,{focus=true}={}){
 const error=$('#alignment-error');error.textContent=problem.message;
 const rows=alignmentWordInputs();rows.forEach(({row})=>{row.classList.remove('timing-error');row.querySelector('.timing-error-detail')?.remove()});
 const target=rows[problem.index];if(target){
  target.row.classList.add('timing-error');
  const detail=document.createElement('span');detail.className='timing-error-detail';detail.textContent=problem.message;target.row.append(detail);
  if(focus){target.row.scrollIntoView({block:'nearest'});(problem.edge==='end'?target.endInput:target.startInput).focus()}
 }
}
function applyWordTimingValues(values){
 alignmentWordInputs().forEach(({row,startInput,endInput},i)=>{startInput.value=values[i].start.toFixed(3);endInput.value=values[i].end.toFixed(3);row.classList.remove('timing-error');row.querySelector('.timing-error-detail')?.remove()});
 $('#alignment-error').textContent='';
 updateAlignmentRepairHint();
}
function alignmentRepairPlan(){
 if(!alignmentRow)return null;
 const original=alignmentWordValues();
 if(!original.length||original.some(word=>!Number.isFinite(word.start)||!Number.isFinite(word.end)))return null;
 const lower=alignmentRow.start_seconds,upper=alignmentDraftEnd;
 const values=original.map(word=>({...word})),changes=[];
 for(let i=0;i<values.length;i++){
  const word=values[i];
  if(word.start>=lower&&word.end<=upper)continue;
  if(word.end-word.start>upper-lower)return null;
  const delta=word.start<lower?lower-word.start:upper-word.end;
  const start=Math.round((word.start+delta)*1000)/1000;
  const end=Math.round((word.end+delta)*1000)/1000;
  changes.push({index:i,before:{...word},after:{start,end}});
  values[i]={start,end};
 }
 if(!changes.length||wordTimingProblem(values))return null;
 return {values,changes};
}
function updateAlignmentRepairHint(){
 const box=$('#alignment-repair');if(!box)return;
 const plan=alignmentRepairPlan();box.hidden=!plan;
 if(!plan)return;
 const first=plan.changes[0],name=`${first.index+1}語目「${alignmentTokens[first.index].trim()}」`;
 $('#alignment-repair-description').textContent=`${name} ${preciseTime(first.before.start)}–${preciseTime(first.before.end)} → ${preciseTime(first.after.start)}–${preciseTime(first.after.end)}${plan.changes.length>1?`、ほか${plan.changes.length-1}語`:''}。単語の長さを保つ仮移動です。音声を確認してから保存してください。`;
}
function repairAlignmentWords(){
 const plan=alignmentRepairPlan();if(!plan)return;
 applyWordTimingValues(plan.values);
 $('#alignment-repair-status').textContent='仮移動しました。試聴後に「確認した時刻を保存」を押してください。';
}
function updateAlignmentEnvelopeHint(){
 if(!alignmentRow)return;
 const old=alignmentRow.timing_words;
 const changed=Math.abs(alignmentDraftEnd-alignmentRow.end_seconds)>.0005;
 const next=project.items.filter(item=>item.segment_index!==alignmentRow.segment_index&&item.speaker_name===alignmentRow.speaker_name&&item.start_seconds>alignmentRow.start_seconds).sort((a,b)=>a.start_seconds-b.start_seconds)[0];
 const nextHint=next&&alignmentDraftEnd>next.start_seconds?` ／ 保存時に同じ話者の次の発話の開始を ${preciseTime(alignmentDraftEnd)} へ変更（単語時刻は要確認）`:'';
 const crossTalk=project.items.some(item=>item.segment_index!==alignmentRow.segment_index&&item.speaker_name!==alignmentRow.speaker_name&&item.start_seconds<alignmentDraftEnd&&item.end_seconds>alignmentRow.start_seconds);
 const crossTalkHint=crossTalk?' ／ 別話者との重なりは維持':'';
 const zeroHint=alignmentOriginalZeroWords.size?` ／ 元データの0秒語 ${alignmentOriginalZeroWords.size}件（位置移動可・単独試聴不可）`:'';
 $('#alignment-hint').textContent=`対象 ${time(alignmentRow.start_seconds)}–${time(alignmentDraftEnd)}${changed?'（終了を保存時に更新）':''}`+
  (old.length?` ／ 現在の単語時刻 ${time(old[0].start)}–${time(old[old.length-1].end)}`:'')+zeroHint+nextHint+crossTalkHint;
 for(const input of document.querySelectorAll('#alignment-boundaries input'))input.max=alignmentDraftEnd;
 updateAlignmentRepairHint();
}
function syncEditedWordBoundary(index,edge){
 const values=alignmentWordValues(),current=values[index];if(!current||!Number.isFinite(current[edge]))return;
 if(edge==='start'&&index>0&&current.start<values[index-1].end)values[index-1].end=current.start;
 if(edge==='end'&&index<values.length-1&&current.end>values[index+1].start)values[index+1].start=current.end;
 const problem=wordTimingProblem(values);if(problem){showWordTimingProblem(problem,{focus:false});updateAlignmentRepairHint();return}
 applyWordTimingValues(values);
}
function moveAlignmentWord(index,delta){
 const values=alignmentWordValues();
 const currentProblem=wordTimingProblem(values);if(currentProblem){showWordTimingProblem(currentProblem);return}
 const mode=document.querySelector('input[name="alignment-move-mode"]:checked')?.value||'following';
 for(let i=index;i<(mode==='following'?values.length:index+1);i++){
  values[i].start=Math.round((values[i].start+delta)*1000)/1000;
  values[i].end=Math.round((values[i].end+delta)*1000)/1000;
 }
 if(index>0&&values[index].start<values[index-1].end)values[index-1].end=values[index].start;
 if(mode==='single'&&index<values.length-1&&values[index].end>values[index+1].start)values[index+1].start=values[index].end;
 const movedEnd=mode==='following'?Math.round((alignmentDraftEnd+delta)*1000)/1000:
  Math.max(alignmentDraftEnd,values[values.length-1].end);
 const movedProblem=wordTimingProblem(values,movedEnd);if(movedProblem){showWordTimingProblem(movedProblem);return}
 alignmentDraftEnd=movedEnd;applyWordTimingValues(values);updateAlignmentEnvelopeHint();
}
async function playAlignmentWord(wordRow){
 if(!player||!alignmentRow)return;
 const [startInput,endInput]=wordRow.querySelectorAll('input');
 const start=startInput.value===''?NaN:Number(startInput.value),end=endInput.value===''?NaN:Number(endInput.value);
 const error=$('#alignment-error');error.textContent='';
 if(!Number.isFinite(start)||!Number.isFinite(end)||end<start||start<alignmentRow.start_seconds||end>alignmentDraftEnd){error.textContent='この単語の開始・終了時刻を発話区間内に入力してください。';return}
 if(end===start){error.textContent='元データで長さ0秒の単語は単独試聴できません。この区間を再生して位置を確認してください。';return}
 player.pause();const source=mediaSourceForRow(alignmentRow);
 if(player.getAttribute('src')!==source){player.src=source;player.load()}
 player.currentTime=start;stopAt=end;
 try{await player.play()}catch(playError){error.textContent=`単語を再生できません: ${playError.message}`}
}
function alignmentShiftPlan(){
 if(!alignmentRow)return {error:'対象の発話がありません'};
 const value=$('#alignment-utterance-start').value;
 const start=value===''?NaN:Number(value);
 if(!Number.isFinite(start)||start<0)return {error:'新しい開始時刻を0秒以上で入力してください'};
 const delta=start-alignmentRow.start_seconds,end=alignmentRow.end_seconds;
 if(end<=start)return {error:'開始は現在の終了時刻より前にしてください'};
 const previous=project.items[alignmentRow.segment_index-1];
 const previousToTrim=delta<0&&previous?.end_seconds>start?previous:null;
 if(previousToTrim&&previousToTrim.start_seconds>=start)return {error:'前の区間の開始より後の時刻にしてください'};
 return {start,end,delta,previousToTrim,
  previousNeedsReview:!!previousToTrim?.timing_words.some(word=>word.end>start),
  currentNeedsReview:alignmentRow.timing_words.some(word=>word.start<start)};
}
function updateAlignmentShiftHint(){
 const hint=$('#alignment-shift-hint'),plan=alignmentShiftPlan();
 if(plan.error){hint.textContent=plan.error;return}
 const details=[`終了 ${preciseTime(plan.end)}を維持`,`開始 ${plan.delta>=0?'+':''}${plan.delta.toFixed(3)}秒`];
 if(plan.previousToTrim)details.push(`前の区間の終了を${preciseTime(plan.start)}へ短縮`);
 if(plan.previousNeedsReview)details.push('前の区間の単語時刻は要確認');
 if(plan.currentNeedsReview)details.push('この区間の単語時刻は要確認');
 hint.textContent=details.join(' ／ ');
}
function alignmentEndPlan(){
 if(!alignmentRow)return {error:'対象の発話がありません'};
 const value=$('#alignment-utterance-end').value,end=value===''?NaN:Math.round(Number(value)*1000)/1000;
 if(!Number.isFinite(end)||end<=alignmentRow.start_seconds)return {error:'新しい終了を発話の開始より後の秒数で入力してください'};
 const lastWordEnd=Math.max(alignmentRow.start_seconds,...alignmentRow.timing_words.map(word=>word.end));
 if(end<lastWordEnd)return {error:`単語時刻を維持するには ${preciseTime(lastWordEnd)} 以降を指定してください`};
 return {end,lastWordEnd};
}
function updateAlignmentEndHint(){
 const plan=alignmentEndPlan(),hint=$('#alignment-end-hint');
 if(plan.error){hint.textContent=plan.error;return}
 const next=project.items.filter(item=>item.segment_index!==alignmentRow.segment_index&&item.speaker_name===alignmentRow.speaker_name&&item.start_seconds>alignmentRow.start_seconds).sort((a,b)=>a.start_seconds-b.start_seconds)[0];
 const details=[`現在 ${preciseTime(alignmentRow.end_seconds)} → 新しい終了 ${preciseTime(plan.end)}`,`最後の単語 ${preciseTime(plan.lastWordEnd)} まで維持`];
 if(next&&plan.end>next.start_seconds)details.push(`同じ話者の次の発話の開始を ${preciseTime(plan.end)} へ変更`);
 hint.textContent=details.join(' ／ ');
}
function nudgeAlignmentEnd(delta){
 const input=$('#alignment-utterance-end'),current=Number(input.value);
 const base=input.value!==''&&Number.isFinite(current)?current:alignmentRow?.end_seconds;
 if(!Number.isFinite(base))return;
 input.value=(Math.round((base+delta)*1000)/1000).toFixed(3);
 updateAlignmentEndHint();
}
async function previewAlignmentEnd(){
 const plan=alignmentEndPlan(),error=$('#alignment-error');error.textContent='';
 if(plan.error){error.textContent=plan.error;return}
 if(!player){error.textContent='音声が接続されていません';return}
 player.pause();const source=mediaSourceForRow(alignmentRow);
 if(player.getAttribute('src')!==source||player.error){player.src=source;player.load()}
 player.currentTime=Math.max(alignmentRow.start_seconds,plan.end-5);stopAt=plan.end;
 try{await player.play();updateAlignmentPosition()}catch(playError){error.textContent=`発話を再生できません: ${playError.message}`}
}
async function saveAlignmentEnd(){
 const plan=alignmentEndPlan(),error=$('#alignment-error');error.textContent='';
 if(plan.error){error.textContent=plan.error;return}
 if(Math.abs(plan.end-alignmentRow.end_seconds)<.0005){error.textContent='終了時刻が変わっていません';return}
 const index=alignmentRow.segment_index;
 const ok=await op('/api/align/end',{segment_index:index,current_text:alignmentRow.text,
  expected_fingerprint:alignmentRow.retry_fingerprint,new_end_seconds:plan.end});
 if(ok){alignmentRow=project.items[index];alignmentDraftEnd=alignmentRow.end_seconds;
  $('#alignment-utterance-end').value=Number(alignmentRow.end_seconds).toFixed(3);
  updateAlignmentEnvelopeHint();updateAlignmentShiftHint();updateAlignmentEndHint();
  const problem=wordTimingProblem(alignmentWordValues());if(problem)showWordTimingProblem(problem)
 }else error.textContent=$('#notice').textContent;
}
async function previewAlignmentShift(){
 const plan=alignmentShiftPlan(),error=$('#alignment-error');error.textContent='';
 if(plan.error){error.textContent=plan.error;return}
 if(!player){error.textContent='音声が接続されていません';return}
 if(shiftPreviewPlaying&&!player.paused){player.pause();shiftPreviewPlaying=false;updateAlignmentPosition();return}
 player.pause();const source=mediaSourceForRow(alignmentRow);
 if(player.getAttribute('src')!==source){player.src=source;player.load()}
 player.currentTime=plan.start;stopAt=plan.end;shiftPreviewPlaying=true;
 try{await player.play();updateAlignmentPosition()}catch(playError){shiftPreviewPlaying=false;updateAlignmentPosition();error.textContent=`発話を再生できません: ${playError.message}`}
}
async function saveAlignmentShift(){
 const plan=alignmentShiftPlan(),error=$('#alignment-error');error.textContent='';
 if(plan.error){error.textContent=plan.error;return}
 if(Math.abs(plan.delta)<.0005){error.textContent='開始時刻が変わっていません';return}
 const ok=await op('/api/align/start',{segment_index:alignmentRow.segment_index,current_text:alignmentRow.text,
  expected_fingerprint:alignmentRow.retry_fingerprint,new_start_seconds:plan.start});
 if(ok){
  alignmentRow=project.items[alignmentRow.segment_index];
  updateAlignmentEnvelopeHint();
  $('#alignment-utterance-start').value=Number(alignmentRow.start_seconds).toFixed(3);
  for(const input of document.querySelectorAll('#alignment-boundaries input'))input.min=alignmentRow.start_seconds;
  updateAlignmentShiftHint();updateAlignmentPosition();
 }else error.textContent=$('#notice').textContent;
}
function openAlignmentDialog(row){if(drafts.has(`${row.segment_index}:original`)||row.reference_proposal){note('この発言の本文を保存してから時刻を調整してください。',true);return}alignmentRow=row;alignmentDraftEnd=row.end_seconds;alignmentTokens=[];shiftPreviewPlaying=false;$('#alignment-repair-status').textContent='';document.querySelector('input[name="alignment-move-mode"][value="following"]').checked=true;$('#alignment-current').textContent=row.text;$('#alignment-utterance-start').value=Number(row.start_seconds).toFixed(3);$('#alignment-utterance-end').value=Number(row.end_seconds).toFixed(3);updateAlignmentShiftHint();updateAlignmentEndHint();$('#alignment-tokens').value=initialAlignmentTokens(row).join('\n');buildAlignmentBoundaries();updateAlignmentEnvelopeHint();$('#alignment-dialog').showModal();if(player){player.currentTime=row.start_seconds;updateAlignmentPosition()}const problem=wordTimingProblem(alignmentWordValues());if(problem)showWordTimingProblem(problem)}
async function saveAlignment(){
 if(!alignmentRow)return;const error=$('#alignment-error');error.textContent='';
 const tokens=$('#alignment-tokens').value.split('\n').map(x=>x.replace(/\r/g,''));
 if(tokens.length!==alignmentTokens.length||tokens.some((x,i)=>x!==alignmentTokens[i])){error.textContent='単語を変更した後は「単語を反映して時刻欄を作る」を押してください。';return}
 const values=alignmentWordValues();
 if(values.length!==tokens.length){error.textContent='単語数が合いません。「単語を反映して時刻欄を作る」を押してください。';return}
 const problem=wordTimingProblem(values);if(problem){showWordTimingProblem(problem);return}
 const words=tokens.map((text,i)=>({text,start:values[i].start,end:values[i].end}));
 const ok=await op('/api/align/manual',{segment_index:alignmentRow.segment_index,current_text:alignmentRow.text,expected_fingerprint:alignmentRow.retry_fingerprint,words,new_end_seconds:alignmentDraftEnd});
 if(ok){$('#alignment-dialog').close();alignmentRow=null}else error.textContent=$('#notice').textContent
}
function nudgeAlignmentStart(delta){
 const input=$('#alignment-utterance-start'),current=Number(input.value);
 const base=input.value!==''&&Number.isFinite(current)?current:alignmentRow?.start_seconds;
 if(!Number.isFinite(base))return;
 input.value=Math.max(0,Math.round((base+delta)*1000)/1000).toFixed(3);
 if(shiftPreviewPlaying&&player&&!player.paused)player.pause();
 updateAlignmentShiftHint();updateAlignmentPosition();
}
$('#alignment-rebuild').onclick=buildAlignmentBoundaries;$('#alignment-repair-button').onclick=repairAlignmentWords;$('#alignment-save').onclick=saveAlignment;
$('#alignment-utterance-start').oninput=()=>{if(shiftPreviewPlaying&&player&&!player.paused)player.pause();updateAlignmentShiftHint()};
$('#alignment-utterance-start').onkeydown=event=>{if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();nudgeAlignmentStart(event.key==='ArrowUp'?-.1:.1)}};
$('#alignment-step-up').onclick=()=>nudgeAlignmentStart(-.1);$('#alignment-step-down').onclick=()=>nudgeAlignmentStart(.1);
$('#alignment-shift-preview').onclick=previewAlignmentShift;$('#alignment-shift-save').onclick=saveAlignmentShift;$('#alignment-play').onclick=async()=>{if(!player||!alignmentRow)return;if(!player.paused){player.pause();return}if(player.currentTime>=alignmentDraftEnd)player.currentTime=alignmentRow.start_seconds;stopAt=alignmentDraftEnd;await player.play()};for(const [id,delta] of [['#alignment-back',-.1],['#alignment-forward',.1]])$(id).onclick=()=>{if(!player||!alignmentRow)return;player.pause();player.currentTime=Math.max(alignmentRow.start_seconds,Math.min(alignmentDraftEnd,player.currentTime+delta));updateAlignmentPosition()};$('#alignment-dialog').addEventListener('close',()=>{if(player)player.pause();shiftPreviewPlaying=false;alignmentRow=null;alignmentDraftEnd=null});
$('#alignment-utterance-end').oninput=updateAlignmentEndHint;
$('#alignment-utterance-end').onkeydown=event=>{if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();nudgeAlignmentEnd(event.key==='ArrowUp'?.1:-.1)}};
$('#alignment-end-up').onclick=()=>nudgeAlignmentEnd(.1);$('#alignment-end-down').onclick=()=>nudgeAlignmentEnd(-.1);
$('#alignment-end-preview').onclick=previewAlignmentEnd;$('#alignment-end-save').onclick=saveAlignmentEnd;
function addSpeakerRow(speaker=null){const row=document.createElement('label');row.className='speaker-row';row.dataset.key=speaker?.key||'';const label=document.createElement('span');label.textContent=speaker?'既存の話者':'新しい話者';const input=document.createElement('input');input.value=speaker?.name||'';input.placeholder='話者名';row.append(label,input);$('#speaker-rows').append(row);return row}
function openSpeakerDialog(segmentIndex=null){speakerDialogSegment=segmentIndex;$('#speaker-rows').replaceChildren();project.speakers.forEach(addSpeakerRow);$('#speaker-dialog-error').textContent='';$('#speaker-assign').checked=false;$('#speaker-assign-wrap').hidden=segmentIndex===null;$('#speaker-dialog').showModal()}
function openMismatchDialog(){const box=$('#mismatch-rows');box.replaceChildren();const rows=project.items.filter(x=>x.language_mismatch);for(const row of rows){const label=document.createElement('label');label.className='mismatch-row';const duplicate=row.retry_candidates.some(x=>x.status==='candidate'&&x.fingerprint===row.retry_fingerprint),active=!duplicate,selected=active&&row.text_language_suggestion.confidence==='high';label.innerHTML=`<input type="checkbox" ${selected?'checked':''} ${active?'':'disabled'} data-index="${row.segment_index}"><span>${time(row.start_seconds)}</span><span>${project.language_options[row.text_language_suggestion.language]}・${row.text_language_suggestion.confidence==='high'?'確信あり':'要確認'}</span><span>${project.language_options[row.language]}</span><span class="original"></span>`;label.querySelector('.original').textContent=row.text;label.title=duplicate?'現在の内容に対する候補があります。比較して採用するか、個別に再取得してください。':row.text_language_suggestion.reason;box.append(label)}if(!rows.length)box.textContent='本文から判定できる不一致候補はありません。短い語句や判定不能な本文は候補外です。';$('#batch-progress').textContent='';$('#batch-start').disabled=!project.providers.assemblyai_live||!rows.length;$('#mismatch-dialog').showModal()}
async function startMismatchBatch(){if(drafts.size){$('#batch-progress').textContent='未保存の原文または訳をすべて保存してください。';return}const selected=[...document.querySelectorAll('#mismatch-rows input:checked')].map(x=>{const row=project.items[Number(x.dataset.index)];return {index:row.segment_index,fingerprint:row.retry_fingerprint,start:row.start_seconds,language:row.language,input:x}});if(!selected.length){$('#batch-progress').textContent='対象を選択してください。';return}$('#batch-start').disabled=true;let done=0,failed=0,skipped=0;const failures=[];for(const target of selected){const current=project.items[target.index];if(!current||current.retry_fingerprint!==target.fingerprint){skipped++;failures.push(`${time(target.start)}: 発言が変更されたため未送信`);continue}$('#batch-progress').textContent=`${done+failed+skipped}/${selected.length}件完了・${time(target.start)}を処理中`;try{const response=await fetch('/api/retry/live',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':project.token},body:JSON.stringify({revision:project.revision,segment_index:target.index,language:target.language,expected_fingerprint:target.fingerprint,auto_adopt:$('#batch-auto-adopt').checked})});const value=await response.json();if(!response.ok)throw Error(value.error);done++;target.input.checked=false;target.input.disabled=true}catch(error){failed++;failures.push(`${time(target.start)}: ${error.message}`)}await load()}$('#batch-progress').textContent=`完了 ${done}件・失敗 ${failed}件・変更のため未送信 ${skipped}件。${failures.join(' ／ ')}`;$('#batch-start').disabled=false;render()}
let translationSelection=[],translationCandidate=null,translationDirty=false;
function translationGroups(){return project.translation_groups||[]}
function translatable(row){return row&&row.language!=='??-??'&&row.language!=='ja-jp'&&!(row.translation.text?.trim()&&(row.translation.provider==='manual'||row.translation.status==='ready'))}
function translationRangeValid(indices){
 if(!indices.length||indices.length>20)return false;
 const rows=indices.map(i=>project.items[i]),first=rows[0];if(!rows.every((row,j)=>translatable(row)&&row.speaker_name===first.speaker_name&&row.language===first.language&&(!j||indices[j]===indices[j-1]+1&&row.start_seconds-rows[j-1].end_seconds<=8)))return false;
 return rows.at(-1).end_seconds-first.start_seconds<=120&&rows.reduce((n,row)=>n+row.text.length,0)<=1800;
}
function translationCandidateFor(indices){return (project.translation_group_candidates||[]).findLast(candidate=>JSON.stringify(candidate.indices)===JSON.stringify(indices)&&candidate.source.every(source=>project.items[source.index]?.text===source.text))||null}
function translationContents(){return {full_translation:$('#translation-full').value,segment_translations:[...document.querySelectorAll('#translation-segments textarea')].map(input=>input.value)}}
function renderTranslationDialog(preferred=null){
 const groups=translationGroups(),unknown=project.items.filter(row=>row.language==='??-??').length;
 if(preferred&&translationRangeValid(preferred))translationSelection=[...preferred];
 else if(!translationRangeValid(translationSelection))translationSelection=[...(groups[0]?.indices||[])];
 $('#translation-plan').textContent=`未訳 ${project.items.filter(translatable).length}件・翻訳単位 ${groups.length}件・言語未指定 ${unknown}件を保留。確定済みの訳は保持します。`;
 const selector=$('#translation-group-select');selector.replaceChildren();groups.forEach((group,j)=>{const first=project.items[group.indices[0]],last=project.items[group.indices.at(-1)];selector.add(new Option(`${j+1}. ${time(first.start_seconds)}–${time(last.end_seconds)}・${first.speaker_name}・${group.indices.length}区間`,j))});
 const match=groups.findIndex(group=>JSON.stringify(group.indices)===JSON.stringify(translationSelection));if(match>=0)selector.value=String(match);else selector.selectedIndex=-1;
 const source=$('#translation-source'),segments=$('#translation-segments');source.replaceChildren();segments.replaceChildren();
 translationCandidate=translationCandidateFor(translationSelection);$('#translation-full').value=translationCandidate?.full_translation||'';
 for(const index of translationSelection){const row=project.items[index];const line=document.createElement('p');line.textContent=`${time(row.start_seconds)}–${time(row.end_seconds)}　${row.text}`;source.append(line);const label=document.createElement('label');label.className='translation-segment';const title=document.createElement('span');title.textContent=`${time(row.start_seconds)}　${row.text}`;const input=document.createElement('textarea');input.rows=2;input.value=translationCandidate?.segment_translations[translationSelection.indexOf(index)]||'';input.addEventListener('input',()=>{translationDirty=true;translationCandidate=null;$('#translation-discard').disabled=false});label.append(title,input);segments.append(label)}
 translationDirty=false;$('#translation-full').oninput=()=>{translationDirty=true;translationCandidate=null;$('#translation-discard').disabled=false};
 $('#translation-include-prev').disabled=!translationRangeValid([translationSelection[0]-1,...translationSelection]);
 $('#translation-include-next').disabled=!translationRangeValid([...translationSelection,translationSelection.at(-1)+1]);
 $('#translation-drop-first').disabled=translationSelection.length<2;$('#translation-drop-last').disabled=translationSelection.length<2;
 $('#translation-generate').disabled=!project.providers.translation||!translationSelection.length;
 $('#translation-save').disabled=!translationSelection.length;$('#translation-discard').disabled=true;
 $('#translation-progress').textContent=translationSelection.length?(translationCandidate?`保存済みの下訳を表示中。確認・修正して保存してください。${translationCandidate.warnings?.length?' 要確認: '+translationCandidate.warnings.join(' ／ '):''}`:project.providers.translation?'下訳を生成するか、手入力して保存できます。':'翻訳の接続設定がありません。訳を手入力して保存できます。'):'未訳の外国語区間はありません。';
}
function translationCanLeave(){if(!translationDirty)return true;$('#translation-progress').textContent='入力中の訳があります。先に保存するか「入力を破棄」を押してください。';return false}
function openTranslationDialog(preferred=null){if(drafts.size){note('未保存の本文または訳があります。先にすべて保存してから翻訳してください。',true);return}translationSelection=[];renderTranslationDialog(preferred);$('#translation-dialog').showModal()}
async function translationGenerate(){if(drafts.size){$('#translation-progress').textContent='未保存の本文または訳をすべて保存してください。';return}if(!translationCanLeave())return;const indices=[...translationSelection];$('#translation-progress').textContent='まとまりの下訳を生成中…';const ok=await op('/api/translation/group/generate',{indices});if(ok){renderTranslationDialog(indices);$('#translation-progress').textContent=`下訳ができました。全体訳と各字幕の割当を確認してください。${translationCandidate?.warnings?.length?' 要確認: '+translationCandidate.warnings.join(' ／ '):''}`}else $('#translation-progress').textContent=$('#notice').textContent}
async function translationSave(){if(drafts.size){$('#translation-progress').textContent='未保存の本文または訳をすべて保存してください。';return}const indices=[...translationSelection],content=translationContents();if(!content.full_translation.trim()||content.segment_translations.some(value=>!value.trim())){$('#translation-progress').textContent='全体訳と各区間の訳を入力してください。';return}$('#translation-progress').textContent='確認した訳を保存中…';const ok=await op('/api/translation/group/apply',{indices,candidate_id:translationCandidate?.id,...content});if(ok){translationDirty=false;translationSelection=[];renderTranslationDialog();$('#translation-progress').textContent='保存しました。次の翻訳単位を選べます。'}else $('#translation-progress').textContent=$('#notice').textContent}
$('#translate-missing').onclick=()=>openTranslationDialog();
$('#translation-group-select').onchange=e=>{if(!translationCanLeave()){const previous=translationGroups().findIndex(group=>JSON.stringify(group.indices)===JSON.stringify(translationSelection));e.target.value=previous>=0?String(previous):'';return}const group=translationGroups()[Number(e.target.value)];if(group)renderTranslationDialog(group.indices)};
for(const [id,change] of [['#translation-include-prev',indices=>[indices[0]-1,...indices]],['#translation-drop-first',indices=>indices.slice(1)],['#translation-drop-last',indices=>indices.slice(0,-1)],['#translation-include-next',indices=>[...indices,indices.at(-1)+1]]])$(id).onclick=()=>{if(!translationCanLeave())return;const next=change(translationSelection);if(translationRangeValid(next))renderTranslationDialog(next)};
$('#translation-generate').onclick=translationGenerate;$('#translation-save').onclick=translationSave;$('#translation-discard').onclick=()=>renderTranslationDialog([...translationSelection]);$('#translation-close').onclick=()=>{if(translationCanLeave())$('#translation-dialog').close()};
$('#translation-dialog').addEventListener('cancel',e=>{if(!translationCanLeave())e.preventDefault()});
$('#speaker-add-row').onclick=()=>{const row=addSpeakerRow();row.querySelector('input').focus()};
$('#speaker-save').onclick=async()=>{const rows=[...document.querySelectorAll('.speaker-row')];const existing=rows.filter(x=>x.dataset.key).map(x=>({key:x.dataset.key,name:x.querySelector('input').value}));const newNames=rows.filter(x=>!x.dataset.key).map(x=>x.querySelector('input').value);const error=$('#speaker-dialog-error');error.textContent='';const names=[...existing.map(x=>x.name.trim()),...newNames.map(x=>x.trim())];if(names.some(x=>!x)){error.textContent='話者名を入力してください。';return}if(new Set(names).size!==names.length){error.textContent='同じ話者名は使用できません。';return}if($('#speaker-assign').checked&&newNames.length!==1){error.textContent='この発言へ割り当てる場合、新しい話者は1人だけ追加してください。';return}const assign=$('#speaker-assign').checked?0:null;$('#speaker-dialog').close();await op('/api/speakers/manage',{existing,new_names:newNames,segment_index:speakerDialogSegment,assign_new_index:assign})};
$('#manage-speakers').onclick=()=>openSpeakerDialog();$('#language-mismatches').onclick=openMismatchDialog;$('#batch-start').onclick=startMismatchBatch;$('#speaker-filter').onchange=render;$('#language-filter').onchange=render;$('#search').oninput=render;$('#candidate-filter').onchange=render;$('#undo').onclick=()=>op('/api/undo');$('#redo').onclick=()=>op('/api/redo');$('#complete').onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから書き出してください。',true);return}op('/api/export')};$('#draft').onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから書き出してください。',true);return}op('/api/export/draft')};window.addEventListener('beforeunload',e=>{if(drafts.size||translationDirty){e.preventDefault();e.returnValue=''}});document.addEventListener('keydown',e=>{if(!e.metaKey||e.altKey||e.ctrlKey||e.key.toLowerCase()!=='z'||document.querySelector('dialog[open]'))return;if(e.target?.closest?.('input,textarea,[contenteditable]'))return;e.preventDefault();if(drafts.size){note('未保存の原文または訳を保存してから履歴を操作してください。',true);return}const button=e.shiftKey?$('#redo'):$('#undo');if(!button.disabled&&!busy)button.click()});document.addEventListener('keydown',e=>{if(!(e.metaKey&&(e.code==='Space'||e.key==='Enter'))||!player)return;e.preventDefault();if(document.querySelector('dialog[open]'))return;const focused=document.activeElement?.closest?.('.card'),hovered=document.querySelector('#items .card:hover');const selectedIndex=(focused||hovered)?.dataset.segmentIndex??activeSegmentIndex;const index=selectedIndex===null?null:Number(selectedIndex);const row=Number.isInteger(index)&&index>=0?project.items[index]:null;if(row){const editor=document.activeElement;const caret=editor?.matches?.('textarea.original')&&editor.closest('.card')?.dataset.segmentIndex===String(index)?playbackTimeFromCaret(row,editor.selectionStart,editor.value):row.start_seconds;toggleCardPlayback(row,caret)}else if(player.paused)player.play().catch(error=>note(`音声を再生できません: ${error.message}`,true));else player.pause()});init().catch(e=>note(e.message,true));

// The setup and reference panels stay mounted while utterance cards are redrawn.
// Their text inputs therefore survive project polling and unrelated edits.
let setupHydrated=false,configuredSignature='',workflowPoll=null,lastExport=null,initialCompactApplied=false,srtDirty=false,srtSelected=null,premiereTarget=null,premiereTargetRevision=null,premiereSnapshot=false,premiereFirstImport=false;
const workflowMode=()=>document.querySelector('input[name="workflow-mode"]:checked')?.value||'multilingual';
const workflowEngine=()=>document.querySelector('input[name="workflow-engine"]:checked')?.value||'local-whisper';
const setupSignature=()=>JSON.stringify([workflowEngine(),$('#media-path').value.trim(),$('#initial-language').value]);
function renderWorkflow(){
 const w=project.workflow||{},engines=w.engines||{},stage=w.stage||'review',running=stage==='transcribing'||w.job?.status==='running';if(!initialCompactApplied){initialCompactApplied=true;if(project.items.length){$('#workflow-setup').classList.add('compact');$('#reference-section').classList.add('compact')}}updatePanelToggles();
 const japanese=!!project.japanese_url;
 $('#japanese-route').textContent=japanese?'従来の日本語ツールを起動してから開いてください。今回の環境では「日本語のみを開く.command」で対象案件を選びます。':'既存の日本語確認画面のURLが指定されていません。起動時に --japanese-url を指定するとここから開けます。';
 $('#japanese-route').classList.toggle('error',workflowMode()==='japanese'&&!japanese);if(japanese){const link=document.createElement('a');link.href=project.japanese_url;link.textContent=' 起動後に日本語のみの画面を開く';$('#japanese-route').append(link)}
 $('#multilingual-setup').hidden=workflowMode()==='japanese';
 if(!setupHydrated){
  if(w.media_path)$('#media-path').value=w.media_path;
  if(w.language)$('#initial-language').value=w.language;
  if(w.engine){const option=document.querySelector(`input[name="workflow-engine"][value="${w.engine}"]`);if(option)option.checked=true;}
  if(w.engine&&w.media_path)configuredSignature=setupSignature();
  setupHydrated=true;
 }
 $('#workspace-location').textContent=w.workspace_path?`保存先: ${w.workspace_path}`:'';
 const selectedPath=$('#media-path').value.trim();$('#media-selected').textContent=selectedPath?selectedPath.split('/').pop():'まだ選択していません';$('#media-selected').title=selectedPath;
 $('#media-pick').disabled=running||!!project.items.length;$('#media-path').disabled=running||!!project.items.length;$('#initial-language').disabled=running||!!project.items.length;
 for(const input of document.querySelectorAll('input[name="workflow-engine"]')){const info=engines[input.value];input.disabled=running||!!project.items.length||(!!info&&!info.available);input.closest('label').title=info&&!info.available?info.reason||'利用できません':'';}
 const selected=engines[workflowEngine()];$('#engine-reason').textContent=selected&&!selected.available?selected.reason||'この文字起こし方法は現在利用できません。':'';
 const canStart=!!$('#media-path').value.trim()&&(!selected||selected.available)&&!running&&!project.items.length;
 $('#workflow-configure').disabled=running||!canStart;
 $('#workflow-start').disabled=!canStart||configuredSignature!==setupSignature();
 $('#workflow-status').textContent=({setup:'準備中',transcribing:'文字起こし中',review:'確認中',error:'要確認'})[stage]||stage;
 $('#workflow-progress').textContent=w.job?.error||w.status||'';
 const disclose=w.external_disclosure||{};
 $('#workflow-disclosure').hidden=workflowEngine()!=='assemblyai';
 $('#workflow-disclosure').textContent=`外部送信先: ${disclose.destination||'AssemblyAI API'} ／ 送信するもの: ${disclose.data||'選択した素材音声'} ／ 目的: ${disclose.purpose||'最初の文字起こし'}。設定保存では送信せず、「文字起こしを開始」で送信します。`;
 const missing=project.items.filter(x=>x.language==='??-??'||(x.language!=='ja-jp'&&x.translation.status!=='ready')||x.alignment_status!=='word-timed').length;
 $('#review-status').textContent=project.items.length?`${project.items.length}発言・要確認 ${missing}件`:'発言なし';
 const output=w.output||lastExport||{};const srt=output.srt_status==='exported',json=output.json_status==='exported',applied=output.premiere_status==='applied_verified';
 $('#output-status').textContent=applied?'Premiere反映を確認済み':srt||json?'ファイルを書き出し済み':'未書き出し';
 $('#output-detail').textContent=`作業内容: ${project.revision>0?'保存済み':'初期状態'} ／ SRT: ${srt?'書き出し済み':'未書き出し'} ／ Premiere JSON: ${json?'書き出し済み':'未書き出し'} ／ Premiere: ${applied?'反映を確認済み':'反映未確認'}${output.srt_path?` ／ SRT保存先: ${output.srt_path}`:''}${output.json_path?` ／ JSON保存先: ${output.json_path}`:''}`;
 $('#complete').disabled=!project.items.length||running;$('#draft').disabled=!project.items.length||running;$('#srt-pages-open').disabled=!project.items.length||running;
 if(premiereTargetRevision!==null&&premiereTargetRevision!==project.revision){premiereTarget=null;premiereTargetRevision=null;premiereSnapshot=false;premiereFirstImport=false;$('#first-import-confirm').checked=false;$('#source-time-verified').checked=false;$('#premiere-progress').textContent='作業内容が変わりました。対象を再取得してください。'}
 const premiere=w.premiere||{};$('#premiere-capability').textContent=premiere.available?'Premiere 25に接続済み。対象素材と既存Transcriptを照合してから適用します。':'Premiere 25の接続待ちです。接続後に選択素材を取得できます。';
 $('#premiere-selected').disabled=!premiere.available||running||!project.items.length;
 $('#premiere-snapshot').disabled=!premiere.available||!premiereTarget||running;
 $('#first-import-wrap').hidden=!premiereFirstImport;
 $('#premiere-apply').disabled=!premiere.available||!premiereSnapshot||!json||running||(premiereFirstImport&&!$('#first-import-confirm').checked);
 $('#premiere-import-srt').disabled=!premiere.available||!premiereSnapshot||!srt||running;
 const assetImported=output.srt_asset_status==='imported_verified',trackCreated=output.srt_track_status==='placement_created_pending_visual';
 $('#caption-placement-status').textContent=trackCreated?'字幕トラックを作成・保存し、トラック数を読み戻しました。Premiere画面で字幕の位置・文字欠けを確認してください。':premiere.srt_track_placement?'配置機能に接続済み。先に現在のSRTを素材として読み込んでください。':'字幕トラック配置の接続待ちです。';
 $('#premiere-place-srt').disabled=!premiere.available||!premiere.srt_track_placement||!premiereSnapshot||!srt||!assetImported||trackCreated||running||!$('#source-time-verified').checked;
 $('#premiere-target').textContent=premiereTarget?`対象プロジェクト: ${premiereTarget.projectPath} ／ 素材名: ${premiereTarget.clipName} ／ メディア: ${premiereTarget.mediaPath} ／ ${premiereSnapshot?'現状照合済み':'現状未照合'}`:'対象未確認';
 $('#review-section').classList.toggle('waiting',!project.items.length);
 if(running)startWorkflowPolling();else stopWorkflowPolling();
 syncPlayer();
}
function syncPlayer(){
 if(!project.has_media)return;
 const mediaKey=project.workflow?.media_path||project.source_media_name;
 if(player&&player.dataset.mediaKey===mediaKey)return;
 if(player){player.pause();player.hidden=true;}
 stopAt=null;player=project.has_video?$('#video'):$('#audio');player.dataset.mediaKey=mediaKey;player.src='/media?revision='+project.revision;player.hidden=false;
 player.ontimeupdate=()=>{if(stopAt!==null&&player.currentTime>=stopAt){player.pause();stopAt=null}updateAlignmentPosition()};
 player.onpause=updateAlignmentPosition;player.onplay=updateAlignmentPosition;player.onseeked=updateAlignmentPosition;
}
function renderReferences(){
 const refs=project.references||{},docs=refs.documents||[],suggestions=refs.suggestions||[];
 $('#reference-count').textContent=`資料 ${docs.length}件・候補 ${suggestions.filter(x=>x.status==='candidate').length}件`;$('#reference-suggest').disabled=!docs.length||!project.items.length;
 const list=$('#reference-documents');list.replaceChildren();for(const doc of docs){const p=document.createElement('p');p.textContent=`${doc.name}・${doc.terms_count??doc.terms?.length??0}語`;p.title=doc.path||'';list.append(p)}
 const box=$('#reference-suggestions');box.replaceChildren();for(const s of suggestions){const card=document.createElement('article');card.className='reference-suggestion';const heading=document.createElement('strong');heading.textContent=`${time(project.items[s.segment_index]?.start_seconds||0)}・${s.source_name||'参照資料'}`;card.append(heading);
  for(const [label,value] of [['現在',s.base_text||project.items[s.segment_index]?.text||''],['採用後',s.candidate_text||s.base_text?.replace(s.from,s.to)||''],['根拠',s.evidence||'']]){const p=document.createElement('p');const b=document.createElement('b');b.textContent=`${label}: `;p.append(b,document.createTextNode(value));card.append(p)}
  if(s.status==='candidate'){const button=document.createElement('button');button.type='button';button.textContent='この修正を採用';button.onclick=async()=>{if(drafts.size){note('未保存の原文または訳を保存してから候補を採用してください。',true);return}const current=project.items[s.segment_index];if(!current){note('対象の発言が見つかりません。',true);return}await op('/api/references/adopt',{suggestion_id:s.id,segment_index:s.segment_index,current_text:current.text})};card.append(button)}else{const label=document.createElement('small');label.textContent=s.status==='adopted'?'採用済み':'現在の原文には採用できません';card.append(label)}box.append(card)}
}
async function workflowPost(path,payload,progress){
 if(busy)return false;busy=true;const unlock=lockActionButtons('処理中');progress.textContent='処理中…';let result=null;
 try{const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':project.token},body:JSON.stringify({...payload,revision:project.revision})});result=await response.json();if(!response.ok)throw Error(result.error||'処理に失敗しました');await load();unlock();render();progress.textContent='完了しました';note('完了しました');return result}
 catch(error){progress.textContent=error.message;note(error.message,true);return false}
 finally{unlock();busy=false}
}
async function configureWorkflow(){
 if(drafts.size){note('未保存の原文または訳を保存してください。',true);return false}
 const media=$('#media-path').value.trim();if(!media.startsWith('/')){$('#workflow-progress').textContent='素材の絶対パスを入力してください。';return false}
 const result=await workflowPost('/api/workflow/configure',{mode:'multilingual',engine:workflowEngine(),media_path:media,language:$('#initial-language').value},$('#workflow-progress'));
 if(result){configuredSignature=setupSignature();renderWorkflow();note('開始設定を保存しました。文字起こしはまだ開始していません。');return true}return false;
}
async function startWorkflow(){
 if(configuredSignature!==setupSignature()){const ready=await configureWorkflow();if(!ready)return}
 const external=workflowEngine()==='assemblyai';const result=await workflowPost('/api/workflow/start',{confirm_external:external},$('#workflow-progress'));
 if(result){$('#workflow-progress').textContent='文字起こし・話者分離の完了を待っています。';note(external?'AssemblyAIへの音声送信を開始しました。':'Mac内の文字起こしを開始しました。');startWorkflowPolling()}
}
function startWorkflowPolling(){if(workflowPoll)return;workflowPoll=setInterval(async()=>{if(busy||drafts.size)return;try{const revision=project.revision,stage=project.workflow?.stage,job=project.workflow?.job?.status;await load();if(project.revision!==revision||project.workflow?.stage!==stage||project.workflow?.job?.status!==job){render();if(project.workflow?.stage==='error')note(project.workflow.job?.error||project.workflow.status||'文字起こしに失敗しました。',true);if(stage==='transcribing'&&project.workflow?.stage==='review'){$('#workflow-setup').classList.add('compact');updatePanelToggles();note('文字起こしが完了しました。原語と話者を確認してください。')}}}catch(error){$('#workflow-progress').textContent=`状態の取得に失敗しました: ${error.message}`}},2000)}
function stopWorkflowPolling(){if(workflowPoll){clearInterval(workflowPoll);workflowPoll=null}}
for(const input of document.querySelectorAll('input[name="workflow-mode"]'))input.onchange=renderWorkflow;
for(const input of document.querySelectorAll('input[name="workflow-engine"]'))input.onchange=renderWorkflow;
$('#media-path').oninput=renderWorkflow;$('#initial-language').onchange=renderWorkflow;
$('#workflow-configure').onclick=configureWorkflow;$('#workflow-start').onclick=startWorkflow;
$('#media-pick').onclick=async()=>{
 const result=await workflowPost('/api/picker/media',{},$('#workflow-progress'));
 if(!result)return;
 if(result.cancelled){$('#workflow-progress').textContent='素材の選択をキャンセルしました。';return}
 $('#media-path').value=result.paths[0]||'';configuredSignature='';renderWorkflow();$('#workflow-progress').textContent='素材を選択しました。言語とエンジンを確認し、設定を保存してください。';
};
$('#reference-pick').onclick=async()=>{
 if(drafts.size){note('未保存の原文または訳を保存してください。',true);return}
 const result=await workflowPost('/api/picker/references',{},$('#reference-progress'));
 if(!result)return;
 if(result.cancelled){$('#reference-progress').textContent='資料の選択をキャンセルしました。';return}
 await workflowPost('/api/references/load',{paths:result.paths},$('#reference-progress'));
};

$('#reference-load').onclick=async()=>{const paths=$('#reference-paths').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);if(!paths.length){$('#reference-progress').textContent='資料の絶対パスを入力してください。';return}if(paths.some(x=>!x.startsWith('/'))){$('#reference-progress').textContent='すべて絶対パスで入力してください。';return}if(drafts.size){note('未保存の原文または訳を保存してください。',true);return}const result=await workflowPost('/api/references/load',{paths},$('#reference-progress'));if(result)$('#reference-paths').value=''};
$('#reference-suggest').onclick=async()=>{if(drafts.size){note('未保存の原文または訳を保存してください。',true);return}await workflowPost('/api/references/suggest',{},$('#reference-progress'))};
function updatePanelToggles(){for(const [section,button,open,closed] of [['#workflow-setup','#workflow-toggle','設定をたたむ','開始設定を開く'],['#reference-section','#reference-toggle','資料欄をたたむ','参照資料を開く']]){const collapsed=$(section).classList.contains('compact');$(button).textContent=collapsed?closed:open;$(button).setAttribute('aria-expanded',String(!collapsed))}}
$('#workflow-toggle').onclick=()=>{$('#workflow-setup').classList.toggle('compact');updatePanelToggles()};
$('#reference-toggle').onclick=()=>{$('#reference-section').classList.toggle('compact');updatePanelToggles()};
for(const [link,section] of [['a[href="#workflow-setup"]','#workflow-setup'],['a[href="#reference-section"]','#reference-section']])$(link).onclick=()=>{$(section).classList.remove('compact');updatePanelToggles()};

function srtPageRow(page={}){
 const row=document.createElement('div');row.className='srt-page-row';
 const timing=document.createElement('div');timing.className='setup-grid';
 for(const [name,label] of [['start','開始秒'],['end','終了秒']]){const field=document.createElement('label');field.textContent=label;const input=document.createElement('input');input.type='number';input.step='0.001';input.min='0';input.className=name;input.value=page[name]??'';field.append(input);timing.append(field)}
 row.append(timing);
 for(const [name,label] of [['source','原語'],['translation','日本語訳']]){const field=document.createElement('label');field.textContent=label;field.className='wide-label';const input=document.createElement('textarea');input.className=name;input.rows=2;input.value=page[name]??'';field.append(input);row.append(field)}
 const remove=document.createElement('button');remove.type='button';remove.textContent='このページを削除';remove.onclick=()=>{row.remove();srtDirty=true};row.append(remove);$('#srt-pages-list').append(row);return row;
}
function refreshSrtPages(){const idx=Number($('#srt-pages-segment').value),item=project.items[idx];if(!item)return;const translation=item.language==='ja-jp'?'':item.translation.text||'';$('#srt-pages-source').textContent=`対象 ${time(item.start_seconds)}–${time(item.end_seconds)} ／ 原語: ${item.text} ／ 訳: ${translation||'なし'}`;$('#srt-pages-list').replaceChildren();const pages=item.srt_pages?.length?item.srt_pages:[{start:item.start_seconds,end:item.end_seconds,source:item.text,translation}];pages.forEach(srtPageRow);$('#srt-pages-error').textContent='';for(const input of document.querySelectorAll('#srt-pages-list .translation'))input.closest('label').hidden=item.language==='ja-jp'}
$('#srt-pages-open').onclick=()=>{if(drafts.size){note('未保存の原文または訳を保存してから字幕を分割してください。',true);return}const select=$('#srt-pages-segment');select.replaceChildren();project.items.forEach((item,i)=>select.add(new Option(`${i+1}・${time(item.start_seconds)}・${item.text.slice(0,36)}`,i)));if(!project.items.length)return;srtDirty=false;srtSelected=select.value;refreshSrtPages();$('#srt-pages-dialog').showModal()};
$('#srt-pages-list').oninput=()=>{srtDirty=true};
$('#srt-pages-segment').onchange=()=>{if(srtDirty){$('#srt-pages-segment').value=srtSelected;$('#srt-pages-error').textContent='編集中の分割があります。保存するか、閉じて破棄してから対象を変更してください。';return}srtSelected=$('#srt-pages-segment').value;refreshSrtPages()};
$('#srt-pages-close').onclick=()=>{if(srtDirty&&!window.confirm('保存していない字幕分割を破棄しますか？'))return;srtDirty=false;$('#srt-pages-dialog').close()};
$('#srt-pages-dialog').oncancel=event=>{if(srtDirty&&!window.confirm('保存していない字幕分割を破棄しますか？'))event.preventDefault();else srtDirty=false};
$('#srt-pages-add').onclick=()=>{const item=project.items[Number($('#srt-pages-segment').value)];if(!item)return;const rows=[...document.querySelectorAll('.srt-page-row')];const last=rows.at(-1);const start=last?Number(last.querySelector('.end').value):item.start_seconds;const row=srtPageRow({start,end:item.end_seconds});srtDirty=true;row.querySelector('.translation').closest('label').hidden=item.language==='ja-jp';row.querySelector('.source').focus()};
$('#srt-pages-save').onclick=async()=>{const index=Number($('#srt-pages-segment').value),item=project.items[index];if(!item)return;const error=$('#srt-pages-error');error.textContent='';if(drafts.size){error.textContent='未保存の原文または訳を保存してください。';return}const pages=[...document.querySelectorAll('.srt-page-row')].map(row=>({start:Number(row.querySelector('.start').value),end:Number(row.querySelector('.end').value),source:row.querySelector('.source').value,translation:item.language==='ja-jp'?null:row.querySelector('.translation').value}));if(!pages.length||pages.some(p=>!Number.isFinite(p.start)||!Number.isFinite(p.end)||p.end<=p.start||!p.source.trim())){error.textContent='各ページの時刻と原語を入力してください。';return}const result=await workflowPost('/api/srt/pages',{segment_index:index,pages},error);if(result){srtDirty=false;$('#srt-pages-dialog').close();note('字幕の分割を保存しました。完成書き出しで行数と幅を再検査します。')}};
$('#premiere-selected').onclick=async()=>{if(drafts.size){note('未保存の入力を保存してから対象を照合してください。',true);return}const result=await workflowPost('/api/premiere/selected',{},$('#premiere-progress'));if(result?.target){premiereTarget=result.target;premiereTargetRevision=project.revision;premiereSnapshot=false;premiereFirstImport=false;$('#first-import-confirm').checked=false;$('#source-time-verified').checked=false;$('#premiere-progress').textContent='対象を取得しました。プロジェクトと素材名を確認し、現状を照合してください。';renderWorkflow()}};
$('#premiere-snapshot').onclick=async()=>{if(!premiereTarget)return;const result=await workflowPost('/api/premiere/snapshot',{target:premiereTarget},$('#premiere-progress'));if(result){premiereSnapshot=true;premiereTargetRevision=project.revision;const segments=result.transcript?.segments;premiereFirstImport=result.transcript===null&&result.transcriptAvailable===false;$('#first-import-confirm').checked=false;$('#premiere-progress').textContent=premiereFirstImport?`既存Transcriptを取得できませんでした（${result.exportError||'原因未確認'}）。初回適用を選ぶ場合、プロジェクト全体のバックアップを作成してから保存・読み戻しを行います。`:Array.isArray(segments)?`対象の現状を照合しました。既存の文字起こし ${segments.length}区間。`:'対象の現状を照合しました。';renderWorkflow()}};
$('#premiere-apply').onclick=async()=>{if(!premiereTarget||!premiereSnapshot||drafts.size)return;const result=await workflowPost('/api/premiere/apply',{target:premiereTarget,confirm_apply:true,allow_first_import:premiereFirstImport&&$('#first-import-confirm').checked},$('#premiere-progress'));if(result){$('#premiere-progress').textContent=result.state==='applied_verified'?`適用・保存・読み戻し確認済み。バックアップ: ${result.backupPath||'作成済み'}`:'適用結果を確認してください。';renderWorkflow()}};
$('#premiere-import-srt').onclick=async()=>{if(!premiereTarget||!premiereSnapshot||drafts.size)return;const result=await workflowPost('/api/premiere/import-srt',{target:premiereTarget,confirm_import:true},$('#premiere-progress'));if(result){$('#premiere-progress').textContent=result.state==='asset_imported_verified'?'SRTをPremiereプロジェクトへ素材として読み込み、保存を確認しました。字幕トラックへの配置は未実施です。':'SRTの読み込み結果を確認してください。';renderWorkflow()}};
$('#first-import-confirm').onchange=renderWorkflow;$('#source-time-verified').onchange=renderWorkflow;
$('#premiere-place-srt').onclick=async()=>{if(!premiereTarget||!premiereSnapshot||drafts.size||!$('#source-time-verified').checked)return;const result=await workflowPost('/api/premiere/place-srt',{target:premiereTarget,confirm_placement:true,source_time_verified:true},$('#premiere-progress'));if(result){$('#premiere-progress').textContent=result.state==='placement_created_pending_visual'?`字幕トラックを作成・保存し、${result.captionTrackCount||1}トラックを読み戻しました。Premiere画面で字幕の時刻・表示・文字欠けを確認してください。`:'配置結果を確認してください。';renderWorkflow()}};
