"use strict";
(() => {
  const data = JSON.parse(document.getElementById("reviewData").textContent);
  const $ = id => document.getElementById(id);
  const labels = new Map(), entries = [], byKey = new Map();
  const extra = ["first_audible_s", "deadline_s", "policy_id", "expected_response", "scenario", "line_id", "group_id", "split"];
  let current = null, filtered = [], dirty = false, formDirty = false;
  const key = row => JSON.stringify([row.sample_id, row.opportunity_id]);
  const player = $("player");
  function message(text) { $("message").textContent = text; }
  for (const record of data.records) {
    if (!record.result) continue;
    const option = new Option(record.input.split(/[\\/]/).pop(), record.sample_id);
    $("fileFilter").add(option);
    for (const op of record.result.opportunities) {
      const row = {sample_id:record.sample_id, audio_sha256:record.audio_sha256, opportunity_id:op.id,
        at_s:op.at_s, observed_until_s:op.observed_until_s, status:op.status};
      const entry = {record, op, row, key:key(row)};
      entries.push(entry); byKey.set(entry.key, entry);
    }
  }
  for (const status of [...new Set(entries.map(e => e.op.status))]) $("statusFilter").add(new Option(data.labels[status], status));
  function progress() {
    $("progress").textContent = `${data.summary.files} 个录音 · ${entries.length} 个机会 · ${labels.size} 条已填写 · ${filtered.length} 条符合筛选` + (dirty || formDirty ? " · 有未导出的改动" : " · 无未导出的改动");
  }
  function refresh() {
    const queue = $("queue"), scrollTop = queue.scrollTop;
    const focusedKey = queue.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    filtered = entries.filter(e => (!$("fileFilter").value || e.row.sample_id === $("fileFilter").value) &&
      (!$("statusFilter").value || e.op.status === $("statusFilter").value) &&
      (!$("evidenceFilter").value || e.op.evidence_level === $("evidenceFilter").value) && (!$("unreviewed").checked || !labels.has(e.key)));
    queue.replaceChildren();
    for (const entry of filtered) {
      const button = document.createElement("button");
      button.type = "button"; button.setAttribute("aria-current", String(current === entry));
      button.dataset.key = entry.key;
      button.textContent = `${labels.has(entry.key) ? "✓ " : ""}${entry.record.input.split(/[\\/]/).pop()} · ${entry.op.id}`;
      const small = document.createElement("small"); small.textContent = `${entry.op.at_s.toFixed(2)} s · ${data.labels[entry.op.status]}`;
      button.append(small); button.onclick = () => select(entry); $("queue").append(button);
    }
    if (!filtered.length) { const p = document.createElement("p"); p.textContent = "没有符合条件的机会。"; $("queue").append(p); if (!formDirty) {player.pause();current=null;$("detail").hidden=true;} }
    queue.scrollTop = scrollTop;
    if (focusedKey) [...queue.children].find(button => button.dataset.key === focusedKey)?.focus({preventScroll:true});
    progress();
  }
  function updateQueueSelection() {
    const queue = $("queue"), hadFocus = queue.contains(document.activeElement);
    for (const button of queue.querySelectorAll("button")) {
      const selected = button.dataset.key === current.key;
      button.setAttribute("aria-current", String(selected));
      if (!selected) continue;
      // Scroll only the list so navigation does not move the review form.
      const item = button.getBoundingClientRect(), viewport = queue.getBoundingClientRect();
      const top = viewport.top + queue.clientTop + 3, bottom = viewport.top + queue.clientTop + queue.clientHeight - 3;
      if (item.top < top || item.height > bottom - top) queue.scrollTop += item.top - top;
      else if (item.bottom > bottom) queue.scrollTop += item.bottom - bottom;
      if (hadFocus) button.focus({preventScroll:true});
    }
  }
  function saveDraft() {
    if (!formDirty) return true;
    message("当前表单尚未记下，请先记下标注或点击放弃表单修改。");
    return false;
  }
  function select(entry) {
    if (!saveDraft()) return;
    player.pause(); current = entry; $("detail").hidden = false;
    const {record, op} = entry, result = record.result;
    $("name").textContent = `${record.input.split(/[\\/]/).pop()} · ${op.id}`;
    $("metadata").textContent = `${result.sample_rate} Hz · ${result.duration_s.toFixed(2)} 秒 · 标注窗口 ${op.at_s.toFixed(2)}–${op.observed_until_s.toFixed(2)} 秒`;
    $("status").textContent = `${data.labels[op.status]} · ${data.evidence[op.evidence_level]}`;
    $("warning").textContent = result.warnings.join("；") || "活动不代表有效回答，请试听后判断。";
    for (const [id, channel] of [["leftTitle", 0], ["rightTitle", 1]]) $(id).textContent = `${channel === 0 ? "左" : "右"}轨 · 配置角色：${result.config.system_channel === channel ? "AI" : "用户"}${result.channel_verified ? "（角色已核实）" : "（角色未核实）"}`;
    $("start").value = Math.max(0, op.at_s - 2).toFixed(2);
    $("end").value = Math.min(result.duration_s, op.observed_until_s + 1).toFixed(2);
    $("seek").max = result.duration_s;
    const label = labels.get(entry.key) || {};
    $("decision").value = label.decision || ""; $("notes").value = label.notes || "";
    if (label.reviewer) $("reviewer").value = label.reviewer;
    for (const id of extra) $(id).value = label[id] ?? "";
    const clip = record.clips && record.clips[op.id];
    $("clip").hidden = $("clipMeta").hidden = !clip;
    if (clip) { $("clip").href = clip.audio; $("clipMeta").href = clip.metadata; }
    switchSource(false); draw(); updateQueueSelection(); progress();
  }
  function bounds() {
    const start = Number($("start").value), end = Number($("end").value);
    if (!current || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end > current.record.result.duration_s + .000001) throw Error("试听起止须在录音范围内，且终点晚于起点");
    return [start, end];
  }
  function switchSource(keepTime = true) {
    if (!current) return;
    const sources = current.record.playback_sources || {}, source = sources[$("channel").value];
    const at = keepTime && Number.isFinite(player.currentTime) ? player.currentTime : Number($("start").value);
    const playing = !player.paused;
    player.pause();
    for (const option of $("channel").options) option.disabled = !sources[option.value];
    $("play").disabled = !source; player.hidden = !source;
    if (!source) { player.removeAttribute("src"); player.load(); message("未附带该声道的音频，无法试听。波形和标注仍可使用。"); return; }
    player.onloadedmetadata = () => { player.currentTime = Math.min(at, player.duration); if (playing) player.play().catch(e => message(e.message)); };
    player.src = source; player.load();
  }
  function draw() {
    if (!current) return;
    const waves = current.record.waveform, duration = current.record.result.duration_s;
    for (const [id, channel] of [["leftWave", 0], ["rightWave", 1]]) {
      const canvas = $(id), ctx = canvas.getContext("2d"), w = canvas.width, h = canvas.height;
      ctx.clearRect(0, 0, w, h); ctx.fillStyle = "#fae5b2";
      ctx.fillRect(current.op.at_s / duration * w, 0, (current.op.observed_until_s - current.op.at_s) / duration * w, h);
      ctx.strokeStyle = channel ? "#427ba0" : "#087875"; ctx.lineWidth = 1; ctx.beginPath();
      const peaks = waves ? waves.channels[channel] || [] : [];
      for (let i = 0; i < peaks.length; i++) { const x = i / peaks.length * w; ctx.moveTo(x, h / 2 - peaks[i][1] * h * .46); ctx.lineTo(x, h / 2 - peaks[i][0] * h * .46); }
      ctx.stroke(); ctx.strokeStyle = "#cd5508"; const x = player.currentTime / duration * w;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
  }
  async function toggle() {
    if (!current || $("play").disabled) return;
    try { const [start,end] = bounds(); if (!player.paused) player.pause(); else { if (player.currentTime < start || player.currentTime >= end) player.currentTime = start; await player.play(); } } catch(e) { message(e.message); }
  }
  player.addEventListener("timeupdate", () => {
    if (!current) return;
    try { const [start,end] = bounds(); if (player.currentTime >= end && !player.paused) { if ($("loop").checked) { player.currentTime = start; player.play().catch(e => message(e.message)); } else player.pause(); } } catch(e) { player.pause(); message(e.message); }
    $("seek").value = player.currentTime; $("time").textContent = `${player.currentTime.toFixed(2)} / ${current.record.result.duration_s.toFixed(2)} 秒`; draw();
  });
  player.addEventListener("ended", () => { if ($("loop").checked) { player.currentTime = Number($("start").value); player.play().catch(e => message(e.message)); } });
  player.addEventListener("error", () => { if (player.getAttribute("src")) message("音频无法播放，请确认报告 audio 目录完整或使用本地 HTTP 打开。"); });
  $("channel").onchange = () => switchSource(); $("play").onclick = toggle;
  $("seek").oninput = () => { if (current && player.getAttribute("src")) player.currentTime = Number($("seek").value); };
  for (const id of ["start", "end"]) $(id).onchange = () => { try { const [start] = bounds(); player.currentTime = start; message(""); } catch(e) { message(e.message); } };
  function validate(row) {
    const entry = byKey.get(key(row)); if (!entry) throw Error("标签不属于本批次录音/事件/应答机会");
    if (row.audio_sha256 !== entry.row.audio_sha256) throw Error("音频摘要不匹配");
    for (const id of ["at_s", "observed_until_s"]) if (row[id] === "" || !Number.isFinite(Number(row[id])) || Math.abs(Number(row[id]) - entry.row[id]) > 1e-6) throw Error("标注窗口不匹配");
    if (!["missing", "delayed", "audible", "exclude", "uncertain"].includes(row.decision) || !row.reviewer?.trim()) throw Error("需要明确判断和复核人");
    if (!/(Z|[+-]\d\d:\d\d)$/.test(row.reviewed_at || "") || Number.isNaN(Date.parse(row.reviewed_at))) throw Error("复核时间须有效且含时区");
    if (row.first_audible_s !== "" && row.first_audible_s != null && (!Number.isFinite(Number(row.first_audible_s)) || Number(row.first_audible_s) < entry.op.at_s || Number(row.first_audible_s) >= entry.op.observed_until_s)) throw Error("首次可听回答须在标注窗口内");
    if (row.first_audible_s !== "" && row.first_audible_s != null && row.decision === "missing") throw Error("缺失回答不能同时记录可听回答时间");
    if (row.deadline_s && (!Number.isFinite(Number(row.deadline_s)) || Number(row.deadline_s) <= 0 || !row.policy_id?.trim())) throw Error("应答时限须为正数并填写策略版本");
    if (row.deadline_s && row.first_audible_s !== "" && row.first_audible_s != null && ["audible","delayed"].includes(row.decision) && ((Number(row.first_audible_s)-entry.op.at_s+1e-8 >= Number(row.deadline_s)) !== (row.decision === "delayed"))) throw Error("回答时间与人工迟答标签/时限不一致");
    if (row.split && (!["calibration", "validation"].includes(row.split) || !row.group_id?.trim())) throw Error("样本集合需合法且填写来源分组 ID");
    if (row.expected_response && !["true", "false"].includes(row.expected_response)) throw Error("是否应答须为 true/false");
    if (row.expected_response === "false" && !["exclude","uncertain"].includes(row.decision)) throw Error("无需应答应标为无需应答或无法判断");
    return entry;
  }
  $("reviewForm").addEventListener("input", () => { formDirty = true; progress(); });
  $("reviewForm").onsubmit = event => {
    event.preventDefault(); if (!current) return;
    const row = {...current.row, decision:$("decision").value, reviewer:$("reviewer").value.trim(), reviewed_at:new Date().toISOString(), notes:$("notes").value};
    for (const id of extra) row[id] = $(id).value.trim();
    try { validate(row); labels.set(current.key,row); dirty = true; formDirty = false; refresh(); message("标注已记在本页，请导出 CSV 后再离开。"); } catch(e) { message(e.message); }
  };
  function move(delta) { const index = filtered.indexOf(current); if (filtered.length) select(filtered[Math.max(0,Math.min(filtered.length-1,index+delta))]); }
  $("previous").onclick = () => move(-1); $("next").onclick = () => move(1);
  $("discard").onclick = () => {formDirty=false; if(current)select(current);message("已恢复本页最后记下的标注。");};
  for (const id of ["fileFilter","statusFilter","evidenceFilter","unreviewed"]) $(id).onchange = () => { refresh(); if (!filtered.includes(current) && filtered.length && !formDirty) select(filtered[0]); };
  function csvCell(value) { let text = String(value ?? ""); if (/^[\s]*[=+\-@]/.test(text)) text = "'" + text; return '"' + text.replaceAll('"','""') + '"'; }
  $("export").onclick = () => {
    if (!saveDraft()) return;
    const rows = entries.map(entry => labels.get(entry.key) || entry.row);
    const csv = "\ufeff" + [data.fields.join(","), ...rows.map(row => data.fields.map(id => csvCell(row[id])).join(","))].join("\r\n") + "\r\n";
    const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
    const a = document.createElement("a"); a.href = url; a.download = "review.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    dirty = false; progress(); message("已发起下载，请保管 review.csv；可重新导入继续复核。");
  };
  function parseCSV(text) {
    text = text.replace(/^\ufeff/, ""); let rows=[],row=[],value="",quoted=false;
    for (let i=0;i<text.length;i++) { const c=text[i]; if(c==='"') { if(quoted && text[i+1]==='"') {value+='"';i++;} else quoted=!quoted; }
      else if(c==="," && !quoted) {row.push(value);value="";} else if((c==="\n" || c==="\r") && !quoted) { if(c==="\r" && text[i+1]==="\n")i++; row.push(value); if(row.some(x=>x!=="")) rows.push(row);row=[];value=""; } else value+=c; }
    if(quoted)throw Error("CSV 引号未闭合"); if(value || row.length){row.push(value);rows.push(row);} if(!rows.length)throw Error("CSV 为空");
    const headers=rows.shift(); if(new Set(headers).size!==headers.length)throw Error("CSV 表头重复");
    for(const id of data.fields.slice(0,10))if(!headers.includes(id))throw Error(`CSV 缺少 ${id}`);
    return rows.map(values=>{if(values.length!==headers.length)throw Error("CSV 列数不一致"); const row={};headers.forEach((h,i)=>{const v=values[i];row[h]=/^'[\s]*[=+\-@]/.test(v)?v.slice(1):v;});return row;});
  }
  $("import").onchange = async () => {
    const file = $("import").files[0]; if(!file)return;
    try { if(!saveDraft())return; if(file.size>20*1024*1024)throw Error("复核 CSV 超过 20 MiB"); const rows=parseCSV(await file.text()), incoming=new Map(),seen=new Set();
      for(const row of rows) { const k=key(row); if(seen.has(k))throw Error("CSV 存在重复机会");seen.add(k);
        const entry=byKey.get(k); if(!entry)throw Error("CSV 包含不属于本批次的机会");
        if(row.audio_sha256!==entry.row.audio_sha256 || ["at_s","observed_until_s"].some(id=>row[id]==="" || !Number.isFinite(Number(row[id])) || Math.abs(Number(row[id])-entry.row[id])>1e-6))throw Error("CSV 音频摘要或标注窗口不匹配");
        if(!row.decision) {if([row.reviewer,row.reviewed_at,row.notes,...extra.map(id=>row[id])].some(x=>x?.trim()))throw Error("CSV 有半填标签");continue;}
        validate(row); const old=labels.get(k); if(old && data.fields.some(id=>String(old[id]??"")!==String(row[id]??"")))throw Error("导入与本页已有标签冲突；请先导出并在重新打开的页面导入"); incoming.set(k,row);
      }
      incoming.forEach((row,k)=>labels.set(k,row)); const wasDirty=dirty; formDirty=false; refresh(); if(current)select(current); dirty=wasDirty;progress();message(`已导入 ${incoming.size} 条标签。`);
    }catch(e){message(e.message);} finally{$("import").value="";}
  };
  document.addEventListener("keydown", event => { if(/INPUT|SELECT|TEXTAREA/.test(event.target.tagName))return;
    if(event.code==="Space"){event.preventDefault();toggle();} else if(event.key==="ArrowRight"){event.preventDefault();move(1);} else if(event.key==="ArrowLeft"){event.preventDefault();move(-1);}
    else if(/^[1-5]$/.test(event.key) && current){$("decision").selectedIndex=Number(event.key);formDirty=true;progress();}
  });
  window.addEventListener("beforeunload", event => {if(dirty || formDirty){event.preventDefault();event.returnValue="";}});
  refresh(); if(entries.length)select(entries[0]); else message("本批次没有可复核的应答机会；请在静态报告查看证据不足或处理错误。");
})();
