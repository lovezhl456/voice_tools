'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const data=JSON.parse($('editorData').textContent);
  const contract=DetectionEditorContract, clone=value=>JSON.parse(JSON.stringify(value));
  const normalize=value=>contract.normalize(value,data.catalog,data.schema);
  const ref=config=>config.id+'@'+config.version;
  const draftKey='voice-tools-config-draft:'+ (data.session?.library_id || 'offline');
  const versionsKey='voice-tools-config-versions:1';
  const parameterNames={remove_dc:'去除直流偏移',frame_ms:'帧长（毫秒）',threshold_dbfs:'活动／静音阈值（dBFS）',
    min_duration_s:'最短片段（秒）',join_gap_s:'合并间隔（秒）',clip_level:'削波幅值阈值'};
  const metricNames={rms_dbfs:'均方根电平',peak_dbfs:'峰值电平',duration_s:'窗口时长',clipping_ratio:'削波比例',
    activity_ratio:'活动占比',activity_total_s:'累计活动时长',activity_longest_s:'最长活动时长',activity_count:'活动次数',
    silence_ratio:'静音占比',silence_total_s:'累计静音时长',silence_longest_s:'最长静音时长',silence_count:'静音次数'};
  let definitions=data.definitions.map(normalize), draft=clone(data.templates[0]), baseline=null;
  let dirty=false, busy=false;
  const selectedRuns=new Set();
  function message(value,success=false) { $('message').textContent=value; $('message').classList.toggle('success',success); }
  function element(tag,text,className) { const node=document.createElement(tag); if(text)node.textContent=text;if(className)node.className=className;return node; }
  function button(text,action,className) { const node=element('button',text,className);node.type='button';node.onclick=action;return node; }
  function field(name,value,onChange,options={}) {
    const label=element('label',name), input=document.createElement(options.choices?'select':'input');
    if(options.choices) for(const [key,title] of options.choices) input.add(new Option(title,key));
    else {input.type=options.type || 'number';input.required=input.type!=='checkbox'&&options.required!==false;if(input.type==='number')input.step='any';}
    for(const key of ['min','max','maxLength']) if(options[key]!==undefined) input[key]=options[key];
    if(input.type==='checkbox') {label.classList.add('check');input.checked=Boolean(value);}
    else input.value=value ?? '';
    input.addEventListener(options.commit?'change':'input',()=>onChange(input.type==='checkbox'?input.checked:input.type==='number'?input.valueAsNumber:input.value,input));
    label.append(input);return label;
  }
  const channelChoices=[['0','左声道（0）'],['1','右声道（1）']];
  function params(container,values,kind) {
    container.replaceChildren();
    for(const [key,spec] of Object.entries(data.catalog[kind].params)) {
      container.append(field(parameterNames[key] || key,values[key],value=>{values[key]=value;changed();},
        {type:spec.type==='boolean'?'checkbox':'number',min:spec.minimum,max:spec.maximum}));
    }
  }
  function defaultParams(kind) { return Object.fromEntries(Object.entries(data.catalog[kind].params).map(([key,spec])=>[key,spec.default])); }
  function validDraft() {
    if(!$('definitionForm').reportValidity()) throw Error('请先补齐必填项，并检查数值范围。');
    return normalize(draft);
  }
  function preview() {
    $('preview').textContent=JSON.stringify(draft,null,2);
    $('dirtyState').textContent=dirty?'有未保存修改':baseline?'当前配置无未保存修改':'当前配置尚未保存';
  }
  function changed() {
    dirty=true;preview();
    try {localStorage.setItem(draftKey,JSON.stringify({config:draft,baseline}));}
    catch {message('浏览器草稿无法保存，请下载配置以免丢失。');}
  }
  function clearDraft() { try {localStorage.removeItem(draftKey);} catch (_) { /* Download and server save still work. */ } }
  function canSwitch() { if(busy||dirty) {message('请先保存配置或放弃未保存修改，再切换。');return false;} return true; }
  function loadSaved(config) {
    draft=normalize(config);baseline=clone(draft);dirty=false;clearDraft();render();
    $('savedConfig').value=ref(config);$('example').value='';
  }
  function startDraft(config) {
    draft=normalize(config);$('savedConfig').value='';changed();render();
  }
  function discardDraft() {
    if(baseline) {
      loadSaved(baseline);message('已恢复上次保存的配置。');
    } else {
      draft=normalize(data.templates[0]);dirty=false;clearDraft();render();
      $('savedConfig').value='';$('example').value='';
      message('已放弃草稿。当前示例尚未保存，请保存后再运行。');
    }
  }
  function newConfig() {
    return normalize({schema_version:'1.0',id:'custom-rule',version:'1',name:'自定义检测',description:'描述要识别的业务问题',
      metrics:{level:{kind:'rms_dbfs',channel:1}},rules:[{id:'rule-1',label:'自定义标签',when:{metric:'level',op:'lt',value:-35}}]});
  }
  function defaultCondition() {return {metric:Object.keys(draft.metrics)[0],op:'lt',value:0};}
  function visitConditions(callback) {
    function visit(node) {if(node.metric) callback(node);for(const child of node.all||node.any||(node.not?[node.not]:[]))visit(child);}
    for(const rule of draft.rules) {visit(rule.when);if(rule.unless)visit(rule.unless);}
  }
  function renderScope() {
    for(const [id,key] of [['scopeStart','start_s'],['scopeEnd','end_s'],['skipFirst','skip_first_s'],['skipLast','skip_last_s']]) {
      $(id).value=draft.scope[key] ?? '';
      $(id).oninput=()=>{draft.scope[key]=$(id).value===''&&key==='end_s'?null:$(id).valueAsNumber;changed();};
    }
    $('windowKind').value=draft.window.kind;
    const window=draft.window, area=$('windowParams');area.replaceChildren();
    if(window.kind!=='whole') {
      area.append(field('窗口长度（秒）',window.length_s,value=>{window.length_s=value;changed();},{min:.02,max:3600}),
        field('分析不足长度的尾部窗口',window.include_partial,value=>{window.include_partial=value;changed();},{type:'checkbox'}));
    }
    if(window.kind==='sliding') area.append(field('窗口步长（秒）',window.step_s,value=>{window.step_s=value;changed();},{min:.02,max:3600}));
    if(window.kind==='after_activity') {
      area.append(field('触发声道',window.channel,value=>{window.channel=Number(value);changed();},{choices:channelChoices}),
        field('活动结束后等待（秒）',window.delay_s,value=>{window.delay_s=value;changed();},{min:0,max:120}));
      const trigger=element('div',null,'grid wide params');params(trigger,window.params,'activity_total_s');area.append(trigger);
    }
    $('exclusions').replaceChildren();
    draft.scope.exclude.forEach((span,index)=>{
      const row=element('div',null,'interval');
      row.append(field('排除起点（秒）',span.start_s,value=>{span.start_s=value;changed();},{min:0,max:3600}),
        field('排除终点（秒）',span.end_s,value=>{span.end_s=value;changed();},{min:0,max:3600}),
        button('移除此时段',()=>{draft.scope.exclude.splice(index,1);changed();renderScope();},'danger'));
      $('exclusions').append(row);
    });
  }
  function renderMetrics() {
    $('metrics').replaceChildren();
    for(const [alias,metric] of Object.entries(draft.metrics)) {
      const card=element('div',null,'metric-card'), heading=element('div',null,'item-heading');
      heading.append(element('h3',alias),button('删除指标',()=>{
        if(Object.keys(draft.metrics).length===1) {message('至少保留一个指标。');return;}
        let used=false;visitConditions(node=>{if(node.metric===alias)used=true;});
        if(used) {message('该指标仍被规则引用，请先修改判断条件。');return;}
        delete draft.metrics[alias];changed();renderMetrics();renderRules();
      },'danger'));
      const grid=element('div',null,'grid');
      grid.append(field('指标标识',alias,(value,input)=>{
        if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(value)||(value!==alias&&Object.hasOwn(draft.metrics,value))) {
          input.value=alias;message('指标标识无效或重复，本次重命名未保存。');return;
        }
        if(value===alias)return;
        draft.metrics=Object.fromEntries(Object.entries(draft.metrics).map(([key,item])=>[key===alias?value:key,item]));
        visitConditions(node=>{if(node.metric===alias)node.metric=value;});changed();renderMetrics();renderRules();
      },{type:'text',maxLength:80,commit:true}),
      field('指标类型',metric.kind,value=>{metric.kind=value;metric.params=defaultParams(value);changed();renderMetrics();message('指标类型已更换，计算参数已使用新类型的默认值。');},
        {choices:Object.keys(data.catalog).map(key=>[key,(metricNames[key] || key)+' · '+data.catalog[key].unit])}),
      field('分析声道',metric.channel,value=>{metric.channel=Number(value);changed();},{choices:channelChoices}));
      const parameterGrid=element('div',null,'grid params');params(parameterGrid,metric.params,metric.kind);
      card.append(heading,grid,element('p',data.catalog[metric.kind].description,'hint'),parameterGrid);$('metrics').append(card);
    }
  }
  function conditionEditor(node,replace,depth=0) {
    const box=element('div',null,'condition'), toolbar=element('div',null,'condition-toolbar');
    const mode=node.all?'all':node.any?'any':node.not?'not':'leaf';
    toolbar.append(field('条件类型',mode,value=>{
      const leaf=defaultCondition();replace(value==='leaf'?leaf:value==='not'?{not:node}:{[value]:[node]});changed();renderRules();
    },{choices:[['leaf','单项比较'],['all','全部满足（AND）'],['any','任一满足（OR）'],['not','取反（NOT）']]}));
    box.append(toolbar);
    if(mode==='leaf') {
      const fields=element('div',null,'leaf');
      fields.append(field('比较指标',node.metric,value=>{node.metric=value;changed();},{choices:Object.keys(draft.metrics).map(key=>[key,key])}),
        field('比较方式',node.op,value=>{node.op=value;changed();},{choices:[['lt','小于'],['le','小于等于'],['gt','大于'],['ge','大于等于'],['eq','等于'],['ne','不等于']]}),
        field('判断值',node.value,value=>{node.value=value;changed();},{min:-1e12,max:1e12}));box.append(fields);
    } else {
      const children=element('div',null,'condition-children');
      if(depth>=8) {children.textContent='已达到8层限制，请改为单项比较。';box.append(children);return box;}
      const list=mode==='not'?[node.not]:node[mode];
      list.forEach((child,index)=>{
        const wrapper=element('div');wrapper.append(conditionEditor(child,value=>{if(mode==='not')node.not=value;else list[index]=value;},depth+1));
        if(mode!=='not'&&list.length>1)wrapper.append(button('移除条件',()=>{list.splice(index,1);changed();renderRules();},'danger'));
        children.append(wrapper);
      });
      box.append(children);
      if(mode!=='not')box.append(button('添加比较条件',()=>{list.push(defaultCondition());changed();renderRules();}),
        button('添加条件组',()=>{list.push({all:[defaultCondition()]});changed();renderRules();}));
    }
    return box;
  }
  function renderRules() {
    $('rules').replaceChildren();
    draft.rules.forEach((rule,index)=>{
      const card=element('div',null,'rule-card'), heading=element('div',null,'item-heading');
      heading.append(element('h3','标签规则 '+(index+1)),button('删除规则',()=>{
        if(draft.rules.length===1) {message('至少保留一条标签规则。');return;}
        draft.rules.splice(index,1);changed();renderRules();
      },'danger'));
      const grid=element('div',null,'grid');
      grid.append(field('规则标识',rule.id,value=>{rule.id=value;changed();},{type:'text',maxLength:80}),
        field('输出业务标签',rule.label,value=>{rule.label=value;changed();},{type:'text',maxLength:100}),
        field('判断说明',rule.description,value=>{rule.description=value;changed();},{type:'text',maxLength:1000}));
      card.append(heading,grid,element('h3','命中条件'),conditionEditor(rule.when,value=>{rule.when=value;}));
      card.append(field('设置排除条件',Boolean(rule.unless),value=>{if(value)rule.unless=defaultCondition();else delete rule.unless;changed();renderRules();},{type:'checkbox'}));
      if(rule.unless)card.append(element('p','排除条件满足时，不生成此标签。','hint'),conditionEditor(rule.unless,value=>{rule.unless=value;}));
      $('rules').append(card);
    });
  }
  function render() {
    for(const [id,key] of [['configName','name'],['configId','id'],['configVersion','version'],['description','description']]) {
      $(id).value=draft[key];$(id).oninput=()=>{draft[key]=$(id).value;changed();};
    }
    renderScope();renderMetrics();renderRules();preview();
  }
  function renderLibrary() {
    $('savedConfig').replaceChildren(new Option('选择配置版本',''));
    for(const config of definitions)$('savedConfig').add(new Option(config.name+' · '+ref(config),ref(config)));
    const choices=$('runDefinitions');choices.replaceChildren(element('legend','选择一个或多个已保存版本'));
    for(const config of definitions) {
      choices.append(field(config.name+' · '+ref(config),selectedRuns.has(ref(config)),value=>{
        if(value) {for(const chosen of selectedRuns)if(chosen.split('@')[0]===config.id)selectedRuns.delete(chosen);selectedRuns.add(ref(config));}
        else selectedRuns.delete(ref(config));renderLibrary();
      },{type:'checkbox'}));
    }
  }
  async function api(path,body) {
    const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',cache:'no-store',
      headers:{'X-Voice-Tools-Session':data.session.token,...(body===undefined?{}:{'Content-Type':'application/json'})},
      body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok||!result.ok)throw Error(result.error||'本机服务返回错误，请检查连接。');
    return result;
  }
  function setBusy(value) {
    busy=value;
    for(const control of document.querySelectorAll('input,select,textarea,button'))control.disabled=value;
  }
  async function action(task) {if(busy)return;setBusy(true);try{await task();}catch(error){message(error.message);}finally{setBusy(false);}}
  function download(config) {
    const url=URL.createObjectURL(new Blob([JSON.stringify(config,null,2)+'\n'],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=ref(config)+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function save() {
    const config=validDraft();
    if(data.session) {
      const result=await api('definitions',{config});definitions=result.definitions;
      for(const chosen of selectedRuns)if(chosen.split('@')[0]===config.id)selectedRuns.delete(chosen);
      selectedRuns.add(ref(config));
      message('配置已保存到本机检测库：'+ref(config)+'。可以在下方运行检测。',true);
    } else {
      const previous=definitions.find(item=>ref(item)===ref(config));
      if(previous&&contract.stable(previous)!==contract.stable(config))throw Error('同一标识和版本已有不同内容，请使用新版本号。');
      if(!previous)definitions.push(config);
      localStorage.setItem(versionsKey,JSON.stringify(definitions));message('已保存到本浏览器。下载 JSON 后可在其他环境执行。',true);
    }
    renderLibrary();loadSaved(config);
  }
  $('definitionForm').onsubmit=event=>{event.preventDefault();action(save);};
  $('validateConfig').onclick=()=>action(async()=>{const config=validDraft();if(data.session)await api('validate',{config});message('配置有效：'+Object.keys(config.metrics).length+' 个指标，'+config.rules.length+' 条标签规则。',true);});
  $('exportConfig').onclick=()=>action(async()=>{download(validDraft());message('已下载当前配置 JSON；修改内容仍需保存为配置版本。',true);});
  $('discardConfig').onclick=discardDraft;
  $('newConfig').onclick=()=>{if(canSwitch()){startDraft(newConfig());$('example').value='';message('请填写新配置和标签规则。');}};
  $('copyConfig').onclick=()=>{if(!canSwitch())return;const config=clone(draft);config.version=(config.version.slice(0,65)+'-copy');while(definitions.some(item=>ref(item)===ref(config)))config.version=config.version.slice(0,65)+'-'+Date.now();startDraft(config);message('已复制配置。请设置新版本号并保存。');};
  $('savedConfig').onchange=()=>{const config=definitions.find(item=>ref(item)===$('savedConfig').value);if(!config)return;if(canSwitch())loadSaved(config);else $('savedConfig').value=dirty?'':baseline?ref(baseline):'';};
  data.templates.forEach((config,index)=>$('example').add(new Option(config.name,String(index))));
  $('example').onchange=()=>{if($('example').value==='')return;if(canSwitch())startDraft(data.templates[Number($('example').value)]);else $('example').value='';};
  $('importConfig').onchange=()=>action(async()=>{
    const file=$('importConfig').files[0];$('importConfig').value='';if(!file)return;
    if(dirty)throw Error('请先保存配置或放弃未保存修改，再导入。');
    if(file.size>1024*1024)throw Error('配置 JSON 不能超过1 MiB。');
    const config=normalize(contract.parse(await file.text()));if(data.session)await api('validate',{config});startDraft(config);message('配置已导入；所有指标和组合条件已保留，请检查后保存。');
  });
  $('windowKind').onchange=()=>{
    const kind=$('windowKind').value;draft.window={kind};
    if(kind!=='whole')Object.assign(draft.window,{length_s:5,include_partial:false});
    if(kind==='sliding')draft.window.step_s=5;
    if(kind==='after_activity')Object.assign(draft.window,{channel:0,delay_s:0,params:defaultParams('activity_total_s')});
    changed();renderScope();
  };
  $('addExclusion').onclick=()=>{if(draft.scope.exclude.length>=100){message('最多100个排除时段。');return;}draft.scope.exclude.push({start_s:0,end_s:1});changed();renderScope();};
  $('addMetric').onclick=()=>{if(Object.keys(draft.metrics).length>=32){message('最多32个指标。');return;}let index=1;while(Object.hasOwn(draft.metrics,'metric-'+index))index++;draft.metrics['metric-'+index]={kind:'rms_dbfs',channel:1,params:defaultParams('rms_dbfs')};changed();renderMetrics();renderRules();};
  $('addRule').onclick=()=>{if(draft.rules.length>=32){message('最多32条规则。');return;}let index=1;while(draft.rules.some(rule=>rule.id==='rule-'+index))index++;draft.rules.push({id:'rule-'+index,label:'新标签',description:'请说明此规则的业务含义',when:defaultCondition()});changed();renderRules();};
  $('runDetection').onclick=()=>action(async()=>{
    if(dirty||!baseline)throw Error('当前表单有未保存修改，请先保存新版本再运行。');
    if(!selectedRuns.size)throw Error('请选择至少一个已保存的配置版本。');
    $('runResult').textContent='正在检测，请稍候。';
    try {
      const result=await api('run',{definitions:[...selectedRuns],batch_id:'web-'+crypto.randomUUID()});
      const summary=result.summary;
      $('runResult').replaceChildren(element('p','使用配置：'+summary.definitions.join('、')),element('p',`批次 ${summary.batch_id}：${summary.recordings} 通录音，${summary.findings} 条标签，${summary.errors} 项错误，${summary.no_windows} 项没有可用窗口。`));
      const link=element('a','打开本次检测与标签复核报告');link.href=result.report_url;$('runResult').append(link);
      if(summary.status==='partial')$('runResult').append(element('p','部分录音未完整分析，请在报告中查看原因。','hint'));
      message('检测批次已保存；人工结论和历史结果均已保留。',true);
    } catch(error) {$('runResult').textContent='本次运行未完成。'+error.message;throw error;}
  });
  $('versionInfo').textContent='版本 '+data.version;
  $('mode').textContent=data.session?'已连接本机检测库，可保存版本并运行检测。':'离线配置页：可保存浏览器版本和下载 JSON，当前页面不执行录音检测。';
  if(data.session) {
    $('runPanel').hidden=false;$('saveConfig').textContent='保存到检测库';
    $('inputSummary').textContent='本次服务已指定 '+data.session.inputs.length+' 个录音文件，录音来源保持不变。';
    for(const path of data.session.inputs)$('inputFiles').append(element('li',path));
  } else {
    $('saveConfig').textContent='保存到浏览器';
    try {const saved=JSON.parse(localStorage.getItem(versionsKey)||'[]');for(const raw of saved){const config=normalize(raw);if(!definitions.some(item=>ref(item)===ref(config)))definitions.push(config);}}
    catch(error){message('本地版本记录未读取：'+error.message);}
  }
  renderLibrary();
  if(definitions.length) {
    draft=normalize(definitions[0]);baseline=clone(draft);$('savedConfig').value=ref(draft);
  } else draft=normalize(draft);
  render();
  // Restore only an explicit unsaved draft; never pretend it is an immutable saved version.
  try {
    const saved=localStorage.getItem(draftKey);
    if(saved){
      const value=JSON.parse(saved), restored=normalize(value.config);
      // Older drafts may name an imported or copied configuration as their baseline.
      const previous=value.baseline?normalize(value.baseline):null;
      baseline=previous?definitions.find(config=>contract.stable(config)===contract.stable(previous))||null:null;
      startDraft(restored);message('已恢复未保存草稿，请保存新版本或放弃修改。');
    }
  } catch(error){message('草稿无法恢复：'+error.message);}
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
})();
