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
      $('snapshotHelp').hidden=true; $('liveToolbar').hidden=false; $('auditPanel').hidden=false; $('normalControls').hidden=false;
      $('saveReview').textContent='保存到检测库';
      $('manualForm').querySelector('[type=submit]').textContent='保存漏检到检测库';
    } catch(error) { $('message').textContent=error.message + '；请重新打开服务后刷新。'; return; }
  }
  const names = {pending:'待复核', confirmed:'已确认', false_positive:'误报', corrected:'已修正'};
  // Older reports and imports require a reviewer field. Browser reviews record their source.
  const localReviewer = '本机复核';
  const records = new Map(data.records.map((record) => [record.id, record]));
  const originals = new Map(data.findings.map((row) => [row.id, row]));
  const decisions = new Map(), manual = new Map();
  const key = `voice-tools-detect:${data.library_id}:${data.report_id}`;
  let current = null, currentRecord = null, visible = [], formDirty = false, notExported = false;
  let recordingGroups = new Map(), saving = false, removedManual = null;
  function message(value, location = 'reportFeedback') {
    if (location === 'reviewFeedback' && ($('detail').hidden || $('annotationDetail').hidden)) location = 'reportFeedback';
    const status = $('message'), container = $(location);
    if (status.parentNode !== container) container.append(status);
    status.textContent = value;
  }
  async function saveWithFeedback(action, location, form) {
    if (saving) return;
    saving = true;
    const focused = document.activeElement;
    if (form) form.inert = true;
    message('正在保存…', location);
    try { await action(); }
    catch (error) { message(error.message, location); }
    finally {
      if (form) {
        form.inert = false;
        if (form.contains(focused) && document.activeElement === document.body) focused.focus({preventScroll:true});
      }
      saving = false;
    }
  }
  const fileFilter = createReviewFileFilter(data.records, (record) => record.id, refresh);
  const player = $('player');
  const waveform = createReviewWaveform(player, (start, end) => playback.setRange(start, end), message, {focusSelection:true});
  const duplex = createRecordingDuplexTimeline(player, (time) => waveform.setTime(time));
  const playback = createReviewPlayback(player, {
    duration: () => currentRecord?.duration_s,
    seek: (time) => waveform.setTime(time),
    rangeChanged: updateListeningRange,
    formatTime: (time) => time.toFixed(2) + ' s',
    error:message
  });
  function updateListeningRange(start, end) {
    waveform.syncRange(start, end);
    duplex.setRange(start, end);
    $('replayRange').textContent = '试听 ' + rangeLabel({start_s:start, end_s:end});
  }
  const filename = (path) => path.split(/[\\/]/).pop();
  const packet = () => ({schema_version:'1.0', library_id:data.library_id, reviews:[...decisions.values()], manual:[...manual.values()]});
  const effective = (row) => ({...row, ...(decisions.get(row.id) || {})});
  function populate(id, values) { for (const value of [...new Set(values)].sort()) $(id).add(new Option(value,value)); }
  populate('batch', data.batches.map((batch) => batch.id));
  populate('definition', data.definitions.map((item) => item.id));
  populate('version', data.definitions.map((item) => item.version));
  const labels = new Set(data.definitions.flatMap(item => item.config.rules.map(rule => rule.label)));
  for (const finding of data.findings) labels.add(finding.label);
  for (const label of [...labels].sort()) {
    $('tagOptions').append(new Option(label));
    $('reviewLabel').add(new Option(label));
  }
  $('reviewLabel').add(new Option('自定义标签…', ''));
  $('summary').textContent = `${data.records.length} 通录音 · ${data.batches.length} 个批次 · ${data.findings.length} 条检测标注`;
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
  $('manualEvaluation').add(new Option('选择要复核的检测记录', ''));
  data.evaluations.forEach((item, index) => {
    $('manualEvaluation').add(new Option(`${filename(item.sources[0])} · ${item.batch_id} · ${item.definition_id}@${item.version} · ${item.status}`, String(index)));
  });

  if (data.evaluations.length === 1) $('manualEvaluation').value = '0';
  function manualEvaluation() {
    const selected = $('manualEvaluation').value;
    return selected === '' ? null : data.evaluations[Number(selected)];
  }
  function singleRuleLabel(evaluation) {
    const definition = data.definitions.find(item => item.hash === evaluation?.config_hash);
    return definition?.config.rules.length === 1 ? definition.config.rules[0].label : '';
  }

  async function submitPacket(value, location) {
    const response = await liveApi('/api/workspace/reviews', value);
    for (const row of response.findings) {
      if (!data.batches.some((batch) => batch.id === row.batch_id)) continue;
      const index = data.findings.findIndex((item) => item.id === row.id);
      if (index < 0) data.findings.push(row); else data.findings[index] = row;
      originals.set(row.id, row);
    }
    for (const item of value.reviews) decisions.delete(item.finding_id);
    for (const item of value.manual) manual.delete(item.id);
    notExported = decisions.size > 0 || manual.size > 0;
    try { localStorage.setItem(key,JSON.stringify(packet())); }
    catch(error) { /* The database save succeeded even if browser draft storage is unavailable. */ }
    renderManual(); refresh(true);
    message('已保存到检测库，重新打开仍可继续复核。', location);
  }
  if (session) {
    const empty = data.evaluations.map((item,index)=>({item,index})).filter(({item})=>item.status==='ok' &&
      !data.findings.some((finding)=>finding.origin==='auto' && finding.batch_id===item.batch_id && finding.recording_id===item.recording_id && finding.config_hash===item.config_hash));
    for (const {item,index} of empty) $('auditEvaluation').add(new Option(`${filename(item.sources[0])} · ${item.definition_id}@${item.version}`,String(index)));
    $('auditProgress').textContent=`${empty.length} 项未命中检测可抽检；${standardRows.filter((row)=>row.verdict==='normal' && records.has(row.recording_id)).length} 个正常范围已保存。`;
    $('auditListen').onclick=()=>{
      if (formDirty || saving) { message('请先完成当前复核。'); return; }
      if (!$('auditEvaluation').options.length) { message('本批没有可抽检的未命中录音。'); return; }
      $('manualPanel').open = true;
      $('manualEvaluation').value=$('auditEvaluation').value; $('listenManual').click();
      $('manualLabel').value = singleRuleLabel(manualEvaluation());
      $('normalChecked').checked=false;
    };
    $('saveDrafts').onclick = () => saveWithFeedback(async () => {
      if (formDirty) throw Error('请先保存当前表单修改');
      await submitPacket(packet(), 'reportFeedback');
    }, 'reportFeedback');
    $('saveNormal').onclick = () => saveWithFeedback(async () => {
      const evaluation=manualEvaluation();
      if (!evaluation || evaluation.status!=='ok') throw Error('只能对已成功分析的录音保存正常抽检结论');
      await liveApi('/api/workspace/standard',{id:crypto.randomUUID(),expected_revision:0,recording_id:evaluation.recording_id,
        label:$('manualLabel').value,start_s:$('manualStart').valueAsNumber,end_s:$('manualEnd').valueAsNumber,
        verdict:'normal',reviewer:localReviewer,comment:$('manualComment').value,checked:$('normalChecked').checked,
        source:'audit:'+evaluation.batch_id});
      $('normalChecked').checked=false;
      message('正常范围已保存到标准样本；未检查范围仍保持未知。', 'manualFeedback');
    }, 'manualFeedback', $('normalControls'));
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
    const filters = ['directoryFilter','fileFilter','fileSearch','tags','batch','definition','version','status'];
    const active = filters.filter(id => $(id).value.trim()).length;
    $('activeFilters').textContent = active ? ' · ' + active : '';
    $('progress').hidden = Boolean(recordingGroups.size && !active && !decisions.size && !formDirty);
    $('discard').hidden = !formDirty;
    const state = $('currentReviewState');
    if (formDirty) state.textContent = '有未保存修改';
    else if (decisions.has(current?.id)) state.textContent = '已存草稿';
    else if (current && current.status !== 'pending') state.textContent = '已入库';
    else state.textContent = '';
    $('clearFilters').hidden = !active;
    $('saveDrafts').hidden = !session || (!decisions.size && !manual.size);
    const progress = [`${decisions.size} 条复核草稿`];
    if (active) progress.unshift(`${recordingGroups.size} 份录音 · ${visible.length} 条标注`);
    if (formDirty) progress.push('表单尚未保存');
    $('progress').textContent = progress.join(' · ');
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
        if (currentRecord?.id === id) return;
        if (formDirty || saving) { message(saving ? '正在保存，稍后可切换录音。' : '请先保存或放弃当前表单修改。'); return; }
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
    $('annotationPicker').hidden = annotations.length < 2;
    const markers = document.createDocumentFragment();
    for (const row of annotations) {
      const title = `${row.label} · ${rangeLabel(row)} · ${statusLabel(row)}`;
      picker.add(new Option(title, row.id));
      const marker = document.createElement('button');
      marker.type = 'button'; marker.dataset.annotationId = row.id;
      marker.dataset.status = row.draft ? 'draft' : row.status;
      marker.title = `${title} · ${row.batch_id} · ${row.definition_id}@${row.version}`;
      marker.setAttribute('aria-label', marker.title);
      marker.dataset.start = row.start_s;
      marker.dataset.end = row.end_s;
      const label = document.createElement('span');
      label.textContent = row.label;
      marker.append(label);
      marker.onclick = () => select(row);
      markers.append(marker);
    }
    timeline.replaceChildren(markers);
    duplex.layoutAnnotations();
    picker.onchange = () => {
      const selected = annotations.find((row) => row.id === picker.value);
      if (selected) select(selected);
      else updateSelection();
    };
    $('recordingSummary').textContent = `${annotations.length} 条标注 · ${Number(record.duration_s.toFixed(2))} 秒`;
  }
  function refresh(afterWrite = false) {
    const tags = $('tags').value.split(/[,，]/).map((value) => value.trim()).filter(Boolean);
    const status = $('status').value;
    let rows = data.findings.map(effective).filter((row) => fileFilter.matches(records.get(row.recording_id)) &&
      matchesScope(row) && (!status || row.status === status));
    $('tagModeField').hidden = tags.length < 2;
    const matchAll = tags.length > 1 && $('tagMode').value === 'all';
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
    if (formDirty || (saving && afterWrite !== true)) {
      if (!saving) message('筛选已更新；请先保存或放弃当前表单修改。');
      return;
    }
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
    const available = [...$('channel').options].filter(option => !option.disabled);
    $('channel').closest('label').hidden = available.length < 2;
    if (!sources[$('channel').value]) $('channel').value = available[0]?.value || '';
    playback.setSource(sources[$('channel').value] || '', keepTime);
    $('seek').disabled = !sources[$('channel').value];
    waveform.syncChannel($('channel').value);
    const hint = currentRecord.audio_error || (!sources.both ? '本报告未附带音频，可查看波形与标注。' : '');
    $('audioHint').textContent = hint;
    $('audioHint').hidden = !hint;
  }
  function showAudio(record, start, end, showEvidence) {
    const sameRecording = currentRecord?.id === record.id;
    currentRecord = record;
    playback.pause();
    if (sameRecording) {
      waveform.setEvidenceRange(start, end, showEvidence);
      duplex.setEvidence(start, end, showEvidence);
      playback.setRange(start, end);
      playback.applyRange(true);
      return;
    }
    $('seek').max = record.duration_s;
    $('seek').value = start;
    $('time').textContent = start.toFixed(2) + ' s';
    $('playbackDuration').textContent = record.duration_s.toFixed(2) + ' s';
    $('recordingReplayHeading').textContent = record.channels === 2 ? '双轨录音回放' : '单轨录音回放';
    $('start').value = start; $('end').value = end;
    const roleNames = {user:'用户上行', assistant:'助手下行'};
    for (const [index,id] of ['leftTitle','rightTitle'].entries()) {
      const role = roleNames[record.channel_roles?.[index]];
      const name = role || `声道 ${index}`;
      $(index === 0 ? 'duplexLeft' : 'duplexRight').textContent = name;
      $(id).querySelector('.role-name').textContent = name;
      $(id).querySelector('.role-meta').textContent = role ? `声道 ${index} · 按检测配置` : '角色未指定';
      $('channel').options[index + 1].textContent = role ? '只听' + role : (index === 0 ? '只听左轨' : '只听右轨');
    }
    setAudio();
    const hasDuplex = duplex.load(record.waveform, record.duration_s);
    if (!hasDuplex) $('precisionWave').open = true;
    duplex.setEvidence(start, end, showEvidence);
    updateListeningRange(start, end);
    waveform.load({record, op:{at_s:start, observed_until_s:end}, showEvidence});
  }
  function select(row) {
    if (current?.id === row.id) return;
    if (formDirty || saving) {
      message(saving ? '正在保存，稍后可切换标注。' : '请先保存或放弃当前表单修改。');
      updateSelection();
      return;
    }
    selectRecording(records.get(row.recording_id), row);
  }
  function syncCustomLabel() {
    const custom = $('reviewLabel').value === '';
    $('customReviewLabel').hidden = !custom;
    $('customReviewLabel').disabled = !custom;
  }
  function fillReviewLabel(label) {
    const picker = $('reviewLabel');
    if (![...picker.options].some(option => option.value === label)) {
      picker.add(new Option(label), picker.options.length - 1);
    }
    picker.value = label;
    $('customReviewLabel').value = '';
    syncCustomLabel();
  }
  function selectRecording(record, row, refreshAnnotations = false) {
    const changed = currentRecord?.id !== record.id;
    const previous = current;
    current = row; $('detail').hidden = false;
    $('name').textContent = filename(record.input);
    $('recordingNote').hidden = true;
    if (changed || refreshAnnotations) renderAnnotations(record);
    $('selectedTag').textContent = row?.label || '';
    $('annotationDetail').hidden = !row;
    $('reviewForm').hidden = !row || Boolean(row.draft);
    if (row) {
      $('annotationName').textContent = `${row.label} · ${rangeLabel(row)}`;
      $('identity').textContent = row.draft
        ? `${row.definition_id}@${row.version} · ${row.batch_id} · 漏检草稿`
        : `${row.definition_id}@${row.version} · ${row.batch_id} · 复核版本 ${row.revision}`;
      $('reason').textContent = row.reason;
      $('reason').hidden = !row.reason || row.reason === row.label;
      $('annotationEvidence').hidden = Boolean(row.draft);
      if (!row.draft) {
        $('evidence').textContent = JSON.stringify(row.automatic, null, 2);
        fillReviewLabel(row.label);
        $('reviewStart').value = row.start_s; $('reviewEnd').value = row.end_s;
        $('comment').value = row.comment;
        $('reviewForm').querySelector('[value="pending"]').hidden = row.status === 'pending';
        $('reviewForm').querySelector('[value="false_positive"]').hidden = row.status === 'false_positive';
      }
    }
    const start = row?.start_s ?? 0, end = row?.end_s ?? record.duration_s;
    if (changed || previous?.id !== row?.id || previous?.start_s !== row?.start_s || previous?.end_s !== row?.end_s) {
      showAudio(record, start, end, Boolean(row));
    }
    const candidates = data.evaluations.filter((item) => item.recording_id === record.id &&
      (row ? item.batch_id === row.batch_id && item.config_hash === row.config_hash : matchesScope(item)));
    $('addRecordingAnnotation').hidden = !candidates.length;
    $('addRecordingAnnotation').onclick = () => {
      if (saving) return;
      const evaluation = candidates.length === 1 ? candidates[0] : null;
      $('manualPanel').open = true;
      $('manualEvaluation').value = evaluation ? String(data.evaluations.indexOf(evaluation)) : '';
      if (!$('manualLabel').value) $('manualLabel').value = singleRuleLabel(evaluation);
      $('manualStart').value = $('start').value;
      $('manualEnd').value = $('end').value;
      $('normalChecked').checked = false;
      $('manualForm').scrollIntoView({block:'start'});
      $(evaluation ? 'manualLabel' : 'manualEvaluation').focus({preventScroll:true});
    };
    updateSelection();
    showProgress();
  }
  function validateRange(start,end,duration) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > duration) throw Error('片段必须在录音范围内，终点大于起点。');
  }
  function required(value,name,max) {
    if (typeof value !== 'string' || !value.trim() || value.length > max) throw Error(`${name}不能为空，且最多 ${max} 字符。`);
  }
  function validateComment(value) {
    if (typeof value !== 'string' || value.length > 4000) throw Error('备注格式错误。');
  }
  function changesOriginal(item, automatic) {
    return item.label !== automatic.label || item.start_s !== automatic.start_s || item.end_s !== automatic.end_s;
  }
  function validateReview(item) {
    const original = originals.get(item.finding_id);
    if (!original || item.expected_revision !== original.revision) throw Error('草稿与此报告的检测身份或复核版本不匹配，请使用原报告或重新生成报告。');
    if (!Object.hasOwn(names,item.status)) throw Error('未知复核状态。');
    required(item.reviewer,'复核人',100); required(item.label,'标签',100);
    validateComment(item.comment);
    validateRange(item.start_s,item.end_s,original.duration_s);
    if (changesOriginal(item, original.automatic) && item.status !== 'corrected') throw Error('复核文件的修正状态与标签或片段不一致。');
  }
  function validateManual(item) {
    const evaluation = data.evaluations.find((row) => row.batch_id === item.batch_id && row.recording_id === item.recording_id && row.config_hash === item.config_hash);
    if (!evaluation || typeof item.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(item.id) || originals.has(item.id)) throw Error('漏检补标的身份不匹配。');
    required(item.label,'标签',100); required(item.reviewer,'复核人',100);
    validateComment(item.comment);
    validateRange(item.start_s,item.end_s,evaluation.duration_s);
  }
  function renderManual() {
    $('manualQueue').replaceChildren();
    $('manualQueue').hidden = !manual.size;
    $('manualCount').textContent = manual.size ? manual.size + ' 条草稿' : '';
    $('undoManualRemoval').hidden = !removedManual;
    for (const item of manual.values()) {
      const li = document.createElement('li'), remove = document.createElement('button');
      li.textContent = `${item.label} · ${item.start_s}–${item.end_s} 秒 · ${item.comment} `;
      remove.textContent = '移除草稿';
      remove.type = 'button';
      remove.onclick = () => {
        if (saving) return;
        removedManual = item;
        manual.delete(item.id);
        persist('manualFeedback', '已移除草稿，可撤销。');
        renderManual(); refresh();
        $('undoManualRemoval').focus();
      };
      li.append(remove); $('manualQueue').append(li);
    }
  }
  $('undoManualRemoval').onclick = () => {
    if (saving || !removedManual) return;
    if (!manual.has(removedManual.id) && !originals.has(removedManual.id)) manual.set(removedManual.id, removedManual);
    removedManual = null;
    persist('manualFeedback', '草稿已恢复。');
    renderManual(); refresh();
    $('manualPanel').querySelector('summary').focus();
  };
  function persist(location = 'reportFeedback', success = '已保存到本地草稿；导出并导入检测库后才成为正式复核记录。') {
    notExported = true;
    try { localStorage.setItem(key,JSON.stringify(packet())); message(success, location); }
    catch { message('浏览器无法保存草稿，请立即导出复核文件以免丢失。', location); }
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
    const action = event.submitter?.value || 'save';
    const original = current.automatic;
    const label = $('reviewLabel').value || $('customReviewLabel').value;
    const bounds = action === 'save'
      ? {label, start_s:$('reviewStart').valueAsNumber, end_s:$('reviewEnd').valueAsNumber}
      : {label:original.label, start_s:original.start_s, end_s:original.end_s};
    let status = action;
    if (action === 'save') status = changesOriginal(bounds, original) ? 'corrected' : 'confirmed';
    const item = {finding_id:current.id, expected_revision:current.revision, status, ...bounds,
      reviewer:localReviewer, comment:$('comment').value};
    if (saving) return;
    await saveWithFeedback(async () => {
      validateReview(item);
      if (session) {
        // Restore the unsaved state if the database declines this revision.
        const wasDirty = formDirty;
        formDirty = false;
        try {
          await submitPacket({schema_version:'1.0',library_id:data.library_id,reviews:[item],manual:[]}, 'reviewFeedback');
        } catch (error) {
          formDirty = wasDirty;
          throw error;
        }
      } else {
        decisions.set(item.finding_id,item);
        formDirty = false;
        refresh(true);
        persist('reviewFeedback');
      }
    }, 'reviewFeedback', $('reviewForm'));
  };
  $('reviewLabel').onchange = syncCustomLabel;
  for (const id of ['reviewLabel','customReviewLabel','reviewStart','reviewEnd','comment']) $(id).oninput = () => { formDirty=true; showProgress(); };
  $('discard').onclick = () => { formDirty=false; refresh(); message('已放弃表单修改。', 'reviewFeedback'); };
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
    if (formDirty || saving) { message('请先完成当前复核，再导出。'); return; }
    download(packet(),'detection-review.json'); notExported=false;
    message('已导出复核文件；使用 detect review-import 导入检测库，再生成新报告。');
  };
  $('importDraft').onchange = async () => {
    const input = $('importDraft'), file = input.files[0];
    input.value = '';
    if (!file) return;
    try {
      if (file.size > 16*1024*1024) throw Error('复核文件超过 16 MiB。');
      const value = JSON.parse(await file.text());
      if (formDirty || saving) throw Error('请先保存或放弃当前表单修改，再导入草稿。');
      importPacket(value);
      persist();
    } catch(error) { message(error.message); }
  };
  $('listenManual').onclick = () => {
    if (formDirty || saving) { message('请先完成当前复核。'); return; }
    const item = manualEvaluation();
    if (!item) { message('请先选择要试听的检测记录。', 'manualFeedback'); return; }
    selectRecording(records.get(item.recording_id), null);
    playback.setRange(0, item.duration_s); playback.applyRange(true);
    $('recordingNote').textContent = `${item.batch_id} · ${item.definition_id}@${item.version} · ${item.status}${item.note ? ' · ' + item.note : ''}`;
    $('recordingNote').hidden = false;
    $('manualStart').value=0; $('manualEnd').value=item.duration_s;
    $('detail').scrollIntoView({block:'start'});
  };
  for (const id of ['manualEvaluation','manualLabel','manualStart','manualEnd','manualComment']) {
    $(id).addEventListener('input', () => { $('normalChecked').checked=false; });
    $(id).addEventListener('change', () => { $('normalChecked').checked=false; });
  }
  $('manualForm').onsubmit = async (event) => {
    event.preventDefault(); const evaluation = manualEvaluation();
    if (!evaluation) { message('没有可补标的检测记录。'); return; }
    const item = {id:crypto.randomUUID(), batch_id:evaluation.batch_id, recording_id:evaluation.recording_id,
      config_hash:evaluation.config_hash, label:$('manualLabel').value, start_s:$('manualStart').valueAsNumber,
      end_s:$('manualEnd').valueAsNumber, reviewer:localReviewer, comment:$('manualComment').value};
    await saveWithFeedback(async () => {
      validateManual(item);
      if (session) {
        await submitPacket({schema_version:'1.0',library_id:data.library_id,reviews:[],manual:[item]}, 'manualFeedback');
      } else {
        manual.set(item.id,item);
        renderManual(); refresh(true);
        persist('manualFeedback');
      }
    }, 'manualFeedback', $('manualForm'));
  };
  window.addEventListener('beforeunload',(event) => { if (formDirty || notExported || saving) { event.preventDefault(); event.returnValue=''; } });
  refresh();
  try { const saved=localStorage.getItem(key); if (saved) importPacket(JSON.parse(saved)); }
  catch(error) { message('本地草稿未加载：' + error.message); }
})();
