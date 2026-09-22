const {test,expect}=require('../browser/node_modules/@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');

function cli(args) {
  const run=spawnSync(process.env.VT_DETECT_PYTHON,['-m','voice_tools','--json','detect',...args],{encoding:'utf8',env:process.env});
  return {code:run.status,result:JSON.parse(run.stdout)};
}
async function openEditor(page,live=false) {
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  if(live) await page.goto(process.env.VT_EDITOR_URL);
  else {await page.goto('/qa/review.html');await page.getByRole('link',{name:'定义指标与标签',exact:true}).click();}
  await expect(page.getByRole('heading',{name:'定义指标与标签',exact:true})).toBeVisible();
  return errors;
}
async function newConfig(page,id,label='测试标签') {
  await page.getByRole('button',{name:'新建配置',exact:true}).click();
  await page.getByLabel('配置标识',{exact:true}).fill(id);
  await page.getByLabel('配置名称',{exact:true}).fill('界面创建 '+id);
  await page.getByLabel('输出业务标签',{exact:true}).fill(label);
}
async function download(page,file) {
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'下载配置 JSON',exact:true}).click();
  await (await pending).saveAs(file);return JSON.parse(fs.readFileSync(file,'utf8'));
}

test('E01 original review entry opens a working offline editor and exported config executes',async({page},info)=>{
  const errors=await openEditor(page);
  await newConfig(page,'offline-'+info.project.name,'离线配置标签');
  await page.getByRole('button',{name:'检查配置',exact:true}).click();
  await expect(page.locator('#message')).toContainText('配置有效');
  await page.getByRole('button',{name:'保存到浏览器',exact:true}).click();
  await expect(page.locator('#message')).toContainText('已保存到本浏览器');
  const file=info.outputPath('config.json');const config=await download(page,file);
  expect(config.rules[0].label).toBe('离线配置标签');expect(cli(['config-check',file]).code).toBe(0);
  const db=info.outputPath('offline.sqlite3');
  const run=cli(['run',path.join(process.env.VT_DETECT_OUTPUT,'site/inputs'),'--db',db,'--config',file]);
  expect(run.code).toBe(1);expect(run.result.summary.findings).toBe(2);
  await page.reload();await page.getByRole('combobox',{name:'选择已保存配置',exact:true}).selectOption(config.id+'@1');
  await expect(page.getByLabel('输出业务标签',{exact:true})).toHaveValue('离线配置标签');
  await expect(page.locator('#runPanel')).toBeHidden();expect(errors).toEqual([]);
});

test('E02 visual metric and AND rules save to library and run into searchable labels',async({page},info)=>{
  const errors=await openEditor(page,true);const id='live-'+info.project.name;
  await newConfig(page,id,'界面安静候选');
  const metric=page.locator('.metric-card').first();
  await metric.getByLabel('指标标识',{exact:true}).fill('quiet_time');await metric.getByLabel('指标标识',{exact:true}).blur();
  await metric.getByRole('combobox',{name:'指标类型',exact:true}).selectOption('silence_total_s');
  await metric.getByLabel('活动／静音阈值（dBFS）',{exact:true}).fill('-45');
  const rule=page.locator('.rule-card').first();
  await rule.getByRole('combobox',{name:'比较方式',exact:true}).selectOption('ge');await rule.getByLabel('判断值',{exact:true}).fill('2');
  await page.getByRole('button',{name:'添加指标',exact:true}).click();
  await rule.getByRole('combobox',{name:'条件类型',exact:true}).first().selectOption('all');
  await rule.getByRole('button',{name:'添加比较条件',exact:true}).click();
  const second=rule.locator('.leaf').nth(1);
  await second.getByRole('combobox',{name:'比较指标',exact:true}).selectOption('metric-1');
  await second.getByRole('combobox',{name:'比较方式',exact:true}).selectOption('gt');await second.getByLabel('判断值',{exact:true}).fill('-100');
  await page.getByRole('button',{name:'保存到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText('配置已保存到本机检测库');
  await page.getByRole('button',{name:'运行检测并生成报告',exact:true}).click();
  await expect(page.locator('#runResult')).toContainText('3 通录音，1 条标签，0 项错误');
  await page.getByRole('link',{name:'打开本次检测与标签复核报告',exact:true}).click();
  await page.getByRole('combobox',{name:'标签（逗号分隔）',exact:true}).fill('界面安静候选');
  await page.getByRole('combobox',{name:'检测定义',exact:true}).selectOption(id);
  expect(await page.locator('#queue button').count()).toBeGreaterThanOrEqual(1);
  await expect(page.locator('#waveform canvas').first()).toBeVisible();
  await page.getByRole('link',{name:'定义指标与标签',exact:true}).click();
  await page.getByRole('combobox',{name:'选择已保存配置',exact:true}).selectOption(id+'@1');
  await expect(page.getByLabel('输出业务标签',{exact:true})).toHaveValue('界面安静候选');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('editor.png'),fullPage:true});expect(errors).toEqual([]);
});

