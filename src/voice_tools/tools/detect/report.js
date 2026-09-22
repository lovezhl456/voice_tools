'use strict';
(async () => {
  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($('reviewData').textContent);
  const session = JSON.parse($('liveSession').textContent);
  let standardRows = [];
  async function liveApi(path, body) {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
      headers:{'Content-Type':'application/json','X-Voice-Tools-Session':session.token},
      ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || '保存失败，请检查本机服务');
    return result;
  }
  if (session) {
    try {
      const live = await liveApi('/api/review-state');
      const batches = new Set(data.batches.map((item) => item.id));
      data.findings = live.findings.filter((item) => batches.has(item.batch_id));
      standardRows = live.standards;
      $('liveToolbar').hidden=false; $('auditPanel').hidden=false; $('normalControls').hidden=false;
      $('reviewForm').querySelector('[type=submit]').textContent='保存到检测库';
      $('manualForm').querySelector('[type=submit]').textContent='保存漏检到检测库';
      $('reviewForm').querySelector('p.muted').textContent='在线保存直接入库；问题确认和误报原因同时积累为人工标准。';
    } catch(error) { $('message').textContent=error.message + '；请重新打开服务后刷新。'; return; }
  }
  const names = {pending:'待复核', confirmed:'已确认', false_positive:'误报', corrected:'已修正'};
  const records = new Map(data.records.map((record) => [record.id, record]));
  const originals = new Map(data.findings.map((row) => [row.id, row]));
  const decisions = new Map(), manual = new Map();
  const key = `voice-tools-detect:${data.library_id}:${data.report_id}`;
  let current = null, currentRecord = null, visible = [], formDirty = false, notExported = false;
  const message = (value) => { $('message').textContent = value; };
  const fileFilter = createReviewFileFilter(data.records, (record) => record.id, refresh);
  const waveform = createReviewWaveform($('player'), (start, end) => playback.setRange(start, end), message, {focusSelection:true});
  const playback = createReviewPlayback($('player'), {
    duration: () => currentRecord?.duration_s,
    seek: (time) => waveform.setTime(time), rangeChanged: (a,b) => waveform.syncRange(a,b), error:message
  });
  const filename = (path) => path.split(/[\\/]/).pop();
  const packet = () => ({schema_version:'1.0', library_id:data.library_id, reviews:[...decisions.values()], manual:[...manual.values()]});
  const effective = (row) => ({...row, ...(decisions.get(row.id) || {})});
  function populate(id, values) { for (const value of [...new Set(values)].sort()) $(id).add(new Option(value,value)); }
  populate('batch', data.batches.map((batch) => batch.id));
  populate('definition', data.definitions.map((item) => item.id));
  populate('version', data.definitions.map((item) => item.version));
  for (const label of new Set(data.findings.map((row) => row.label))) $('tagOptions').append(new Option(label,label));
  $('summary').textContent = `${data.records.length} 通录音 · ${data.batches.length} 个批次 · ${data.findings.length} 条标签 · ${data.created_at}`;
  $('configs').textContent = JSON.stringify(data.definitions.map((item) => item.config), null, 2);
  for (const batch of data.batches) {
    const p = document.createElement('p');
    const evaluations = data.evaluations.filter((item) => item.batch_id === batch.id);
    const incomplete = evaluations.filter((item) => item.status !== 'ok');
    p.textContent = `${batch.id} · ${batch.status} · ${batch.evaluations} 项检测 · ${batch.errors.length} 个输入错误 · ${incomplete.length} 项未完整分析`;
    $('batchSummary').append(p);
    for (const item of [...batch.errors.map((error) => `${error.source}：${error.error}`),
                        ...incomplete.map((row) => `${row.recording_id.slice(0,12)} ${row.definition_id}@${row.version}：${row.note}`)]) {
      const note = document.createElement('p'); note.className='muted'; note.textContent=item; $('batchSummary').append(note);
    }
  }
  data.evaluations.forEach((item, index) => {
    $('manualEvaluation').add(new Option(`${filename(item.sources[0])} · ${item.batch_id} · ${item.definition_id}@${item.version} · ${item.status}`, String(index)));
  });

  async function submitPacket(value) {
    const response = await liveApi('/api/workspace/reviews', value);
    for (const row of response.findings) {
      if (!data.batches.some((batch) => batch.id === row.batch_id)) continue;
      const index = data.findings.findIndex((item) => item.id === row.id);
      if (index < 0) data.findings.push(row); else data.findings[index] = row;
      originals.set(row.id, row);
    }
    for (const item of value.reviews) decisions.delete(item.finding_id);
    for (const item of value.manual) manual.delete(item.id);
    formDirty=false; notExported=decisions.size > 0 || manual.size > 0;
    try { localStorage.setItem(key,JSON.stringify(packet())); }
    catch(error) { /* The database save succeeded even if browser draft storage is unavailable. */ }
    renderManual(); refresh();
    if (current && originals.has(current.id)) select(originals.get(current.id));
    message('已保存到检测库，重新打开仍可继续复核。');
  }
  if (session) {
    const empty = data.evaluations.map((item,index)=>({item,index})).filter(({item})=>item.status==='ok' &&
      !data.findings.some((finding)=>finding.origin==='auto' && finding.batch_id===item.batch_id && finding.recording_id===item.recording_id && finding.config_hash===item.config_hash));
    for (const {item,index} of empty) $('auditEvaluation').add(new Option(`${filename(item.sources[0])} · ${item.definition_id}@${item.version}`,String(index)));
    $('auditProgress').textContent=`${empty.length} 项未命中检测可抽检；${standardRows.filter((row)=>row.verdict==='normal' && records.has(row.recording_id)).length} 个正常范围已保存。`;
    $('auditListen').onclick=()=>{
      if (formDirty) { message('请先保存或放弃当前复核。'); return; }
      if (!$('auditEvaluation').options.length) { message('本批没有可抽检的未命中录音。'); return; }
      $('manualEvaluation').value=$('auditEvaluation').value; $('listenManual').click();
      const evaluation=data.evaluations[Number($('manualEvaluation').value)];
      const definition=data.definitions.find((item)=>item.hash===evaluation.config_hash);
      $('manualLabel').value=definition?.config.rules.length===1 ? definition.config.rules[0].label : '';
      $('normalChecked').checked=false;
    };
    $('saveDrafts').onclick=async()=>{
      try { if(formDirty) throw Error('请先保存当前表单修改'); await submitPacket(packet()); }
      catch(error){ message(error.message); }
    };
    $('saveNormal').onclick=async()=>{
      try {
        const evaluation=data.evaluations[Number($('manualEvaluation').value)];
        if (!evaluation || evaluation.status!=='ok') throw Error('只能对已成功分析的录音保存正常抽检结论');
        await liveApi('/api/workspace/standard',{id:crypto.randomUUID(),expected_revision:0,recording_id:evaluation.recording_id,
          label:$('manualLabel').value,start_s:$('manualStart').valueAsNumber,end_s:$('manualEnd').valueAsNumber,
          verdict:'normal',reviewer:$('manualReviewer').value,comment:$('manualComment').value,checked:$('normalChecked').checked,
          source:'audit:'+evaluation.batch_id});
        $('normalChecked').checked=false; message('正常范围已保存到标准样本；未检查范围仍保持未知。');
      } catch(error) { message(error.message); }
    };
  }
  function renderQueue() {
    $('queue').replaceChildren();
    for (const row of visible) {
      const button = document.createElement('button'), small = document.createElement('small');
      button.type = 'button'; button.textContent = `${row.label} · ${filename(row.sources[0])}`;
      small.textContent = `${row.start_s.toFixed(2)}–${row.end_s.toFixed(2)} 秒 · ${names[row.status]} · ${row.definition_id}@${row.version} · ${row.batch_id}`;
      button.append(small); button.setAttribute('aria-current', String(current?.id === row.id));
      button.onclick = () => select(row); $('queue').append(button);
    }
    if (!visible.length) $('queue').textContent = '没有符合条件的标签。可清空筛选，或在下方检查无命中的录音。';
    $('progress').textContent = `${visible.length} 条符合筛选 · ${decisions.size} 条复核草稿 · ${manual.size} 条漏检补标` + (formDirty ? ' · 表单尚未保存' : '');
  }
  function refresh() {
    const tags = $('tags').value.split(/[,，]/).map((value) => value.trim()).filter(Boolean);
    let rows = data.findings.map(effective).filter((row) => fileFilter.matches(records.get(row.recording_id)) &&
      (!$('batch').value || row.batch_id === $('batch').value) &&
      (!$('definition').value || row.definition_id === $('definition').value) &&
      (!$('version').value || row.version === $('version').value) &&
      (!$('status').value || row.status === $('status').value));
    if (tags.length && $('tagMode').value === 'all') {
      const groups = new Map();
      for (const row of rows) {
        const id = row.batch_id + ':' + row.recording_id;
        if (!groups.has(id)) groups.set(id,new Set());
        groups.get(id).add(row.label);
      }
      rows = rows.filter((row) => tags.every((tag) => groups.get(row.batch_id + ':' + row.recording_id).has(tag)));
    }
    visible = rows.filter((row) => !tags.length || tags.includes(row.label));
    renderQueue();
    if (formDirty) { message('筛选已更新；请先保存或放弃当前表单修改。'); return; }
    if (visible.length && !visible.some((row) => row.id === current?.id)) select(visible[0]);
    else if (!visible.length) { playback.pause(); current = null; $('detail').hidden = true; }
  }
  function setAudio(keepTime=false) {
    const sources = currentRecord?.playback_sources || {};
    for (const option of $('channel').options) option.disabled = !sources[option.value];
    if (!sources[$('channel').value]) $('channel').value = sources.both ? 'both' : 'left';
    playback.setSource(sources[$('channel').value] || '', keepTime);
    waveform.syncChannel($('channel').value);
    $('audioHint').textContent = currentRecord.audio_error || (sources.both ? '音频摘要已核对。图形保持原始声道；单轨选择仅改变试听。' : '未附带音频；生成报告时增加 --include-audio 可试听。波形仍可查看。');
  }
  function showAudio(record, start, end) {
    currentRecord = record; playback.pause(); $('seek').max = record.duration_s;
    $('start').value = start; $('end').value = end;
    for (const [index,id] of ['leftTitle','rightTitle'].entries()) {
      $(id).querySelector('.role-name').textContent = `声道 ${index}`;
      $(id).querySelector('.role-meta').textContent = '角色按检测配置约定';
    }
    setAudio(); waveform.load({record, op:{at_s:start, observed_until_s:end}});
  }
  function select(row) {
    if (formDirty) { message('请先保存到草稿或放弃表单修改。'); return; }
    current = effective(originals.get(row.id)); $('detail').hidden = false; $('reviewForm').hidden = false;
    $('name').textContent = `${current.label} · ${filename(current.sources[0])}`;
    $('identity').textContent = `${current.definition_id}@${current.version} · ${current.batch_id} · ${current.id} · 复核版本 ${current.revision}`;
    $('reason').textContent = current.reason;
    $('evidence').textContent = JSON.stringify(current.automatic, null, 2);
    $('reviewStatus').value = current.status; $('reviewLabel').value = current.label;
    $('reviewStart').value = current.start_s; $('reviewEnd').value = current.end_s;
    $('reviewer').value = current.reviewer || $('reviewer').value;
    $('comment').value = current.comment;
    showAudio(records.get(current.recording_id), current.start_s, current.end_s); renderQueue();
  }
  function validateRange(start,end,duration) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > duration) throw Error('片段必须在录音范围内，终点大于起点。');
  }
  function required(value,name,max) {
    if (typeof value !== 'string' || !value.trim() || value.length > max) throw Error(`${name}不能为空，且最多 ${max} 字符。`);
  }
  function validateReview(item) {
    const original = originals.get(item.finding_id);
    if (!original || item.expected_revision !== original.revision) throw Error('草稿与此报告的检测身份或复核版本不匹配，请使用原报告或重新生成报告。');
    if (!Object.hasOwn(names,item.status)) throw Error('未知复核状态。');
    required(item.reviewer,'复核人',100); required(item.label,'标签',100);
    if (typeof item.comment !== 'string' || item.comment.length > 4000) throw Error('备注格式错误。');
    validateRange(item.start_s,item.end_s,original.duration_s);
    const auto = original.automatic;
    if ((item.label !== auto.label || item.start_s !== auto.start_s || item.end_s !== auto.end_s) && item.status !== 'corrected') throw Error('修正标签／时间后，请选择“修正标签／片段”。');
  }
  function validateManual(item) {
    const evaluation = data.evaluations.find((row) => row.batch_id === item.batch_id && row.recording_id === item.recording_id && row.config_hash === item.config_hash);
    if (!evaluation || typeof item.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(item.id) || originals.has(item.id)) throw Error('漏检补标的身份不匹配。');
    required(item.label,'标签',100); required(item.reviewer,'复核人',100); required(item.comment,'漏检原因',4000);
    validateRange(item.start_s,item.end_s,evaluation.duration_s);
  }
  function renderManual() {
    $('manualQueue').replaceChildren();
    for (const item of manual.values()) {
      const li = document.createElement('li'), remove = document.createElement('button');
      li.textContent = `${item.label} · ${item.start_s}–${item.end_s} 秒 · ${item.comment} `;
      remove.textContent = '移除草稿'; remove.onclick = () => { manual.delete(item.id); persist(); renderManual(); renderQueue(); };
      li.append(remove); $('manualQueue').append(li);
    }
  }
  function persist() {
    notExported = true;
    try { localStorage.setItem(key,JSON.stringify(packet())); message('已保存到本地草稿；导出并导入检测库后才成为正式复核记录。'); }
    catch { message('浏览器无法保存草稿，请立即导出复核文件以免丢失。'); }
  }
  function importPacket(value) {
    if (value.schema_version !== '1.0' || value.library_id !== data.library_id || !Array.isArray(value.reviews) || !Array.isArray(value.manual)) throw Error('复核文件格式或检测库身份不匹配。');
    if (value.reviews.length + value.manual.length > 10000) throw Error('每次最多导入 10000 条。');
    value.reviews.forEach(validateReview); value.manual.forEach(validateManual);
    if (new Set(value.reviews.map((item) => item.finding_id)).size !== value.reviews.length || new Set(value.manual.map((item) => item.id)).size !== value.manual.length) throw Error('复核文件含重复条目。');
    for (const item of value.reviews) decisions.set(item.finding_id,item);
    for (const item of value.manual) manual.set(item.id,item);
    renderManual(); refresh();
    if (current) select(current);
  }
  function download(value, name) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const link = document.createElement('a'); link.href=url; link.download=name; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  $('reviewForm').onsubmit = async (event) => {
    event.preventDefault();
    const item = {finding_id:current.id, expected_revision:current.revision, status:$('reviewStatus').value,
      label:$('reviewLabel').value, start_s:$('reviewStart').valueAsNumber, end_s:$('reviewEnd').valueAsNumber,
      reviewer:$('reviewer').value, comment:$('comment').value};
    try { validateReview(item);
      if (session) await submitPacket({schema_version:'1.0',library_id:data.library_id,reviews:[item],manual:[]});
      else { decisions.set(item.finding_id,item); formDirty=false; persist(); refresh(); }
    }
    catch(error) { message(error.message); }
  };
  for (const id of ['reviewStatus','reviewLabel','reviewStart','reviewEnd','reviewer','comment']) $(id).oninput = () => { formDirty=true; renderQueue(); };
  $('discard').onclick = () => { formDirty=false; select(current); message('已放弃表单修改。'); };
  $('channel').onchange = () => setAudio(true);
  $('player').addEventListener('error', () => { if ($('player').getAttribute('src')) message('音频加载失败，不能完成试听；请核对报告资源。'); });
  for (const id of ['directoryFilter','fileFilter','batch','definition','version','status','tagMode']) $(id).onchange = () => {
    if (id === 'directoryFilter') fileFilter.updateFiles(); refresh();
  };
  $('tags').oninput = refresh;
  $('clearFilters').onclick = () => {
    for (const id of ['directoryFilter','fileFilter','fileSearch','tags','batch','definition','version','status']) $(id).value='';
    fileFilter.updateFiles(); refresh();
  };
  $('exportResults').onclick = () => download(visible,'detection-query.json');
  $('exportReviews').onclick = () => {
    if (formDirty) { message('请先保存或放弃表单修改，再导出。'); return; }
    download(packet(),'detection-review.json'); notExported=false;
    message('已导出复核文件；使用 detect review-import 导入检测库，再生成新报告。');
  };
  $('importDraft').onchange = async () => {
    if (formDirty) { message('请先保存或放弃当前表单修改。'); return; }
    const file = $('importDraft').files[0]; if (!file) return;
    try { if (file.size > 16*1024*1024) throw Error('复核文件超过 16 MiB。'); importPacket(JSON.parse(await file.text())); persist(); }
    catch(error) { message(error.message); }
    $('importDraft').value='';
  };
  $('listenManual').onclick = () => {
    if (formDirty) { message('请先保存或放弃表单修改。'); return; }
    const item = data.evaluations[Number($('manualEvaluation').value)]; if (!item) return;
    current=null; $('detail').hidden=false; $('reviewForm').hidden=true;
    $('name').textContent = filename(item.sources[0]); $('identity').textContent = `${item.batch_id} · ${item.definition_id}@${item.version} · ${item.status}`;
    $('reason').textContent = item.note || '请试听后在下方补充漏检片段。'; $('evidence').textContent='';
    $('manualStart').value=0; $('manualEnd').value=item.duration_s;
    showAudio(records.get(item.recording_id),0,item.duration_s); $('detail').scrollIntoView({block:'start'});
  };
  for (const id of ['manualEvaluation','manualLabel','manualStart','manualEnd','manualReviewer','manualComment']) {
    $(id).addEventListener('input', () => { $('normalChecked').checked=false; });
    $(id).addEventListener('change', () => { $('normalChecked').checked=false; });
  }
  $('manualForm').onsubmit = async (event) => {
    event.preventDefault(); const evaluation = data.evaluations[Number($('manualEvaluation').value)];
    if (!evaluation) { message('没有可补标的检测记录。'); return; }
    const item = {id:crypto.randomUUID(), batch_id:evaluation.batch_id, recording_id:evaluation.recording_id,
      config_hash:evaluation.config_hash, label:$('manualLabel').value, start_s:$('manualStart').valueAsNumber,
      end_s:$('manualEnd').valueAsNumber, reviewer:$('manualReviewer').value, comment:$('manualComment').value};
    try { validateManual(item);
      if (session) await submitPacket({schema_version:'1.0',library_id:data.library_id,reviews:[],manual:[item]});
      else { manual.set(item.id,item); persist(); renderManual(); renderQueue(); }
    }
    catch(error) { message(error.message); }
  };
  window.addEventListener('beforeunload',(event) => { if (formDirty || notExported) { event.preventDefault(); event.returnValue=''; } });
  refresh();
  try { const saved=localStorage.getItem(key); if (saved) { importPacket(JSON.parse(saved)); message('已恢复此报告的本地草稿；可继续复核或导出。'); } }
  catch(error) { message('本地草稿未加载：' + error.message); }
})();
