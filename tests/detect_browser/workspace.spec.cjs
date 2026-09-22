const {test,expect}=require('../browser/node_modules/@playwright/test');
const path=require('node:path');
const fs=require('node:fs');
const {spawn,spawnSync}=require('node:child_process');
let processHandle;
async function start(page,info,standards=false){
  const root=info.outputPath('workspace');
  const setup=spawnSync(process.env.VT_DETECT_PYTHON,[path.join(__dirname,'workspace_fixtures.py'),root,...(standards?['--standards']:[])],{env:process.env,encoding:'utf8'});
  expect(setup.status,setup.stderr).toBe(0);
  processHandle=spawn(process.env.VT_DETECT_PYTHON,['-m','voice_tools','--json','detect','serve',path.join(root,'inputs'),'--db',path.join(root,'library.sqlite3'),'--out',path.join(root,'service'),'--port','0'],{env:process.env});
  const address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('workspace startup timed out')),15000);let buffer='';
    processHandle.stdout.on('data',data=>{buffer+=data; if(buffer.includes('\n')){clearTimeout(timer);try{resolve(JSON.parse(buffer.split('\n')[0]).url);}catch(e){reject(e);}}});
    processHandle.once('exit',code=>{clearTimeout(timer);reject(Error('server exited '+code));});
  });
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(address+'workspace');
  await expect(page.locator('#inputCount')).toContainText('2 个');
  return {address,root,errors};
}
test.afterEach(async()=>{if(processHandle){processHandle.kill('SIGTERM');await new Promise(resolve=>processHandle.once('exit',resolve));processHandle=null;}});
async function runAndReview(page){
  await page.locator('#definitions input[value="level@1"]').check();
  await page.getByRole('button',{name:'开始质检',exact:true}).click();
  await page.getByRole('link',{name:'打开本次录音复核',exact:true}).click();
  await expect(page.locator('#liveToolbar')).toBeVisible();
  await expect(page.locator('#queue button')).toHaveCount(1);
}

