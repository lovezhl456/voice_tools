'use strict';
(() => {
  const data = JSON.parse(document.getElementById('reviewData').textContent);
  const $ = id => document.getElementById(id);
  const entries = [], labels = new Map();
  const names = {dead_air:'长停顿',micro_dropout:'短断音',clustered_short_gaps:'密集短断音',terminal_interruption:'预期输出中断',manual_gap:'人工补标',inspect:'录音复查'};
  const statuses = {CANDIDATE:'待复核候选',EXCLUDED:'已按规则排除',CENSORED:'观察不足'};
  let current = null, dirty = false, formDirty = false;
  const message = text => { $('message').textContent = text; };
  const key = row => `${row.fingerprint}:${row.gap_id}`;
  const player = $('player');
  const fileFilter = createReviewFileFilter(data.records, record => record.result.fingerprint);
  const wave = createReviewWaveform(player, (a,b) => playback.setRange(a,b), message);
  const playback = createReviewPlayback(player, {duration:()=>current?.record.result.duration_s,
    seek:v=>wave.setTime(v), rangeChanged:(a,b)=>wave.syncRange(a,b), error:message});
  function rowFor(record, gap, origin='automatic') {
    return {schema_version:'1.0',audio_sha256:record.audio_sha256,fingerprint:record.result.fingerprint,
      gap_id:gap.id,origin,type:gap.type,start_s:gap.start_s,end_s:gap.end_s};
  }
  for (const record of data.records) {
    if (!record.result) continue;
    const gaps = record.result.gaps.length ? record.result.gaps : [{id:'inspect',type:'inspect',start_s:0,end_s:record.result.duration_s,status:record.result.status,evidence_level:'acoustic_only'}];
    for (const gap of gaps) entries.push({record,gap,row:rowFor(record,gap)});
  }
  $('statusFilter').replaceChildren(new Option('全部状态',''));
  for (const status of new Set(entries.map(e=>e.gap.status))) $('statusFilter').add(new Option(statuses[status]||status,status));
  function filtered() {
    return entries.filter(e => fileFilter.matches(e.record) &&
      (!$('statusFilter').value||e.gap.status===$('statusFilter').value) &&
      (!$('evidenceFilter').value||e.gap.evidence_level===$('evidenceFilter').value) &&
      (!$('unreviewed').checked||!labels.has(key(e.row))));
  }
  function refresh() {
    $('queue').replaceChildren();
    for (const entry of filtered()) {
      const button = document.createElement('button');
      button.type='button'; button.textContent=`${names[entry.gap.type]} · ${entry.gap.start_s.toFixed(3)}–${entry.gap.end_s.toFixed(3)}s${labels.has(key(entry.row))?' ✓':''}`;
      button.setAttribute('aria-current',String(entry===current)); button.onclick=()=>select(entry);
      $('queue').append(button);
    }
    $('progress').textContent=`${data.summary.files} 个录音 · ${data.summary.candidates||0} 个候选 · ${labels.size} 条标注${dirty?' · 尚未导出':''}${data.summary.truncated?' · 预览已截断，请分批复核或查看原始结果':''}`;
  }
  function select(entry) {
    if(formDirty){message('请先记下标注，或放弃当前表单修改。');return;}
    playback.pause(); current=entry; $('detail').hidden=false;
    const {record,gap,row}=entry, result=record.result;
    $('name').textContent=`${record.input.split(/[\\/]/).pop()} · ${names[gap.type]}`;
    $('metadata').textContent=`${gap.start_s.toFixed(3)}–${gap.end_s.toFixed(3)} 秒 · ${result.sample_rate} Hz`;
    $('status').textContent=`${statuses[gap.status]||gap.status} · ${gap.reason_code||''}`;
    $('warning').textContent=result.warnings.join('；');
    const evidence=record.evidence||{status:'not_provided'};
    const sources=(evidence.sources||[]).map(({intervals,...source})=>({...source,interval:intervals?.[gap.id]}));
    const evidenceNames={aligned:'已对齐',unverified:'未验证旁证',unaligned:'未对齐',legacy_summary:'旧版整流统计',partial:'覆盖不完整',error:'证据错误',no_matching_segments:'无匹配评分片段'};
    $('evidenceSummary').textContent=sources.length?sources.map(s=>`${s.kind.toUpperCase()}：${evidenceNames[s.status]||s.status}`).join('；'):(evidence.error||'未提供匹配的外部旁证');
    $('evidenceDetail').textContent=JSON.stringify({interval:gap,evidence:{...evidence,sources}},null,2);
    $('saveLabel').disabled=gap.type==='inspect';
    if(gap.type==='inspect')message('此录音没有自动候选；发现漏检时可调整试听范围并人工补标。');
    for(const [id,c] of [['leftTitle',0],['rightTitle',1]]) {
      $(id).querySelector('.role-name').textContent=result.config.system_channel===c?'AI':'用户';
      $(id).querySelector('.role-meta').textContent=result.channel_verified?'角色已核实':'角色未核实';
    }
    $('start').value=Math.max(0,gap.start_s-1); $('end').value=Math.min(result.duration_s,gap.end_s+1);
    $('seek').max=result.duration_s;
    const label=labels.get(key(row))||{};
    $('decision').value=label.decision||''; $('notes').value=label.notes||'';
    if(label.reviewer)$('reviewer').value=label.reviewer;
    playback.setSource(record.playback_sources?.[$('channel').value]||'',false);
    wave.load({record,op:{at_s:gap.start_s,observed_until_s:gap.end_s}});
    wave.syncChannel($('channel').value); refresh();
  }
  function valid(row, allowPending=false) {
    const record=data.records.find(r=>r.result?.fingerprint===row.fingerprint);
    if(!record||row.schema_version!=='1.0'||record.audio_sha256!==row.audio_sha256)throw Error('标签录音摘要或检测身份不匹配');
    const a=Number(row.start_s),b=Number(row.end_s);
    if(!Number.isFinite(a)||!Number.isFinite(b)||a<0||b<=a||b>record.result.duration_s)throw Error('标注区间无效');
    if(row.origin==='automatic') {
      const gap=record.result.gaps.find(g=>g.id===row.gap_id);
      if(!gap||gap.type!==row.type||Math.abs(gap.start_s-a)>1e-6||Math.abs(gap.end_s-b)>1e-6)throw Error('自动候选区间或类型不匹配');
    } else if(row.origin!=='manual'||!row.gap_id.startsWith('manual-')||row.type!=='manual_gap')throw Error('标注来源无效');
    if(allowPending&&!row.decision){
      if(row.reviewer||row.reviewed_at||row.notes||row.origin==='manual')throw Error('不完整标注');
      return record;
    }
    if(!['confirmed','normal','excluded','uncertain'].includes(row.decision)||!row.reviewer?.trim()||
      !/(Z|[+-]\d{2}:\d{2})$/.test(row.reviewed_at||'')||!Number.isFinite(Date.parse(row.reviewed_at)))throw Error('请完整填写判断、复核人和带时区时间');
    return record;
  }
  function save(manual=false) {
    if(!current)return;
    let row={...current.row};
    if(manual){const [a,b]=playback.bounds();row={...row,gap_id:'manual-'+(crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random().toString(16).slice(2)),origin:'manual',type:'manual_gap',start_s:a,end_s:b};}
    row={...row,decision:$('decision').value,reviewer:$('reviewer').value.trim(),reviewed_at:new Date().toISOString(),notes:$('notes').value};
    const record=valid(row); labels.set(key(row),row);
    if(manual)entries.push({record,row,gap:{id:row.gap_id,type:row.type,start_s:Number(row.start_s),end_s:Number(row.end_s),status:'CANDIDATE',evidence_level:'human_review'}});
    dirty = true;
    formDirty = false;
    refresh();
    reconcileSelection();
    message('已记下；离开前请导出 CSV。');
  }
  $('reviewForm').onsubmit=e=>{e.preventDefault();try{save();}catch(err){message(err.message);}};
  $('manual').onclick=()=>{try{save(true);}catch(err){message(err.message);}};
  function reconcileSelection() {
    if (formDirty) return;
    const visible = filtered();
    if (visible.length && !visible.includes(current)) select(visible[0]);
    else if (!visible.length) {
      playback.pause();
      current = null;
      $('detail').hidden = true;
    }
  }
  $('discard').onclick = () => {
    formDirty = false;
    reconcileSelection();
    if (current) select(current);
    message('已放弃尚未记下的表单修改。');
  };
  for(const id of ['decision','reviewer','notes'])$(id).oninput=()=>{formDirty=true;};
  for (const id of ['directoryFilter', 'fileFilter', 'statusFilter', 'evidenceFilter', 'unreviewed']) {
    $(id).onchange = () => {
      if (id === 'directoryFilter') fileFilter.updateFiles();
      refresh();
      if (formDirty && !filtered().includes(current))
        message('筛选已更新；当前表单有未记下的修改，记下或放弃后再切换录音。');
      else reconcileSelection();
    };
  }
  $('channel').onchange=()=>{playback.setSource(current?.record.playback_sources?.[$('channel').value]||'');wave.syncChannel($('channel').value);};
  function download(content,name,type) {
    const url=URL.createObjectURL(new Blob([content],{type})),link=document.createElement('a');
    link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
  }
  $('export').onclick=()=>{
    if(formDirty){message('当前表单未记下，请先记下标注。');return;}
    const cell=v=>{let s=String(v??'');if(/^['=+@-]/.test(s.trimStart()))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
    download('\uFEFF'+[data.fields,...[...labels.values()].map(r=>data.fields.map(k=>r[k]))].map(r=>r.map(cell).join(',')).join('\r\n'),'gaps-review.csv','text/csv;charset=utf-8');dirty=false;refresh();
  };
  function parseCSV(text) {
    const rows=[];let row=[],cell='',quoted=false;
    text=text.replace(/^\uFEFF/,'');
    for(let i=0;i<text.length;i++){
      const c=text[i];
      if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
      else if(!quoted&&(c===','||c==='\n')){row.push(cell);cell='';if(c==='\n'){rows.push(row);row=[];}}
      else if(c!=='\r'||quoted)cell+=c;
    }
    if(quoted)throw Error('CSV 引号未闭合');
    if(cell||row.length){row.push(cell);rows.push(row);}return rows;
  }
  $('import').onchange=async()=>{
    try{
      if(formDirty)throw Error('请先记下当前表单');
      const file=$('import').files[0];if(!file)return;
      const [header,...rows]=parseCSV(await file.text());
      if(JSON.stringify(header)!==JSON.stringify(data.fields))throw Error('CSV 列不匹配');
      const staged=new Map(),manual=[],seen=new Set();
      for(const cells of rows){
        if(cells.length!==header.length)throw Error('CSV 列数不匹配');
        const row=Object.fromEntries(header.map((h,i)=>[h,cells[i]]));
        for(const field of ['reviewer','notes'])if(/^'\s*['=+@-]/.test(row[field]))row[field]=row[field].slice(1);
        const record=valid(row,true);
        if(seen.has(key(row)))throw Error('重复标注');seen.add(key(row));
        if(!row.decision)continue;
        staged.set(key(row),row);
        if(row.origin==='manual'&&!entries.some(e=>key(e.row)===key(row)))manual.push({record,row,gap:{id:row.gap_id,type:row.type,start_s:Number(row.start_s),end_s:Number(row.end_s),status:'CANDIDATE',evidence_level:'human_review'}});
      }
      for(const [k,v] of staged)labels.set(k,v);entries.push(...manual);dirty=true;
      if(current)select(current);else refresh();message(`已导入 ${staged.size} 条标注`);
    }catch(err){message(err.message);}finally{$('import').value='';}
  };
  async function clip() {
    if(!current?.record.playback_sources?.both)throw Error('未附带音频，请指定 --include-audio');
    const response=await fetch(current.record.playback_sources.both);if(!response.ok)throw Error('音频读取失败');
    const bytes=await response.arrayBuffer(),view=new DataView(bytes);
    let offset=12,fmt=null,body=null;
    while(offset+8<=bytes.byteLength){const name=String.fromCharCode(...new Uint8Array(bytes,offset,4)),size=view.getUint32(offset+4,true);if(offset+8+size>bytes.byteLength)throw Error('WAV 数据截断');if(name==='fmt ')fmt=offset+8;if(name==='data')body=[offset+8,size];offset+=8+size+(size%2);}
    if(fmt===null||!body||view.getUint16(fmt,true)!==1||view.getUint16(fmt+14,true)!==16)throw Error('片段导出需要 PCM16 WAV');
    const channels=view.getUint16(fmt+2,true),rate=view.getUint32(fmt+4,true),[start,end]=playback.bounds();
    const a=Math.round(start*rate),b=Math.min(Math.round(end*rate),body[1]/(channels*2)),size=(b-a)*channels*2;
    const out=new ArrayBuffer(44+size),v=new DataView(out),u=new Uint8Array(out);
    for(const [at,s] of [[0,'RIFF'],[8,'WAVEfmt '],[36,'data']])for(let i=0;i<s.length;i++)u[at+i]=s.charCodeAt(i);
    v.setUint32(4,36+size,true);v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,channels,true);v.setUint32(24,rate,true);v.setUint32(28,rate*channels*2,true);v.setUint16(32,channels*2,true);v.setUint16(34,16,true);v.setUint32(40,size,true);
    u.set(new Uint8Array(bytes,body[0]+a*channels*2,size),44);
    return {wav:out,metadata:{audio_sha256:current.record.audio_sha256,fingerprint:current.row.fingerprint,gap_id:current.row.gap_id,original_start_s:a/rate,original_end_s:b/rate,sample_rate:rate,channels}};
  }
  for(const id of ['clip','clipMeta'])$(id).onclick=async e=>{e.preventDefault();try{const c=await clip();download(id==='clip'?c.wav:JSON.stringify(c.metadata,null,2),id==='clip'?'gap.wav':'gap.json',id==='clip'?'audio/wav':'application/json');}catch(err){message(err.message);}};
  document.addEventListener('keydown',e=>{if(/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;if(e.code==='Space'){e.preventDefault();playback.toggle();}if(['ArrowLeft','ArrowRight'].includes(e.key)){const list=filtered(),i=list.indexOf(current),next=list[i+(e.key==='ArrowRight'?1:-1)];if(next)select(next);}});
  window.addEventListener('beforeunload',e=>{if(dirty||formDirty){e.preventDefault();e.returnValue='';}});
  refresh();if(entries.length)select(entries[0]);else message('没有可复核录音，请检查报告中的输入错误。');
})();
