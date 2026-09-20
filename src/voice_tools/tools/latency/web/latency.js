/* Shared data-only renderer. Bundle HTML and scripts are never loaded. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {completed:'执行完成',failed:'处理失败',partial_error:'部分失败',interrupted:'已中断',measured:'存在有效测量',insufficient_evidence:'证据不足',not_measured:'未测量',paired:'已配对',overlap:'重叠',superseded:'被后续片段替代',unpaired:'未配对',short_filtered:'短片段过滤',all_silence:'全静音',identical_channels:'两个声道内容相同',no_valid_pairs:'没有有效配对'};
  const label = value => labels[value] || value;
  const seconds = value => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) + ' s' : '—';
  function localURL(path) {
    const url = new URL(path, document.baseURI);
    if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) throw Error('请通过本地 HTTP 服务查看；资源必须来自同一站点');
    return url.href;
  }
  function metrics(summary) {
    const coverage = summary.coverage || {};
    return `<div class="lat-stats"><div class="lat-stat">执行状态<b>${escape(label(summary.execution_status))}</b></div><div class="lat-stat">测量状态<b>${escape(label(summary.measurement_status))}</b></div><div class="lat-stat">Human → AI 配对覆盖<b>${coverage.ratio == null ? '—' : (coverage.ratio * 100).toFixed(1) + '%'}</b>${coverage.paired || 0} / ${coverage.eligible_human_segments || 0} 个合格人声片段</div>${['human_to_ai','ai_to_human'].map(direction => `<div class="lat-stat">${direction === 'human_to_ai' ? 'Human → AI' : 'AI → Human'} 中位数<b>${seconds(summary.statistics?.[direction]?.median_s)}</b>${summary.statistics?.[direction]?.count || 0} 轮 · P95 ${seconds(summary.statistics?.[direction]?.p95_s)}</div>`).join('')}</div>`;
  }
  function wave(track, duration, name) {
    const width = track.length || 1;
    const path = track.map((bin, index) => `M${(index / width * 1000).toFixed(2)} ${(40 - bin[0] * 35).toFixed(2)}V${(40 - bin[1] * 35).toFixed(2)}`).join('');
    return `<div>${escape(name)}</div><svg class="lat-wave" role="img" aria-label="${escape(name)}波形，点击跳转" data-duration="${Number(duration)}" viewBox="0 0 1000 80"><path d="${path}" stroke="#1e7a83" stroke-width="1"/><line x1="0" x2="1000" y1="40" y2="40" stroke="#afc6cb"/></svg>`;
  }
  function mount(root, data) {
    root.classList.add('vt-latency');
    let filePage = 0, turnPage = 0, selected = null, generation = 0;
    const rows = data.rows || [];
    function overview() {
      root.innerHTML = `<section class="lat-panel"><h2>运行摘要</h2>${data.summary ? metrics(data.summary) : '<p>此处展示任务收集的录音结果；完整运行摘要见导出文件。</p>'}<p class="lat-help">两个方向单独统计，批次分位数来自所有有效轮次。重复内容保留并计入统计。全静音、重复声道和零配对均不表示零延迟或业务通过。</p><div class="lat-tools">${Object.entries(data.exports || {}).map(([name,path])=>`<a href="${escape(localURL(path))}" download>${escape(name)}</a>`).join('')}</div>${data.truncated ? '<p class="lat-warning">复查列表达到展示上限；完整记录保存在下载文件中。</p>' : ''}</section><section class="lat-panel"><h2>录音列表 · ${rows.length} 份</h2><div class="lat-scroll"><table><thead><tr><th>录音</th><th>执行</th><th>测量</th><th>备注</th></tr></thead><tbody>${rows.slice(filePage * 50, filePage * 50 + 50).map((row,i)=>`<tr><td><button data-file="${filePage * 50 + i}">${escape((row.input || row.name || '录音').split('/').pop())}</button></td><td>${escape(label(row.execution_status))}</td><td>${escape(label(row.measurement_status))}</td><td>${row.duplicate_of_file_index ? '与文件 ' + escape(row.duplicate_of_file_index) + ' 内容相同' : escape(row.error?.message || '')}</td></tr>`).join('')}</tbody></table></div><div class="lat-pager"><button data-files="-1" ${filePage ? '' : 'disabled'}>上一页录音</button><span>第 ${filePage + 1} 页 · 每页 50 份</span><button data-files="1" ${(filePage + 1) * 50 < rows.length ? '' : 'disabled'}>下一页录音</button></div></section><div class="lat-recording" aria-live="polite"></div>`;
    }
    function turns() {
      const report = selected.report, pairs = report.measurement.pairs;
      const page = pairs.slice(turnPage * 50, turnPage * 50 + 50);
      const max = Math.max(1, ...page.map(p => p.latency_s));
      const chart = page.map((p,i)=>`<circle cx="${20 + (i + .5) / Math.max(1, page.length) * 960}" cy="${145 - p.latency_s / max * 125}" r="5" fill="${p.direction === 'human_to_ai' ? '#177d88' : '#bb752c'}"><title>${escape(p.direction)} ${seconds(p.latency_s)}</title></circle>`).join('');
      root.querySelector('.lat-turns').innerHTML = `<h2>逐轮延迟 · ${pairs.length} 轮</h2><p class="lat-help">图表和表格显示当前 50 轮；统计使用全部轮次。蓝色 Human → AI，橙色 AI → Human。纵轴 0–${seconds(max)}。</p><svg role="img" aria-label="当前页逐轮延迟" viewBox="0 0 1000 170"><line x1="20" x2="990" y1="145" y2="145" stroke="#a9bdc5"/>${chart}</svg><div class="lat-scroll"><table><thead><tr><th>方向</th><th>源片段结束</th><th>应答开始</th><th>延迟</th><th>试听</th></tr></thead><tbody>${page.map(p=>`<tr><td>${escape(p.direction)}</td><td>${seconds(p.source_end_s)}</td><td>${seconds(p.response_start_s)}</td><td>${seconds(p.latency_s)}</td><td><button data-seek="${Math.max(0, p.source_end_s - .3)}">跳转试听</button></td></tr>`).join('')}</tbody></table></div><div class="lat-pager"><button data-turns="-1" ${turnPage ? '' : 'disabled'}>上一页轮次</button><span>第 ${turnPage + 1} 页</span><button data-turns="1" ${(turnPage + 1) * 50 < pairs.length ? '' : 'disabled'}>下一页轮次</button></div>`;
    }
    async function loadFile(index) {
      const id = ++generation, row = rows[index];
      root.querySelector('audio')?.pause();
      const container = root.querySelector('.lat-recording');
      container.innerHTML = '<section class="lat-panel">读取结构化录音数据…</section>';
      if (!row.detail) { container.innerHTML = `<section class="lat-panel lat-warning">${escape(row.error?.message || '该录音没有详细测量结果')}</section>`; return; }
      try {
        const response = await fetch(localURL(row.detail));
        if (!response.ok) throw Error('详细数据读取失败：HTTP ' + response.status);
        const report = await response.json();
        if (id !== generation || !root.isConnected) return;
        if (report.kind !== 'latency_recording' || report.schema_version !== '1.0' || !Array.isArray(report.measurement?.pairs) || !Array.isArray(report.measurement?.segments)) throw Error('不支持的 latency 结果协议');
        selected = {row, report}; turnPage = 0;
        const measured = report.measurement;
        const tracks = report.waveform?.tracks || [];
        container.innerHTML = `<section class="lat-panel"><h2 class="lat-name">${escape((row.input || '').split('/').pop())}</h2>${metrics({execution_status:report.execution_status,measurement_status:measured.status,coverage:measured.coverage,statistics:measured.statistics})}${measured.reasons.length ? `<p class="lat-warning">证据限制：${escape(measured.reasons.map(label).join(' / '))}</p>` : ''}<p class="lat-help">录音起点 = 0 s · AI 位于 ${escape(report.system_channel)} · 波形每轨最多 1600 格，仅用于定位，不参与统计。</p>${tracks.map((track,i)=>wave(track,report.audio.duration_s,(i ? '右声道' : '左声道') + ((i === 0 ? 'left':'right') === report.system_channel ? ' · AI' : ' · Human'))).join('')}${row.playback ? `<audio controls preload="metadata" src="${escape(localURL(row.playback))}"></audio>` : '<p class="lat-warning">报告未包含试听音频。独立报告需要 --include-audio；任务复查使用任务中已收集的录音。</p>'}<div class="lat-player-status" role="status"></div><a href="${escape(localURL(row.detail))}" download>下载完整录音 JSON（含全部片段去向）</a></section><section class="lat-panel lat-turns"></section><section class="lat-panel"><h2>配对去向</h2><p class="lat-help">合格人声片段的 outgoing 作为 Human → AI 分母去向；incoming 独立记录该片段在反方向的应答角色。</p><div class="lat-tools">${Object.entries(measured.coverage.dispositions || {}).map(([key,count])=>`<span>${escape(label(key))}：${escape(count)}</span>`).join('')}</div><details><summary>完整参数、身份及来源</summary><pre>${escape(JSON.stringify({recording_id:report.recording_id,parameters:report.parameters,parameter_sources:report.parameter_sources,fixed_parameters:report.fixed_parameters,provenance:report.provenance,resources:report.resources},null,2))}</pre></details></section>`;
        turns();
      } catch (error) { if (id === generation) container.innerHTML = `<section class="lat-panel lat-warning">${escape(error.message)}</section>`; }
    }
    async function seek(value) {
      const player = root.querySelector('audio'), status = root.querySelector('.lat-player-status');
      if (!player) { if (status) status.textContent = '没有可用试听录音。'; return; }
      try {
        if (player.readyState < 1) await new Promise((resolve,reject)=>{player.addEventListener('loadedmetadata',resolve,{once:true});player.addEventListener('error',()=>reject(Error('音频加载失败')),{once:true});});
        player.currentTime = Math.min(Math.max(0,value),player.duration);
        await player.play();
        status.textContent = '正在从 ' + seconds(player.currentTime) + ' 试听';
      } catch (error) { status.textContent = '播放未开始：' + error.message; }
    }
    root.onclick = event => {
      const file = event.target.closest('[data-file]'), files = event.target.closest('[data-files]'), page = event.target.closest('[data-turns]'), button = event.target.closest('[data-seek]'), waveform = event.target.closest('.lat-wave');
      if (file) loadFile(Number(file.dataset.file));
      else if (files) { generation++; root.querySelector('audio')?.pause(); filePage += Number(files.dataset.files); overview(); }
      else if (page) { turnPage += Number(page.dataset.turns); turns(); }
      else if (button) seek(Number(button.dataset.seek));
      else if (waveform) { const rect=waveform.getBoundingClientRect(); seek((event.clientX-rect.left)/rect.width*Number(waveform.dataset.duration)); }
    };
    try { overview(); if (rows.length) loadFile(0); } catch (error) { root.textContent = error.message; }
  }
  window.VoiceLatency = {mount};
}());
