'use strict';
(() => {
  const data = JSON.parse(document.getElementById('reviewData').textContent);
  const $ = (id) => document.getElementById(id);
  const names = {AUTO_PASS:'自动通过', AUTO_ANOMALY:'自动异常', NEEDS_REVIEW:'待人工复核'};
  const labels = new Map();
  const byId = new Map(data.records.map((record) => [record.assessment_id, record]));
  let current = null, visible = [], formDirty = false, dirty = false;
  const filter = createReviewFileFilter(data.records, (record) => record.assessment_id, refresh);
  const message = (text) => { $('message').textContent = text; };
  const waveform = createReviewWaveform($('player'), (start, end) => playback.setRange(start, end), message,
    {focusSelection: true});
  const playback = createReviewPlayback($('player'), {
    duration: () => current?.result.duration_s,
    seek: (value) => waveform.setTime(value),
    rangeChanged: (start, end) => waveform.syncRange(start, end),
    error: message
  });
  const filename = (record) => record.input.split(/[\\/]/).pop();
  const fixed = (number) => Number.isFinite(number) ? number.toFixed(2) : '未知';

  for (const [title, value] of [
    ['录音总数', data.summary.files], ['自动通过', data.summary.decisions.AUTO_PASS],
    ['自动异常', data.summary.decisions.AUTO_ANOMALY], ['人工队列（含抽检）', data.summary.review_recordings]
  ]) {
    const card = document.createElement('div'), count = document.createElement('strong');
    card.className = 'card'; card.textContent = title; count.textContent = String(value);
    card.append(count); $('totals').append(card);
  }

  function inQueue(record) {
    const mode = $('queueMode').value, result = record.result;
    if (mode === 'all') return true;
    if (mode === 'audit') return result.audit_selected;
    if (mode === 'review') {
      const label = labels.get(record.assessment_id);
      return result.review_required && (!label || label.decision === 'uncertain');
    }
    return result.decision === mode;
  }

  function renderQueue() {
    $('queue').replaceChildren();
    for (const record of visible) {
      const button = document.createElement('button'), small = document.createElement('small');
      button.type = 'button'; button.textContent = filename(record); button.title = record.input;
      button.setAttribute('aria-current', String(current === record));
      const label = labels.get(record.assessment_id);
      const parent = record.input.split(/[\\/]/).slice(-2,-1)[0];
      small.textContent = `${parent ? parent + ' · ' : ''}${names[record.result.decision]}${record.result.audit_selected ? ' · 抽检' : ''}` +
        (label ? ` · ${label.decision === 'uncertain' ? '人工仍不确定' : '已填写'}` : '');
      button.append(small); button.onclick = () => select(record); $('queue').append(button);
    }
    if (!visible.length) {
      const empty = document.createElement('p');
      empty.textContent = '当前范围没有待处理录音，可清空检索或切换到“全部录音”。'; $('queue').append(empty);
    }
    $('progress').textContent = `${data.records.length} 通录音 · ${visible.length} 通符合筛选 · ${labels.size} 通已填写` +
      (dirty || formDirty ? ' · 有未导出的修改' : ' · 无未导出的修改');
  }

  function refresh() {
    visible = data.records.filter((record) => filter.matches(record) && inQueue(record));
    renderQueue();
    if (formDirty) {
      if (!visible.includes(current)) message('筛选已更新；当前表单尚未记下，请先保存或放弃修改。');
      return;
    }
    if (visible.length && !visible.includes(current)) select(visible[0]);
    else if (!visible.length) { playback.pause(); current = null; $('detail').hidden = true; }
  }

  function setAudio(keepTime = true) {
    const sources = current?.playback_sources || {};
    if (!sources[$('channel').value]) $('channel').value = sources.both ? 'both' : sources.left ? 'left' : 'both';
    for (const option of $('channel').options) option.disabled = !sources[option.value];
    const source = sources[$('channel').value] || '';
    playback.setSource(source, keepTime);
    waveform.syncChannel($('channel').value);
    $('audioHint').textContent = source ? '点击波形定位，或选择下方建议范围试听；图形保持原始双轨，人工结论仍按整通填写。' : '此报告未附带试听音频，仍可查看已保存的波形。分析时增加 --include-audio 可生成试听副本。';
  }

  function select(record) {
    if (formDirty) { message('请先记下整通结论或放弃表单修改。'); return; }
    current = record; $('detail').hidden = false; message('');
    playback.pause();
    const result = record.result;
    const duration = result.duration_s || 0;
    // Imported timestamps may exceed duration by the validator's rounding tolerance.
    const clampRange = ([start, end]) => [Math.min(start, duration), Math.min(end, duration)];
    const windows = result.review_windows.map(clampRange).filter(([start, end]) => end > start);
    $('name').textContent = filename(record);
    $('decision').textContent = names[result.decision] + (result.audit_selected ? ' · 已选入抽检' : '');
    $('metadata').textContent = `${fixed(result.duration_s)} 秒 · ${result.turns.length} 个自动分析轮次 · ${result.findings.length} 处证据`;
    $('blockers').textContent = (result.blockers || []).join('\n');
    $('blockers').hidden = !result.blockers?.length;
    $('windows').replaceChildren();
    for (const [start, end] of windows) {
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = `定位 ${fixed(start)}–${fixed(end)} 秒`;
      button.onclick = () => {
        playback.pause(); playback.setRange(start, end);
        waveform.setEvidenceRange(start, end); playback.applyRange(true);
        if (!$('player').getAttribute('src')) { message('此报告未附带试听音频。'); return; }
        playback.toggle();
      };
      $('windows').append(button);
    }
    $('evidence').replaceChildren();
    const lines = [`工程检查：${result.engineering.status === 'completed' ? '已完成' : '未完整完成'}`,
      `语音模型：${result.model.status === 'completed' ? '已完成' : result.model.error || '未完成'}`,
      ...result.findings.map((item) => `${fixed(item.start_s)}–${fixed(item.end_s)} 秒：${item.reason}（${item.decision === 'ANOMALY' ? '自动异常' : '需核对'}）`)];
    if (!result.findings.length) lines.push(result.decision === 'AUTO_PASS'
      ? '当前声学与应答时序检查已完成，未发现超出策略阈值的问题。'
      : '没有可定位的异常片段，请先核对上方待复核原因。');
    for (const line of lines) { const p = document.createElement('p'); p.textContent = line; $('evidence').append(p); }
    $('raw').textContent = JSON.stringify(result, null, 2);
    const label = labels.get(record.assessment_id) || {};
    $('humanDecision').value = label.decision || ''; $('notes').value = label.notes || '';
    if (label.reviewer) $('reviewer').value = label.reviewer;
    const mono = record.waveform?.channels.length === 1;
    const rolesVerified = result.channel_verified === true && [0, 1].includes(result.system_channel);
    for (const [index, id] of ['leftTitle', 'rightTitle'].entries()) {
      let role = '角色未核实', verification = '待核实';
      if (mono) { role = '单声道'; verification = '无法区分双方'; }
      else if (rolesVerified) { role = index === result.system_channel ? 'AI' : '用户'; verification = '已核实'; }
      $(id).querySelector('.role-name').textContent = role;
      $(id).querySelector('.role-meta').textContent = `${index === 0 ? '左' : '右'}声道\n${verification}`;
    }
    const range = windows[0] || clampRange(result.checked_range || [0, duration]);
    const [start, end] = range[1] > range[0] ? range : [0, duration];
    $('start').value = start; $('end').value = end;
    for (const id of ['start', 'end']) $(id).removeAttribute('aria-invalid');
    $('seek').max = duration; $('seek').value = start;
    $('time').textContent = `${fixed(start)} / ${fixed(result.duration_s)} 秒`;
    setAudio(false);
    waveform.load({record, op: {at_s: start, observed_until_s: end}});
    renderQueue();
  }

  for (const id of ['directoryFilter','fileFilter','queueMode']) $(id).onchange = () => {
    if (id === 'directoryFilter') filter.updateFiles(); refresh();
  };
  $('channel').onchange = () => setAudio();
  $('player').onerror = () => { if ($('player').getAttribute('src')) message('试听音频无法读取，请核对报告音频副本。'); };
  for (const id of ['humanDecision','reviewer','notes']) $(id).oninput = () => { formDirty = true; renderQueue(); };
  function baseRow(record) {
    return {schema_version:'1.0',assessment_id:record.assessment_id,audio_sha256:record.audio_sha256,
      automatic_decision:record.result.decision};
  }
  $('reviewForm').onsubmit = (event) => {
    event.preventDefault(); if (!current) return;
    if (!$('humanDecision').value || !$('reviewer').value.trim()) { message('请填写人工判断和复核人。'); return; }
    labels.set(current.assessment_id, {...baseRow(current),decision:$('humanDecision').value,
      reviewer:$('reviewer').value.trim(),reviewed_at:new Date().toISOString(),notes:$('notes').value});
    dirty = true; formDirty = false; refresh(); message('已记下整通结论；离开前请导出 CSV。');
  };
  $('discard').onclick = () => {
    formDirty = false; refresh(); if (current) select(current); message('已放弃未记下的表单修改。');
  };

  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[\s]*[=+\-@']/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"','""') + '"';
  }
  $('export').onclick = () => {
    if (formDirty) { message('请先记下整通结论或放弃表单修改。'); return; }
    const rows = data.records.map((record) => labels.get(record.assessment_id) || baseRow(record));
    const csv = '\ufeff' + [data.fields, ...rows.map((row) => data.fields.map((field) => row[field]))]
      .map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
    const url = URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = 'recording-review.csv'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    dirty = false; renderQueue(); message('已发起下载；请保管整通人工标签。');
  };
  function parseCSV(text) {
    const rows = []; let row = [], cell = '', quoted = false;
    for (let i=0; i<text.length; i++) {
      const c = text[i];
      if (c === '"') { if (quoted && text[i+1] === '"') { cell += '"'; i++; } else quoted = !quoted; }
      else if (!quoted && (c === ',' || c === '\n')) { row.push(cell); cell = ''; if (c === '\n') { rows.push(row); row = []; } }
      else if (c !== '\r' || quoted) cell += c;
    }
    if (quoted) throw Error('CSV 引号未闭合');
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }
  $('import').onchange = async () => {
    try {
      if (formDirty) throw Error('请先记下当前表单或放弃修改。');
      const file = $('import').files[0]; if (!file) return;
      if (file.size > 16*1024*1024) throw Error('复核 CSV 超过 16 MiB');
      const [header,...rows] = parseCSV((await file.text()).replace(/^\ufeff/,''));
      if (JSON.stringify(header) !== JSON.stringify(data.fields)) throw Error('整通复核 CSV 列不匹配，不能混用逐片段标签。');
      const staged = new Map(), seen = new Set();
      for (const cells of rows) {
        if (cells.length !== header.length) throw Error('CSV 列数不匹配');
        const row = Object.fromEntries(header.map((field,index) => [field,cells[index]]));
        const record = byId.get(row.assessment_id);
        if (!record || seen.has(row.assessment_id)) throw Error('复核身份未知或重复');
        seen.add(row.assessment_id);
        if (Object.entries(baseRow(record)).some(([key,value]) => row[key] !== value)) throw Error('录音身份或自动结论与当前结果不匹配');
        for (const field of ['reviewer','notes']) if (/^'\s*['=+@-]/.test(row[field])) row[field] = row[field].slice(1);
        if (!row.decision) {
          if (row.reviewer || row.reviewed_at || row.notes) throw Error('存在未完整填写的人工标签');
          continue;
        }
        if (!['normal','abnormal','uncertain'].includes(row.decision) || !row.reviewer.trim() ||
            !/(Z|[+-]\d{2}:\d{2})$/.test(row.reviewed_at) || !Number.isFinite(Date.parse(row.reviewed_at)))
          throw Error('请填写有效判断、复核人和带时区时间');
        staged.set(row.assessment_id,row);
      }
      for (const [key,row] of staged) labels.set(key,row);
      dirty = true; refresh(); if (current) select(current); message(`已导入 ${staged.size} 通人工标签。`);
    } catch (error) { message(error.message); }
    finally { $('import').value = ''; }
  };
  window.addEventListener('beforeunload',(event) => { if (dirty || formDirty) { event.preventDefault(); event.returnValue = ''; } });
  refresh();
})();