test('E03 version collisions, draft recovery and dirty-run guard preserve saved configuration',async({page},info)=>{
  const errors=await openEditor(page,true);const id='versions-'+info.project.name;
  await newConfig(page,id,'旧标签');await page.getByRole('button',{name:'保存到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText('已保存');
  await page.getByLabel('输出业务标签',{exact:true}).fill('新标签');
  await page.getByRole('button',{name:'运行检测并生成报告',exact:true}).click();await expect(page.locator('#message')).toContainText('未保存修改');
  await page.getByRole('button',{name:'保存到检测库',exact:true}).click();await expect(page.locator('#message')).toContainText('请使用新版本');
  await page.reload();await expect(page.locator('#message')).toContainText('恢复未保存草稿');
  await expect(page.getByLabel('输出业务标签',{exact:true})).toHaveValue('新标签');
  await page.getByLabel('版本',{exact:true}).fill('2');await page.getByRole('button',{name:'保存到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText(id+'@2');
  await expect(page.locator('#runDefinitions input:checked')).toHaveCount(1);
  await page.getByRole('combobox',{name:'选择已保存配置',exact:true}).selectOption(id+'@1');
  await expect(page.getByLabel('输出业务标签',{exact:true})).toHaveValue('旧标签');
  expect(errors).toEqual([]);
});

test('E04 nested imported config survives form export and invalid JSON never replaces it',async({page},info)=>{
  const errors=await openEditor(page);
  const config=await page.locator('#editorData').evaluate(el=>JSON.parse(el.textContent).templates[0]);
  config.scope.exclude=[{start_s:1,end_s:2}];
  config.rules[0].when={all:[config.rules[0].when,{not:{any:[{metric:'ai_quiet',op:'lt',value:.1},{metric:'user_speaking',op:'gt',value:.9}]}}]};
  const file=info.outputPath('nested.json');fs.writeFileSync(file,JSON.stringify(config));
  await page.getByLabel('导入配置 JSON',{exact:true}).setInputFiles(file);
  await expect(page.locator('#message')).toContainText('配置已导入');
  await page.getByRole('button',{name:'保存到浏览器',exact:true}).click();await expect(page.locator('#message')).toContainText('已保存');
  const exported=await download(page,info.outputPath('exported.json'));expect(exported).toEqual(config);
  expect(cli(['config-check',info.outputPath('exported.json')]).code).toBe(0);
  await page.locator('.metric-card').first().getByRole('button',{name:'删除指标',exact:true}).click();
  await expect(page.locator('#message')).toContainText('仍被规则引用');
  const duplicate=info.outputPath('duplicate.json');fs.writeFileSync(duplicate,'{"id":"one","id":"two"}');
  await page.getByLabel('导入配置 JSON',{exact:true}).setInputFiles(duplicate);await expect(page.locator('#message')).toContainText('重复字段');
  await expect(page.getByLabel('配置标识',{exact:true})).toHaveValue(config.id);
  expect(errors).toEqual([]);
});
