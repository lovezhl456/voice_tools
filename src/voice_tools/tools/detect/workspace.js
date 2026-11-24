'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const session = JSON.parse($('workspaceSession').textContent);
  let state = null, selectedSample = null, polling = false;
  function notice(text, control) {
    const status = $('notice');
    const region = control?.closest('.actions') || control?.closest('form') || control;
    if (region) region.after(status);
    VoiceMotion.feedback(status, text);
  }
  async function api(path, body) {
    const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST', headers:{
      'Content-Type':'application/json', 'X-Voice-Tools-Session':session.token
    }, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || '本机服务操作失败');
    return result;
  }
  function node(tag, text, className) {
    const element = document.createElement(tag); if (text !== undefined) element.textContent = text;
    if (className) element.className = className; return element;
  }
  function button(text, action) {
    const element = node('button', text); element.type = 'button'; element.onclick = () => perform(action); return element;
  }
  async function perform(action, control) {
    if (control?.disabled) return;
    if (control) {
      control.disabled = true;
      notice('正在处理…', control);
    }
    try { await action(); }
    catch(error) { notice(error.message, control); }
    finally {
      if (control) control.disabled = false;
      updateRunOptions();
    }
  }
  function updateRunOptions() {
    const trial = $('runMode').value === 'trial';
    $('trialFiles').hidden = !trial;
    $('trialSizeLabel').hidden = !trial || Boolean($('inputs').querySelector('input:checked'));
  }
  function selectedRules() { return [...$('definitions').querySelectorAll('input:checked')].map((input) => input.value); }
  function row(container, title, subtitle, action) {
    const item = node('div', undefined, 'row'), content = node('div');
    content.append(node('strong', title), node('small', subtitle)); item.append(content);
    if (action) item.append(action); container.append(item);
  }
  function options(id, values) {
    const control = $(id), previous = control.value; control.replaceChildren();
    for (const [value, label] of values) control.add(new Option(label, value));
    if (values.some(([value]) => value === previous)) control.value = previous;
  }
  function renderInputs() {
    const selected = new Set([...$('inputs').querySelectorAll('input:checked')].map((input) => input.value));
    $('inputs').replaceChildren();
    for (const item of state.inputs) {
      const label = node('label', undefined, 'check'), input = node('input');
      input.type = 'checkbox'; input.value = item.id; input.checked = selected.has(item.id);
      label.append(input, document.createTextNode(item.name)); $('inputs').append(label);
    }
    $('inputCount').textContent = `当前目录发现 ${state.inputs.length} 个 WAV 文件`;
  }
  function renderRules(initial) {
    const selected = new Set(initial ? state.settings.definitions : selectedRules());
    $('definitions').replaceChildren(node('legend', '本次使用的规则版本'));
    for (const item of state.definitions) {
      const ref = item.id + '@' + item.version, label = node('label', undefined, 'check'), input = node('input');
      input.type = 'checkbox'; input.value = ref; input.checked = selected.has(ref);
      label.append(input, document.createTextNode(`${item.name} · ${ref}`)); $('definitions').append(label);
    }
    if (!state.definitions.length) $('definitions').append(node('p', '还没有保存的规则，请先进入规则编辑器导入或创建。'));
    $('dailyRules').textContent = '已保存的日常组合：' + (state.settings.definitions.join('、') || '尚未选择');
    const versions = state.definitions.map((item) => [item.id + '@' + item.version, item.name + ' · ' + item.version]);
    options('before', versions); options('after', versions);
  }
  function renderBatches() {
    $('batchList').replaceChildren();
    if (!state.batches.length) $('batchList').append(node('p', '开始检测后，批次和复核入口会出现在这里。'));
    for (const item of state.batches) {
      const link = node('a', '打开复核'); if (item.report_url) link.href = item.report_url;
      row($('batchList'), item.id, `${item.status === 'completed' ? '检测完成' : '存在未完成项'} · ${item.evaluations} 项检测 · ${item.errors.length} 个文件错误`,
          item.report_url ? link : node('small', '报告不可用，可通过 detect report 重新导出'));
    }
  }
  function editSample(item) {
    selectedSample = item; $('sampleEdit').hidden = false; $('sampleEdit').open = true;
    for (const [id, key] of [['sampleLabel','label'],['sampleVerdict','verdict'],['sampleStart','start_s'],['sampleEnd','end_s'],['sampleReviewer','reviewer'],['sampleComment','comment']]) $(id).value = item[key];
    $('sampleChecked').checked = false; $('sampleEdit').scrollIntoView({block:'center'});
  }
  function renderSamples() {
    $('sampleList').replaceChildren();
    if (!state.standards.length) $('sampleList').append(node('p', '暂无人工标准。先完成问题复核或未命中抽检。'));
    options('sampleFilter', [['','全部标签'], ...[...new Set(state.standards.map((item)=>item.label))].sort().map((label)=>[label,label])]);
    for (const item of state.standards.filter((item)=>!$('sampleFilter').value || item.label===$('sampleFilter').value)) {
      row($('sampleList'), `${item.label} · ${item.verdict === 'problem' ? '存在问题' : '已检查无此问题'}`,
          `${item.recording_name} · ${item.start_s}–${item.end_s} 秒 · ${item.reviewer} · ${item.comment} · 修订 ${item.revision}`,
          button('修订', () => editSample(item)));
    }
    options('sampleSet', state.sets.map((item) => [item.id, `${item.name} · ${item.samples} 条标准`]));
  }
  function renderComparisons() {
    $('comparisons').replaceChildren();
    const labels = {hits:'命中问题片段', misses:'漏检问题片段', false_alarms:'正常范围误报', boundary_errors:'定位不匹配', unjudged:'未检查范围命中', normal_correct:'正确排除正常范围'};
    for (const item of state.comparisons) {
      const card = node('div', undefined, 'comparison');
      card.append(node('h3', `${item.before} → ${item.after}`), node('p', `${item.set_name} · ${item.comparable}/${item.recordings} 通可比较 · ${item.status === 'completed' ? '对比完成' : '对比不完整，缺失项未计入结果'}`));
      const wrapper = node('div', undefined, 'metrics'), table = node('table'), header = node('tr');
      ['指标','原版本','新版本','变化'].forEach((text) => header.append(node('th', text))); table.append(header);
      for (const [key, label] of Object.entries(labels)) {
        const tr = node('tr'), a = item.totals.before[key], b = item.totals.after[key];
        [label,String(a),String(b),(b-a>0?'+':'') + (b-a)].forEach((text) => tr.append(node('td',text))); table.append(tr);
      }
      wrapper.append(table); card.append(wrapper);
      const details = node('details'), summary = node('summary', '查看逐录音片段与保存对比');
      const content = node('div');
      details.append(summary, content);
      let loading = false, loaded = false;
      details.ontoggle = async () => {
        if (!details.open || loading || loaded) return;
        loading = true;
        content.replaceChildren(node('p', '正在加载对比…'));
        try {
          const response = await api('/api/comparisons/' + item.id);
          const json = JSON.stringify(response.comparison, null, 2);
          const link = node('a', '下载本次对比 JSON');
          link.href = '#';
          link.download = 'rule-comparison.json';
          link.onclick = () => {
            const url = URL.createObjectURL(new Blob([json], {type:'application/json'}));
            link.href = url;
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          };
          content.replaceChildren(node('pre', json), link);
          VoiceMotion.reveal(content);
          loaded = true;
        } catch (error) {
          content.replaceChildren(node('p', error.message + '。收起后重新打开可重试。'));
        } finally { loading = false; }
      };
      card.append(details);
      if (item.status === 'completed') card.append(button('将此新版本选为日常规则', async () => {
        const id = item.after.split('@')[0];
        const refs = state.settings.definitions.filter((ref) => ref.split('@')[0] !== id); refs.push(item.after);
        await saveDaily(refs); await refresh(true); notice('已明确选用 ' + item.after + '，其他日常规则保持原版本。');
      }));
      $('comparisons').append(card);
    }
  }
  function renderJobs() {
    const container = $('jobs');
    const previous = new Map([...container.children].map(card => [card.dataset.jobId, card]));
    for (const job of state.jobs) {
      const card = previous.get(job.id) || node('div', undefined, 'job');
      previous.delete(job.id);
      const status = job.status === 'running' ? `处理中 ${job.done}/${job.total || '…'}`
        : job.status === 'failed' ? '失败' : '已完成';
      const title = card.querySelector('strong') || card.appendChild(node('strong'));
      const text = `${job.kind} · ${status}`;
      if (job.status !== card.dataset.status) {
        VoiceMotion.feedback(title, text);
        card.dataset.status = job.status;
        if (job.error) card.append(node('p', job.error));
        if (job.result?.report_url) {
          const link = node('a', '打开本次录音复核');
          link.href = job.result.report_url;
          card.append(document.createTextNode(' · '), link);
        }
      } else if (title.textContent !== text) {
        title.textContent = text;
      }
      if (!card.isConnected) {
        card.dataset.jobId = job.id;
        container.append(card);
      }
    }
    for (const card of previous.values()) card.remove();
  }
  async function refresh(initial=false) {
    const previous = state;
    state = await api('/api/workspace');
    const changed = (name) => JSON.stringify(previous?.[name]) !== JSON.stringify(state[name]);
    if (initial) $('workspaceName').value = state.settings.name;
    if (initial || changed('settings')) $('roots').textContent = '录音目录：' + state.settings.roots.join('；');
    if (changed('inputs')) renderInputs();
    if (initial || changed('definitions') || changed('settings')) renderRules(initial);
    if (changed('batches')) renderBatches();
    if (changed('standards') || changed('sets')) renderSamples();
    if (changed('comparisons')) renderComparisons();
    if (changed('jobs')) renderJobs();
    updateRunOptions();
  }
  async function followJobs() {
    if (polling) return; polling=true;
    try {
      do { await new Promise((resolve) => setTimeout(resolve,800)); await refresh(); }
      while (state.jobs.some((job) => job.status === 'running'));
    } finally { polling=false; }
  }
  async function saveDaily(refs=selectedRules()) {
    await api('/api/workspace/settings', {name:$('workspaceName').value, definitions:refs, expected_revision:state.settings.revision});
  }
  $('scan').onclick = () => perform(async () => { await api('/api/workspace/scan',{}); await refresh(); notice('目录已重新扫描。'); }, $('scan'));
  $('saveDaily').onclick = () => perform(async () => { await saveDaily(); await refresh(); notice('工作区和日常规则已保存，重新启动后继续使用。'); }, $('saveDaily'));
  $('runMode').onchange = updateRunOptions;
  $('inputs').onchange = updateRunOptions;
  $('run').onclick = () => perform(async () => {
    const result = await api('/api/workspace/run', {definitions:selectedRules(), mode:$('runMode').value, limit:$('trialSizeLabel').hidden ? 10 : $('trialSize').valueAsNumber,
      selected:[...$('inputs').querySelectorAll('input:checked')].map((input) => input.value)});
    notice(result.message || '批次已启动。可在本页查看进度，完成后进入人工复核。'); if (!result.skipped) await followJobs();
  }, $('run'));
  $('sampleFilter').onchange = renderSamples;
  $('freeze').onclick = () => perform(async () => { await api('/api/workspace/freeze',{name:$('setName').value,ids:state.standards.filter((item)=>!$('sampleFilter').value || item.label===$('sampleFilter').value).map((item)=>item.id)}); await refresh(); notice('固定样本集已保存，之后的样本修订不会改变它。'); }, $('freeze'));
  $('compare').onclick = () => perform(async () => {
    const before = $('before').value, after = $('after').value;
    if (!$('sampleSet').value) throw Error('请先冻结一份人工标准作为固定样本集。');
    if (!before || !after || before === after) throw Error('请选择两个不同的规则版本。');
    await api('/api/workspace/compare', {set_id:$('sampleSet').value, before, after});
    notice('正在同一固定样本集上运行两个版本。');
    await followJobs();
  }, $('compare'));
  $('sampleForm').onsubmit = (event) => { event.preventDefault(); perform(async () => {
    await api('/api/workspace/standard', {id:selectedSample.id,expected_revision:selectedSample.revision,recording_id:selectedSample.recording_id,
      label:$('sampleLabel').value,verdict:$('sampleVerdict').value,start_s:$('sampleStart').valueAsNumber,end_s:$('sampleEnd').valueAsNumber,
      reviewer:$('sampleReviewer').value,comment:$('sampleComment').value,checked:$('sampleChecked').checked,source:selectedSample.source});
    $('sampleEdit').hidden=true;
    await refresh();
    $('sampleList').after($('notice'));
    notice('已保存新修订，历史和冻结样本集保持原值。');
  }, event.submitter); };
  perform(async () => { await refresh(true); if (state.jobs.some((job)=>job.status==='running')) await followJobs(); });
})();
