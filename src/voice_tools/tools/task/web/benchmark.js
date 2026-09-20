/* Chinese timing authoring and evidence views. Never initiate calls or upload audio. */
window.VoiceBenchmark = (() => {
  'use strict';
  const data = window.VT_DATA;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const copy = value => JSON.parse(JSON.stringify(value));
  const titles = {first_audio:'接通后首声', response:'用户语音后新声音', barge_stop:'重叠后的停声', longest_silence:'最长连续静音'};
  const statuses = {measured:'已测量', invalid:'场景无效', insufficient_evidence:'证据不足', completed:'分析完成', findings:'发现超限', passed:'符合声学阈值', failed:'超出声学阈值', not_configured:'未配置阈值'};
  let kind = 'interrupt';
  let draft = data.benchmark_templates ? copy(data.benchmark_templates[kind].scenario) : null;
  let entries = [];
  let hooks = {};

  function input(key, label, value, type = 'text') {
    return `<div class="field"><label for="bench-${key}">${escape(label)}</label><input id="bench-${key}" data-bench-field="${key}" type="${type}" ${type === 'number' ? 'step="any"' : ''} value="${escape(value)}"></div>`;
  }

  function author() {
    const benchmark = draft.benchmark;
    const trigger = draft.steps.find(s => s.action === 'wait_audio');
    const playback = draft.steps.find(s => s.action === 'play');
    const selected = data.benchmark_templates[kind];
    return `<section class="panel"><h2>中文电话时序用例</h2>
      <p class="help">先准备获授权真人录音，再由 CLI 或执行机拨测。这里仅编辑和导出文件。</p>
      <div class="form-grid"><div class="field"><label for="bench-kind">场景模板</label><select id="bench-kind">${Object.entries(data.benchmark_templates).map(([id, item]) => `<option value="${id}" ${id === kind ? 'selected' : ''}>${escape(item.title)}</option>`).join('')}</select></div>
      ${input('case_id','用例标识',benchmark.case_id)}${input('target','SIP 测试端点',draft.target_uri)}${input('tags','分组标签，用逗号分隔',benchmark.tags.join(', '))}</div>
      <div class="info">${playback ? '待补素材：' + escape(selected.phrases.join(' / ')) : '此用例保持静默，等待机器人开场。'} 人工确认预期行为；声音活动不等于语义理解。</div>
      ${playback ? `<div class="form-grid">${input('file','源录音相对路径',playback.file)}<div class="field"><label for="bench-file-picker">选择录音以填写文件名（不上传）</label><input id="bench-file-picker" type="file" accept=".wav"></div></div>
      <div class="field"><label for="bench-speech">人工确认的源录音语音区间（可空；秒）</label><textarea id="bench-speech" data-bench-field="speech" placeholder='[{"start_s":0.12,"end_s":0.8}]'>${playback.speech ? escape(JSON.stringify(playback.speech)) : ''}</textarea></div>` : ''}
      <h3>接收声音触发</h3><div class="form-grid"><div class="field"><label for="bench-trigger">等待状态</label><select id="bench-trigger" data-bench-field="state"><option value="active" ${trigger.state === 'active' ? 'selected' : ''}>出现声音</option><option value="silent" ${trigger.state === 'silent' ? 'selected' : ''}>保持安静</option></select></div>
      ${input('duration_ms','状态持续时间（毫秒）',trigger.duration_ms,'number')}${input('timeout_s','等待上限（秒）',trigger.timeout_s,'number')}</div>
      <h3>测量参数</h3><div class="form-grid"><div class="field"><label for="bench-backend">活动检测</label><select id="bench-backend" data-bench-field="backend"><option value="webrtcvad" ${benchmark.detector.backend === 'webrtcvad' ? 'selected' : ''}>WebRTC VAD＋能量</option><option value="energy" ${benchmark.detector.backend === 'energy' ? 'selected' : ''}>能量（回归测试）</option></select></div>
      ${input('threshold_db','能量门限（dBFS）',benchmark.detector.threshold_db,'number')}${input('minimum_ms','最短活动（毫秒）',benchmark.detector.minimum_ms,'number')}${input('join_gap_ms','合并短停顿（毫秒）',benchmark.detector.join_gap_ms,'number')}${input('stop_silence_ms','确认停声的静音（毫秒）',benchmark.detector.stop_silence_ms,'number')}</div>
      <div class="field"><label for="bench-windows">测量窗口（运行时钟秒；空数组表示全部接收证据）</label><textarea id="bench-windows" data-bench-field="windows">${escape(JSON.stringify(benchmark.windows))}</textarea></div>
      <h3>声学阈值（留空表示不判断合格）</h3><div class="form-grid">${input('first_audio_max_ms','首声上限（毫秒）',benchmark.expectations.first_audio_max_ms ?? '', 'number')}${input('response_max_ms','新声音响应上限（毫秒）',benchmark.expectations.response_max_ms ?? '', 'number')}${input('stop_max_ms','停声上限（毫秒）',benchmark.expectations.stop_max_ms ?? '', 'number')}
      <div class="field"><label for="bench-expect">插话预期</label><select id="bench-expect" data-bench-field="expect_interrupt">${[['','不判断'],['true','应停声（声学）'],['false','应继续（人工复核）']].map(([value,label]) => `<option value="${value}" ${String(benchmark.expectations.expect_interrupt ?? '') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></div></div>
      <div class="toolbar"><button data-bench-export="case" class="primary">下载场景 JSON</button><button data-bench-export="task">下载对应任务</button><button data-bench-export="queue">下载三次重复队列</button></div>
      <p class="help">将场景放在 cases/${kind}.json，录音放在 cases/audio/。任务包会收集引用的录音；CLI 再检查格式、标注和摘要。队列文件也放在 cases/，用 sip batch 执行。</p>
      <details><summary>场景预览（SIP 1.1 · 需要 voice_tools ≥ 0.13.1）</summary><pre id="bench-preview">${escape(JSON.stringify(draft,null,2))}</pre></details></section>`;
  }

  function task() {
    return {schema_version:'1.0', id:'zh-timing-' + kind, title:'中文时序 · ' + data.benchmark_templates[kind].title,
      inputs:{scenario:`cases/${kind}.json`}, steps:[
        {id:'validate',tool:'sip',action:'validate',params:{scenario:{input:'scenario'}}},
        {id:'call',tool:'sip',action:'run',environment:'lab',depends_on:['validate'],params:{scenario:{input:'scenario'}}},
        {id:'timing',tool:'benchmark',action:'analyze',depends_on:['call'],params:{run_dir:{step:'call',path:'data'}}}]};
  }

  function edit(field, value) {
    const trigger = draft.steps.find(s => s.action === 'wait_audio');
    const playback = draft.steps.find(s => s.action === 'play');
    const benchmark = draft.benchmark;
    if (field === 'target') draft.target_uri = value;
    else if (field === 'case_id') benchmark.case_id = value;
    else if (field === 'tags') benchmark.tags = value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    else if (field === 'file') playback.file = value;
    else if (field === 'speech') {
      if (!value.trim()) delete playback.speech;
      else {
        const spans = JSON.parse(value);
        if (!Array.isArray(spans) || spans.some(s => !Number.isFinite(s.start_s) || !Number.isFinite(s.end_s) || s.start_s < 0 || s.end_s <= s.start_s)) throw Error('语音区间需为有效的 start_s / end_s 数组');
        playback.speech = spans;
      }
    } else if (field === 'windows') {
      const windows = JSON.parse(value || '[]');
      if (!Array.isArray(windows) || windows.some(w => !w.id || !Number.isFinite(w.start_s) || !Number.isFinite(w.end_s) || w.start_s < 0 || w.end_s <= w.start_s)) throw Error('窗口需为 id、start_s、end_s 数组');
      benchmark.windows = windows;
    } else if (field === 'expect_interrupt') {
      if (value === '') delete benchmark.expectations.expect_interrupt;
      else benchmark.expectations.expect_interrupt = value === 'true';
    } else if (field === 'state') trigger.state = value;
    else if (field === 'backend') benchmark.detector.backend = value;
    else if (field in benchmark.detector || ['duration_ms','timeout_s','first_audio_max_ms','response_max_ms','stop_max_ms'].includes(field)) {
      if (field.endsWith('_max_ms') && value === '') delete benchmark.expectations[field];
      else {
        const number = Number(value);
        if (!Number.isFinite(number) || value === '') throw Error('请填写有限数字');
        if (field in benchmark.detector) benchmark.detector[field] = number;
        else if (field.endsWith('_max_ms')) benchmark.expectations[field] = number;
        else trigger[field] = number;
      }
    }
  }

  function timeline(entry, index) {
    const report = entry.report;
    const end = Math.max(report.observed_end_s || 1, ...Object.values(report.activity || {}).flat().map(s => s.end_s));
    return `<div class="bench-timeline" role="group" aria-label="双向声音时间轴">${['rx','tx'].map(direction => `<div class="bench-track"><b>${direction === 'rx' ? '接收到的远端音频' : '本机发送素材'}</b><div class="bench-bars">${(report.activity?.[direction] || []).map((s,i) => `<button class="bench-span ${direction}" style="left:${Math.max(0,s.start_s/end*100)}%;width:${Math.max(.4,(s.end_s-s.start_s)/end*100)}%" data-bench-span="${index}:${direction}:${i}" aria-label="${direction} ${s.start_s.toFixed(2)}至${s.end_s.toFixed(2)}秒" title="${s.start_s.toFixed(2)}–${s.end_s.toFixed(2)}秒"></button>`).join('')}</div></div>`).join('')}<small>0 → ${end.toFixed(2)} 秒（运行时钟）；点击声音片段定位原始 PCM。</small></div>`;
  }

  function markers(entry, index) {
    return `<div class="bench-markers" aria-label="首声与插话标记">${entry.report.metrics.flatMap((metric, i) => {
      const labels = metric.kind === 'barge_stop' ? [['at_s','audio_at_s','插话'],['end_s','audio_end_at_s',metric.status === 'measured' ? '停声' : '观察结束'],['resumed_at_s','resumed_audio_at_s','再次出声']] : metric.kind === 'first_audio' ? [['at_s','audio_at_s','首声']] : [];
      return labels.filter(([field,audioField]) => metric[field] != null && metric[audioField] != null).map(([field,audioField,label]) => `<button data-bench-marker="${index}:${i}:${audioField}" ${entry.audio?.rx ? '' : 'disabled'}>${label} ${metric[field].toFixed(3)}s</button>`);
    }).join('')}</div>`;
  }

  function review(step) {
    entries = step.benchmarks || [];
    const summaries = (step.benchmark_summaries || []).map(summary => {
      const counts = summary.counts;
      const rows = Object.entries(summary.metrics || {}).map(([kind, value]) => `<tr><td>${escape(titles[kind] || kind)}</td><td>${escape(value.n)}</td><td>${escape(value.p50_ms)}</td><td>${escape(value.p95_ms)}</td><td>${escape(value.p99_ms)}</td></tr>`).join('');
      return `<section class="panel"><h2>批量时序汇总 · ${escape(summary.calls)} 次</h2><p>有效 ${escape(counts.valid)} · 失败 ${escape(counts.failed)} · 无效 ${escape(counts.invalid)} · 证据不足 ${escape(counts.insufficient_evidence)}</p><div class="bench-table"><table><thead><tr><th>指标</th><th>有效测量数</th><th>P50 ms</th><th>P95 ms</th><th>P99 ms</th></tr></thead><tbody>${rows}</tbody></table></div><p class="help">分位数包含失败用例中的有效测量；小样本尾部分位数不稳定。原始汇总文件保留分组数据。</p><details><summary>标签分组</summary><pre>${escape(JSON.stringify(summary.groups || {},null,2))}</pre></details></section>`;
    }).join('');
    if (!entries.length) return summaries || '<section class="panel"><h2>时序评测</h2><p>此步骤没有时序结果。使用 SIP 1.1 时序场景，或对已保存的媒体观测运行 benchmark analyze。</p></section>';
    const truncated = step.benchmarks_truncated ? '<p class="warning">仅展示前 200 份时序报告，完整结果保留在证据文件中。</p>' : '';
    return summaries + truncated + entries.map((entry,index) => {
      const report = entry.report;
      return `<section class="panel"><h2>${escape(report.case_id)} <span class="pill">${escape(statuses[report.status] || report.status)}</span></h2>
        <p class="help">${escape((report.tags || []).join(' · '))} · 20ms 分析帧 · 媒体桥观测</p>${timeline(entry,index)}${markers(entry,index)}
        ${(report.issues || []).length ? `<div class="info">证据问题：${escape(report.issues.join('；'))}</div>` : ''}
        <div class="bench-table"><table><thead><tr><th>指标</th><th>数值</th><th>证据</th><th>阈值结果</th></tr></thead><tbody>${(report.metrics || []).map((m,i) => `<tr><td><button data-bench-seek="${index}:${i}" ${m.audio_at_s == null || !entry.audio?.rx ? 'disabled' : ''}>${escape(titles[m.kind] || m.kind)}</button></td><td>${m.value_ms == null ? '—' : escape(m.value_ms) + ' ms'}${m.lower_bound_ms != null ? '<br>观察下界 '+escape(m.lower_bound_ms)+' ms' : ''}</td><td>${escape(statuses[m.status] || m.status)}${m.reason ? '<br>'+escape(m.reason) : ''}</td><td>${escape(statuses[m.verdict] || m.verdict)}</td></tr>`).join('')}</tbody></table></div>
        <p class="help">执行状态：${escape(report.execution_status)}。声学停顿不自动证明语义打断。接收音频已经过抖动缓冲；本机发送记录不证明远端收到。</p></section>`;
    }).join('');
  }

  document.addEventListener('change', event => {
    const target = event.target;
    try {
      if (target.id === 'bench-kind') {
        kind = target.value;
        draft = copy(data.benchmark_templates[kind].scenario);
        hooks.render();
      } else if (target.id === 'bench-file-picker' && target.files[0]) {
        edit('file', 'audio/' + target.files[0].name);
        hooks.render();
      } else if (target.dataset.benchField) {
        edit(target.dataset.benchField, target.value);
        document.querySelector('#bench-preview').textContent = JSON.stringify(draft,null,2);
        hooks.notice('场景已更新，尚未执行。');
      }
    } catch (error) { hooks.notice(error.message); }
  });

  document.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target) return;
    try {
      if (target.dataset.benchExport) {
        // Revalidate all visible edits, including an invalid field that has not blurred.
        for (const field of document.querySelectorAll('[data-bench-field]')) edit(field.dataset.benchField, field.value);
        if (target.dataset.benchExport === 'case') hooks.download(draft,kind+'.json');
        else if (target.dataset.benchExport === 'task') hooks.download(task(),'task.json');
        else hooks.download({schema_version:'1.0',kind:'sip_batch',concurrency:1,jobs:[{name:draft.benchmark.case_id,repeat:3,scenario:draft}]},'queue.json');
      } else if (target.dataset.benchSeek) {
        const [entryIndex,metricIndex] = target.dataset.benchSeek.split(':').map(Number);
        const entry = entries[entryIndex];
        hooks.openAudio(entry.audio.rx, entry.report.metrics[metricIndex].audio_at_s);
      } else if (target.dataset.benchMarker) {
        const [entryIndex, metricIndex, field] = target.dataset.benchMarker.split(':');
        const entry = entries[Number(entryIndex)];
        hooks.openAudio(entry.audio.rx, entry.report.metrics[Number(metricIndex)][field]);
      } else if (target.dataset.benchSpan) {
        const [entryIndex,direction,spanIndex] = target.dataset.benchSpan.split(':');
        const entry = entries[Number(entryIndex)];
        const audio = entry.audio[direction];
        if (!audio) throw Error('该音频未包含在结果包中');
        hooks.openAudio(audio, entry.report.activity[direction][Number(spanIndex)].audio_start_s);
      }
    } catch (error) { hooks.notice(error.message); }
  });

  return {author, review, bind: value => { hooks = value; }};
})();
