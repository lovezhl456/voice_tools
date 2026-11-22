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
  let recordingGroups = new Map();
  const message = (value) => { $('message').textContent = value; };
  const fileFilter = createReviewFileFilter(data.records, (record) => record.id, refresh);
  const player = $('player');
  const waveform = createReviewWaveform(player, (start, end) => playback.setRange(start, end), message, {focusSelection:true});
  const duplex = createRecordingDuplexTimeline(player, (time) => waveform.setTime(time));
  const playback = createReviewPlayback(player, {
    duration: () => currentRecord?.duration_s,
    seek: (time) => waveform.setTime(time),
    rangeChanged: (start, end) => {
      waveform.syncRange(start, end);
      duplex.setRange(start, end);
    },
    error:message
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
  const rangeLabel = (row) => `${row.start_s.toFixed(2)}–${row.end_s.toFixed(2)} 秒`;
  const statusLabel = (row) => row.draft ? '漏检草稿' : names[row.status];
  const matchesScope = (row) => (!$('batch').value || row.batch_id === $('batch').value) &&
    (!$('definition').value || row.definition_id === $('definition').value) &&
    (!$('version').value || row.version === $('version').value);
  function updateSelection() {
    for (const button of $('queue').children) {
      button.setAttribute('aria-current', String(currentRecord?.id === button.dataset.recordingId));
    }
    for (const button of $('annotationTimeline').children) {
      button.setAttribute('aria-pressed', String(current?.id === button.dataset.annotationId));
    }
    $('annotationSelect').value = current?.id || '';
  }
  function showProgress() {
    $('progress').textContent = `${recordingGroups.size} 份录音 · ${visible.length} 条符合筛选的检测记录 · ${decisions.size} 条复核草稿 · ${manual.size} 条漏检补标` +
      (formDirty ? ' · 表单尚未保存' : '');
  }
  function renderQueue() {
    $('queue').replaceChildren();
    for (const [id, annotations] of recordingGroups) {
      const button = document.createElement('button'), count = document.createElement('small');
      button.type = 'button'; button.dataset.recordingId = id;
      button.textContent = filename(records.get(id).input);
      count.textContent = annotations.length ? `${annotations.length} 条标注` : '无标注';
      button.append(count);
      button.onclick = () => {
        if (formDirty) { message('请先保存或放弃当前表单修改。'); return; }
        selectRecording(records.get(id), annotations[0] || null);
      };
      $('queue').append(button);
    }
    if (!recordingGroups.size) $('queue').textContent = '没有符合条件的录音。';
    updateSelection();
    showProgress();
  }
  function renderAnnotations(record) {
    const annotations = recordingGroups.get(record.id) || [];
    const picker = $('annotationSelect'), timeline = $('annotationTimeline');
    picker.replaceChildren(new Option(annotations.length ? '选择一条标注' : '暂无标注', ''));
    picker.disabled = !annotations.length;
    timeline.replaceChildren();
    const laneEnds = [];
    for (const row of annotations) {
      const title = `${row.label} · ${rangeLabel(row)} · ${statusLabel(row)}`;
      picker.add(new Option(title, row.id));
      const marker = document.createElement('button');
      marker.type = 'button'; marker.dataset.annotationId = row.id;
      marker.dataset.status = row.draft ? 'draft' : row.status;
      marker.title = `${title} · ${row.batch_id} · ${row.definition_id}@${row.version}`;
      marker.setAttribute('aria-label', marker.title);
      const availableLane = laneEnds.findIndex((end) => end <= row.start_s);
      const lane = availableLane < 0 ? laneEnds.length : availableLane;
      laneEnds[lane] = row.end_s;
      marker.style.setProperty('--lane', String(lane));
      marker.style.setProperty('--start', `${row.start_s / record.duration_s * 100}%`);
      marker.style.setProperty('--width', `${(row.end_s - row.start_s) / record.duration_s * 100}%`);
      marker.onclick = () => select(row);
      timeline.append(marker);
    }
    timeline.style.setProperty('--lane-count', String(laneEnds.length));
    picker.onchange = () => {
      const selected = annotations.find((row) => row.id === picker.value);
      if (selected) select(selected);
      else updateSelection();
    };
    $('recordingSummary').textContent = `${annotations.length} 条标注 · ${Number(record.duration_s.toFixed(2))} 秒`;
  }
  function refresh() {
    const tags = $('tags').value.split(/[,，]/).map((value) => value.trim()).filter(Boolean);
    const status = $('status').value;
    let rows = data.findings.map(effective).filter((row) => fileFilter.matches(records.get(row.recording_id)) &&
      matchesScope(row) && (!status || row.status === status));
    const matchAll = tags.length && $('tagMode').value === 'all';
    if (matchAll) {
      const tagsByCall = new Map();
      for (const row of rows) {
        const key = row.batch_id + ':' + row.recording_id;
        if (!tagsByCall.has(key)) tagsByCall.set(key,new Set());
        tagsByCall.get(key).add(row.label);
      }
      rows = rows.filter((row) => tags.every((tag) => tagsByCall.get(row.batch_id + ':' + row.recording_id).has(tag)));
    }
    visible = rows.filter((row) => !tags.length || tags.includes(row.label));
    recordingGroups = new Map();
    for (const row of visible) {
      if (!recordingGroups.has(row.recording_id)) recordingGroups.set(row.recording_id, []);
      recordingGroups.get(row.recording_id).push(row);
    }
    if (!tags.length && !status) {
      for (const evaluation of data.evaluations) {
        if (!recordingGroups.has(evaluation.recording_id) && matchesScope(evaluation) &&
            fileFilter.matches(records.get(evaluation.recording_id))) {
          recordingGroups.set(evaluation.recording_id, []);
        }
      }
    }
    if (!status) {
      for (const item of manual.values()) {
        const evaluation = data.evaluations.find((row) => row.batch_id === item.batch_id &&
          row.recording_id === item.recording_id && row.config_hash === item.config_hash);
        if (!evaluation || !matchesScope(evaluation) || !fileFilter.matches(records.get(item.recording_id)) ||
            (tags.length && !tags.includes(item.label)) || (matchAll && !recordingGroups.has(item.recording_id))) continue;
        if (!recordingGroups.has(item.recording_id)) recordingGroups.set(item.recording_id, []);
        recordingGroups.get(item.recording_id).push({...evaluation, ...item, draft: true});
      }
    }
    for (const annotations of recordingGroups.values()) {
      annotations.sort((a,b) => a.start_s - b.start_s || a.end_s - b.end_s);
    }
    renderQueue();
    if (formDirty) { message('筛选已更新；请先保存或放弃当前表单修改。'); return; }
    const target = recordingGroups.has(currentRecord?.id) ? currentRecord.id : recordingGroups.keys().next().value;
    if (target) {
      const annotations = recordingGroups.get(target);
      selectRecording(records.get(target), annotations.find((row) => row.id === current?.id) || annotations[0] || null, true);
    } else {
      playback.pause(); current = null; currentRecord = null; $('detail').hidden = true;
    }
  }
  function setAudio(keepTime=false) {
    const sources = currentRecord?.playback_sources || {};
    for (const option of $('channel').options) option.disabled = !sources[option.value];
    if (!sources[$('channel').value]) $('channel').value = sources.both ? 'both' : 'left';
    playback.setSource(sources[$('channel').value] || '', keepTime);
    waveform.syncChannel($('channel').value);
    $('audioHint').textContent = currentRecord.audio_error || (sources.both ? '音频摘要已核对。图形保持原始声道；单轨选择仅改变试听。' : '未附带音频；生成报告时增加 --include-audio 可试听。波形仍可查看。');
  }
  function showAudio(record, start, end, showEvidence) {
    const sameRecording = currentRecord?.id === record.id;
    currentRecord = record;
    playback.pause();
    if (sameRecording) {
      waveform.setEvidenceRange(start, end, showEvidence);
      playback.setRange(start, end);
      playback.applyRange(true);
      return;
    }
    $('seek').max = record.duration_s;
    $('start').value = start; $('end').value = end;
    for (const [index,id] of ['leftTitle','rightTitle'].entries()) {
      $(id).querySelector('.role-name').textContent = `声道 ${index}`;
      $(id).querySelector('.role-meta').textContent = '角色按检测配置约定';
    }
    setAudio();
    const hasDuplex = duplex.load(record.waveform, record.duration_s);
    $('recordingAnnotations').classList.toggle('with-duplex', hasDuplex);
    if (!hasDuplex) $('precisionWave').open = true;
    duplex.setRange(start, end);
    waveform.load({record, op:{at_s:start, observed_until_s:end}, showEvidence});
  }
  function select(row) {
    if (formDirty) { message('请先保存或放弃当前表单修改。'); updateSelection(); return; }
    selectRecording(records.get(row.recording_id), row);
  }
  function selectRecording(record, row, refreshAnnotations = false) {
    const changed = currentRecord?.id !== record.id;
    const previous = current;
    current = row; $('detail').hidden = false;
    $('name').textContent = filename(record.input);
    $('recordingNote').hidden = true;
    if (changed || refreshAnnotations) renderAnnotations(record);
    $('annotationDetail').hidden = !row;
    $('reviewForm').hidden = !row || Boolean(row.draft);
    if (row) {
      $('annotationName').textContent = `${row.label} · ${rangeLabel(row)}`;
      $('identity').textContent = row.draft
        ? `${row.definition_id}@${row.version} · ${row.batch_id} · 漏检草稿`
        : `${row.definition_id}@${row.version} · ${row.batch_id} · 复核版本 ${row.revision}`;
      $('reason').textContent = row.draft ? row.comment : row.reason;
      $('annotationEvidence').hidden = Boolean(row.draft);
      if (!row.draft) {
        $('evidence').textContent = JSON.stringify(row.automatic, null, 2);
        $('reviewStatus').value = row.status; $('reviewLabel').value = row.label;
        $('reviewStart').value = row.start_s; $('reviewEnd').value = row.end_s;
        $('reviewer').value = row.reviewer || $('reviewer').value;
        $('comment').value = row.comment;
      }
    }
    const start = row?.start_s ?? 0, end = row?.end_s ?? record.duration_s;
    if (changed || previous?.id !== row?.id || previous?.start_s !== row?.start_s || previous?.end_s !== row?.end_s) {
      showAudio(record, start, end, Boolean(row));
    }
    const index = data.evaluations.findIndex((item) => item.recording_id === record.id &&
      (row ? item.batch_id === row.batch_id && item.config_hash === row.config_hash : matchesScope(item)));
    $('addRecordingAnnotation').hidden = index < 0;
    $('addRecordingAnnotation').onclick = () => {
      $('manualEvaluation').value = String(index);
      $('manualStart').value = $('start').value;
      $('manualEnd').value = $('end').value;
      $('normalChecked').checked = false;
      $('manualForm').scrollIntoView({block:'start'});
      $('manualLabel').focus({preventScroll:true});
    };
    updateSelection();
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
      remove.textContent = '移除草稿'; remove.onclick = () => { manual.delete(item.id); persist(); renderManual(); refresh(); };
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
  for (const id of ['reviewStatus','reviewLabel','reviewStart','reviewEnd','reviewer','comment']) $(id).oninput = () => { formDirty=true; showProgress(); };
  $('discard').onclick = () => { formDirty=false; refresh(); message('已放弃表单修改。'); };
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
    selectRecording(records.get(item.recording_id), null);
    playback.setRange(0, item.duration_s); playback.applyRange(true);
    $('recordingNote').textContent = `${item.batch_id} · ${item.definition_id}@${item.version} · ${item.status}${item.note ? ' · ' + item.note : ''}`;
    $('recordingNote').hidden = false;
    $('manualStart').value=0; $('manualEnd').value=item.duration_s;
    $('detail').scrollIntoView({block:'start'});
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
      else { manual.set(item.id,item); persist(); renderManual(); refresh(); }
    }
    catch(error) { message(error.message); }
  };
  window.addEventListener('beforeunload',(event) => { if (formDirty || notExported) { event.preventDefault(); event.returnValue=''; } });
  refresh();
  try { const saved=localStorage.getItem(key); if (saved) { importPacket(JSON.parse(saved)); message('已恢复此报告的本地草稿；可继续复核或导出。'); } }
  catch(error) { message('本地草稿未加载：' + error.message); }
})();