test('W01 daily rules, online review, false-positive reason, audit and reopening persist',async({page},info)=>{
  const {address,errors}=await start(page,info);
  await page.locator('#definitions input[value="level@1"]').check();
  await page.getByLabel('工作区名称',{exact:true}).fill('每日录音质检');
  await page.getByRole('button',{name:'保存为日常规则组合',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('已保存');await page.reload();
  await expect(page.locator('#definitions input[value="level@1"]')).toBeChecked();
  await runAndReview(page);
  await page.locator('#reviewStatus').selectOption('false_positive');await page.locator('#reviewer').fill('人工复核');
  await page.getByRole('button',{name:'保存到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText('误报原因');
  await page.locator('#reviewStatus').selectOption('confirmed');await page.locator('#comment').fill('确认输出音量低');
  await page.getByRole('button',{name:'保存到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText('已保存到检测库');await page.reload();
  await expect(page.locator('#reviewStatus')).toHaveValue('confirmed');
  await page.getByRole('button',{name:'试听这条未命中录音',exact:true}).click();
  await expect(page.locator('#waveform canvas').first()).toBeVisible();
  await page.locator('#player').evaluate(async audio=>{await audio.play();});
  await expect.poll(()=>page.locator('#player').evaluate(audio=>audio.currentTime)).toBeGreaterThan(0);
  await page.locator('#player').evaluate(audio=>audio.pause());
  await page.locator('#manualReviewer').fill('人工抽检');await page.locator('#manualComment').fill('此范围音量正常');
  await page.getByRole('button',{name:'保存正常范围到标准样本',exact:true}).click();
  await expect(page.locator('#message')).toContainText('试听并明确');await page.locator('#normalChecked').check();
  await page.getByRole('button',{name:'保存正常范围到标准样本',exact:true}).click();
  await expect(page.locator('#message')).toContainText('正常范围已保存');
  await page.getByRole('link',{name:'返回质检工作区'}).click();
  await expect(page.locator('#sampleList .row')).toHaveCount(2);
  await page.getByRole('button',{name:'开始质检',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('没有需要检测');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('workspace.png'),fullPage:true});expect(errors).toEqual([]);
});

test('W02 fixed samples reveal a regression and explicit daily selection survives reopening',async({page},info)=>{
  const {errors}=await start(page,info,true);
  await page.getByLabel('固定样本集名称',{exact:true}).fill('固定回归样本');
  await page.getByRole('button',{name:'冻结当前人工标准',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('固定样本集已保存');
  await page.locator('#before').selectOption('level@1');await page.locator('#after').selectOption('level@2');
  await page.getByRole('button',{name:'运行新旧版本对比',exact:true}).click();
  await expect(page.locator('#comparisons')).toContainText('对比完成');
  const miss=page.locator('#comparisons tr').filter({hasText:'漏检问题片段'});
  await expect(miss.locator('td').nth(1)).toHaveText('0');await expect(miss.locator('td').nth(2)).toHaveText('1');
  await expect(page.locator('#dailyRules')).toContainText('尚未选择');
  // Choosing a version is an explicit user operation, even when it introduces a regression.
  await page.getByRole('button',{name:'将此新版本选为日常规则',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('已明确选用');await page.reload();
  await expect(page.locator('#definitions input[value="level@2"]')).toBeChecked();
  await page.getByRole('button',{name:'查看逐录音片段与保存对比',exact:true}).click();
  const download=page.waitForEvent('download');await page.getByRole('link',{name:'下载本次对比 JSON',exact:true}).click();
  const file=info.outputPath('comparison.json');await(await download).saveAs(file);
  expect(JSON.parse(fs.readFileSync(file)).totals.after.misses).toBe(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('comparison.png'),fullPage:true});expect(errors).toEqual([]);
});

test('W03 trial selection, manual miss and sample revision keep frozen evidence unchanged',async({page},info)=>{
  const {errors}=await start(page,info);
  await page.locator('#definitions input[value="level@2"]').check();
  await page.getByRole('combobox',{name:'检测方式',exact:true}).selectOption('trial');
  await page.getByText('查看录音 / 勾选试跑范围',{exact:true}).click();
  await page.locator('#inputs label').filter({hasText:'quiet.wav'}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'开始质检',exact:true}).click();
  await page.getByRole('link',{name:'打开本次录音复核',exact:true}).click();
  await expect(page.locator('#summary')).toContainText('1 通录音');
  await page.getByRole('button',{name:'试听这条未命中录音',exact:true}).click();
  await page.locator('#manualReviewer').fill('复核');await page.locator('#manualComment').fill('阈值过低导致漏检');
  await page.getByRole('button',{name:'保存漏检到检测库',exact:true}).click();
  await expect(page.locator('#message')).toContainText('已保存到检测库');
  await page.getByRole('link',{name:'返回质检工作区'}).click();
  await expect(page.locator('#sampleList')).toContainText('存在问题');
  await page.getByLabel('固定样本集名称',{exact:true}).fill('冻结后修订');await page.getByRole('button',{name:'冻结当前人工标准',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('已保存');
  await page.getByRole('button',{name:'修订',exact:true}).click();await page.locator('#sampleEnd').fill('4');
  await page.locator('#sampleChecked').check();await page.getByRole('button',{name:'保存样本修订',exact:true}).click();
  await expect(page.locator('#sampleList')).toContainText('0–4 秒');
  await page.locator('#before').selectOption('level@1');await page.locator('#after').selectOption('level@2');
  await page.getByRole('button',{name:'运行新旧版本对比',exact:true}).click();await expect(page.locator('#comparisons')).toContainText('对比完成');
  await page.getByRole('button',{name:'查看逐录音片段与保存对比',exact:true}).click();
  await expect(page.locator('#comparisons pre')).toContainText('"end_s": 8');expect(errors).toEqual([]);
});
