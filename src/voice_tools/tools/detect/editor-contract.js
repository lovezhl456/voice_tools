'use strict';
// Validates the shipped Schema subset, then the detector's cross-field constraints.
// The Python validator remains authoritative for saving and running configurations.
(function(root) {
  function fail(message) { throw Error(message); }
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  function parse(text) {
    const result = JSON.parse(text);
    const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^{}\[\]:,\s]+/g) || [];
    const stack = [];
    tokens.forEach((token,index) => {
      if (token === '{' || token === '[') stack.push(token === '{' ? new Set() : null);
      else if (token === '}' || token === ']') stack.pop();
      else if (tokens[index+1] === ':' && token.startsWith('"')) {
        const key = JSON.parse(token), keys = stack[stack.length-1];
        if (keys.has(key)) fail('JSON 含重复字段：' + key);
        keys.add(key);
      }
    });
    return result;
  }
  function validateSchema(value, rule, schema, path='配置', depth=0) {
    if (depth > 80) fail('配置嵌套过深');
    if (rule.$ref) return validateSchema(value,schema.$defs[rule.$ref.split('/').pop()],schema,path,depth+1);
    const choices = rule.oneOf || rule.anyOf;
    if (choices) {
      let valid=0;
      for (const choice of choices) {
        try { validateSchema(value,choice,schema,path,depth+1); valid++; } catch (_) { /* Try the next declared variant. */ }
      }
      if (!valid || (rule.oneOf && valid !== 1)) fail(path + ' 的字段、类型或取值不符合格式，请检查配置');
    }
    if (Object.hasOwn(rule,'const') && value !== rule.const) fail(path + ' 必须为 ' + rule.const);
    if (rule.enum && !rule.enum.includes(value)) fail(path + ' 取值无效');
    const types = {object:object(value),array:Array.isArray(value),string:typeof value==='string',
      number:typeof value==='number'&&Number.isFinite(value),integer:Number.isInteger(value),boolean:typeof value==='boolean',null:value===null};
    if (rule.type && !types[rule.type]) fail(path + ' 类型无效，应为 ' + rule.type);
    if (typeof value === 'number' && (!Number.isFinite(value) || value < (rule.minimum ?? -Infinity) || value > (rule.maximum ?? Infinity))) fail(path + ' 数值超出允许范围');
    if (typeof value === 'string' && ([...value].length < (rule.minLength ?? 0) || [...value].length > (rule.maxLength ?? Infinity) || (rule.pattern && !new RegExp(rule.pattern).test(value)))) fail(path + ' 长度或字符格式无效');
    if (Array.isArray(value)) {
      if (value.length < (rule.minItems ?? 0) || value.length > (rule.maxItems ?? Infinity)) fail(path + ' 项数无效');
      if (rule.items) value.forEach((item,index)=>validateSchema(item,rule.items,schema,path+'['+index+']',depth+1));
    }
    if (object(value)) {
      const keys=Object.keys(value);
      if (keys.length < (rule.minProperties ?? 0) || keys.length > (rule.maxProperties ?? Infinity)) fail(path + ' 字段数量无效');
      for (const key of rule.required || []) if (!Object.hasOwn(value,key)) fail(path + ' 缺少 ' + key);
      for (const key of keys) {
        if (rule.propertyNames) validateSchema(key,rule.propertyNames,schema,path+'.指标标识',depth+1);
        const child=Object.hasOwn(rule.properties || {},key) ? rule.properties[key] : undefined;
        if (child) validateSchema(value[key],child,schema,path+'.'+key,depth+1);
        else if (rule.additionalProperties === false) fail(path + ' 不支持字段 ' + key);
        else if (object(rule.additionalProperties)) validateSchema(value[key],rule.additionalProperties,schema,path+'.'+key,depth+1);
      }
    }
  }
  function normalize(raw,catalog,schema) {
    validateSchema(raw,schema,schema);
    const value=clone(raw);
    for (const key of ['name','description']) if (Object.hasOwn(value,key) && !value[key].trim()) fail(key+' 不能为空白');
    value.description ??= '未提供业务说明';
    value.scope={start_s:0,end_s:null,skip_first_s:0,skip_last_s:0,exclude:[],...value.scope};
    if (value.scope.end_s !== null && value.scope.end_s <= value.scope.start_s) fail('分析结束时间必须大于开始时间');
    for (const span of value.scope.exclude) if (span.end_s <= span.start_s) fail('排除时段终点必须大于起点');
    const params=(kind,supplied)=>({...Object.fromEntries(Object.entries(catalog[kind].params).map(([key,spec])=>[key,spec.default])),...supplied});
    value.window ??= {kind:'whole'};
    if (value.window.kind !== 'whole') { value.window.length_s ??= 5; value.window.include_partial ??= false; }
    if (value.window.kind === 'sliding') value.window.step_s ??= value.window.length_s;
    if (value.window.kind === 'after_activity') {
      value.window.channel ??= 0; value.window.delay_s ??= 0;
      value.window.params=params('activity_total_s',value.window.params || {});
    }
    for (const metric of Object.values(value.metrics)) metric.params=params(metric.kind,metric.params || {});
    function condition(node,depth=0,budget={count:0}) {
      if (depth>8 || ++budget.count>128) fail('条件最多8层、128个节点');
      if (node.metric && !Object.hasOwn(value.metrics,node.metric)) fail('条件引用了不存在的指标：'+node.metric);
      for (const child of node.all || node.any || (node.not ? [node.not] : [])) condition(child,depth+1,budget);
    }
    const ids=new Set();
    for (const rule of value.rules) {
      if (ids.has(rule.id)) fail('规则标识重复：'+rule.id);
      ids.add(rule.id);
      if (!rule.label.trim() || (rule.description !== undefined && !rule.description.trim())) fail('标签或规则说明不能为空白');
      rule.description ??= rule.label;
      condition(rule.when); if (rule.unless) condition(rule.unless);
    }
    return value;
  }
  function stable(value) {
    if (Array.isArray(value)) return '['+value.map(stable).join(',')+']';
    if (object(value)) return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';
    return JSON.stringify(value);
  }
  const api={parse,normalize,stable};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.DetectionEditorContract=api;
})(globalThis);
