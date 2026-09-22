const {test,expect} = require('../browser/node_modules/@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const output=process.env.VT_DETECT_OUTPUT;
const site=path.join(output,'site');
function cli(args) {
  const run=spawnSync(process.env.VT_DETECT_PYTHON,['-m','voice_tools','--json','detect',...args],{encoding:'utf8',env:process.env});
  return {code:run.status,result:JSON.parse(run.stdout)};
}
async function open(page,route='/report/') {
  const errors=[];
  page.on('pageerror',(error)=>errors.push(error.message));
  page.on('dialog',(dialog)=>dialog.dismiss());
  await page.goto(route);
  await expect(page.locator('#summary')).toContainText('3 通录音');
  return errors;
}
async function download(page,button,file) {
  const promise=page.waitForEvent('download');
  await page.locator(button).click();
  const item=await promise; await item.saveAs(file);
  return JSON.parse(fs.readFileSync(file,'utf8'));
}

test('D01 filters versions, multiple tags and exports the visible result',async({page})=>{
  const errors=await open(page);
  await page.locator('#batch').selectOption('baseline');
  await page.locator('#tags').fill('AI低音量,待核实');
  await page.locator('#tagMode').selectOption('all');
  await expect(page.locator('#queue button')).toHaveCount(4);
  await page.locator('#fileSearch').fill('quiet');
  await expect(page.locator('#queue button')).toHaveCount(2);
  await page.locator('#version').selectOption('2');
  await expect(page.locator('#queue button')).toHaveCount(0);
  await page.locator('#version').selectOption('1');
  const saved=await download(page,'#exportResults',test.info().outputPath('query.json'));
  expect(saved).toHaveLength(2); expect(saved.every(row=>row.batch_id==='baseline')).toBe(true);
  await page.locator('#clearFilters').click();
  expect(await page.locator('#queue button').count()).toBeGreaterThan(4);
  expect(errors).toEqual([]);
});

test('D02 shared waveform, channel, volume, seeking and interval playback',async({page})=>{
  const errors=await open(page);
  const buttonBoxes=await page.locator('#queue button').evaluateAll(items=>items.map(item=>item.getBoundingClientRect().height));
  expect(buttonBoxes.every(height=>height>=40)).toBe(true);
  await expect(page.locator('#waveform canvas').first()).toBeVisible();
  await page.locator('#zoomIn').click(); await expect(page.locator('#zoomValue')).toContainText('2');
  await page.locator('#waveGain').selectOption('4'); await expect(page.locator('#waveScale')).toContainText('4');
  await page.locator('#channel').selectOption('right');
  await expect(page.locator('#player')).toHaveAttribute('src',/-ch1.wav$/);
  await page.locator('#volume').fill('0.5'); await page.locator('#mute').check();
  expect(await page.locator('#player').evaluate(el=>[el.volume,el.muted])).toEqual([0.5,true]);
  await page.locator('#mute').uncheck(); await page.locator('#loop').uncheck();
  await page.locator('#start').fill('1'); await page.locator('#end').fill('1.5'); await page.locator('#end').blur();
  await page.locator('#play').click();
  await expect.poll(()=>page.locator('#player').evaluate(el=>el.currentTime)).toBeGreaterThan(1.05);
  await expect.poll(()=>page.locator('#player').evaluate(el=>el.paused)).toBe(true);
  const current=await page.locator('#player').evaluate(el=>el.currentTime);
  expect(current).toBeGreaterThanOrEqual(1.49); expect(current).toBeLessThan(1.7);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('review.png'),fullPage:true});
  expect(errors).toEqual([]);
});

test('D03 correction draft survives reopening and imports into the installed CLI',async({page},info)=>{
  const errors=await open(page);
  await page.locator('#reviewStatus').selectOption('corrected'); await page.locator('#reviewLabel').fill('已核实问题');
  await page.locator('#reviewStart').fill('1'); await page.locator('#reviewEnd').fill('3');
  await page.locator('#reviewer').fill('browser-tester'); await page.locator('#comment').fill('听后修正');
  await page.locator('#queue button').nth(1).click(); await expect(page.locator('#message')).toContainText('保存');
  await page.locator('#reviewForm button[type=submit]').click();
  const file=info.outputPath('reviews.json'); const packet=await download(page,'#exportReviews',file);
  expect(packet.reviews).toHaveLength(1);
  await page.reload(); await expect(page.locator('#progress')).toContainText('1 条复核草稿');
  await page.locator('#importDraft').setInputFiles(file);
  const db=info.outputPath('library.sqlite3'); fs.copyFileSync(path.join(site,'library.sqlite3'),db);
  expect(cli(['review-import',file,'--db',db]).code).toBe(0);
  expect(cli(['review-import',file,'--db',db]).code).toBe(2);
  const target=path.join(site,'reopened-'+info.project.name);
  expect(cli(['report','--db',db,'--out',target,'--include-audio','--hide-paths']).code).toBe(0);
  await page.locator('#exportReviews').click();
  await page.goto('/reopened-'+info.project.name+'/');
  await page.locator('#tags').fill('已核实问题');
  await expect(page.locator('#queue button')).toHaveCount(1);
  await expect(page.locator('#identity')).toContainText('复核版本 1');
  await expect(page.locator('#reviewStart')).toHaveValue('1');
  expect(errors).toEqual([]);
});

test('D04 manual missed segment, unknown source, and hostile labels remain data',async({page},info)=>{
  const errors=await open(page);
  await page.locator('#manualEvaluation').selectOption({label:await page.locator('#manualEvaluation option').filter({hasText:'loud.wav'}).first().textContent()});
  await page.locator('#listenManual').click(); await expect(page.locator('#reviewForm')).toBeHidden();
  await expect(page.locator('#waveform canvas').first()).toBeVisible();
  await page.locator('#manualLabel').fill('人工补标'); await page.locator('#manualStart').fill('2'); await page.locator('#manualEnd').fill('4');
  await page.locator('#manualReviewer').fill('tester'); await page.locator('#manualComment').fill('自动规则漏检');
  await page.locator('#manualForm button[type=submit]').click(); await expect(page.locator('#manualQueue li')).toHaveCount(1);
  const file=info.outputPath('manual.json'); const packet=await download(page,'#exportReviews',file);
  expect(packet.manual).toHaveLength(1);
  const db=info.outputPath('manual.sqlite3'); fs.copyFileSync(path.join(site,'library.sqlite3'),db);
  expect(cli(['review-import',file,'--db',db]).code).toBe(0);
  expect(cli(['query','--db',db,'--tag','人工补标']).result.summary.count).toBe(1);
  await page.goto('/no-audio/'); await expect(page.locator('#play')).toBeDisabled();
  await expect(page.locator('#waveform canvas').first()).toBeVisible();
  await page.locator('#tags').fill('</script><img src=x onerror=alert(1)>');
  await expect(page.locator('#queue button')).toHaveCount(1);
  await expect(page.locator('#queue img')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('D05 invalid ranges, mismatched identity and stale draft are rejected',async({page},info)=>{
  const errors=await open(page);
  await page.locator('#reviewer').fill('tester'); await page.locator('#reviewStatus').selectOption('corrected');
  await page.locator('#reviewEnd').fill('100'); await page.locator('#reviewForm button[type=submit]').click();
  await expect(page.locator('#message')).toContainText('录音范围');
  await page.locator('#discard').click();
  const fixture=JSON.parse(fs.readFileSync(path.join(site,'report/results.json'),'utf8'));
  const row=fixture.findings[0];
  const packet={schema_version:'1.0',library_id:fixture.library_id,reviews:[{finding_id:row.id,expected_revision:99,status:'confirmed',reviewer:'tester',comment:'',label:row.label,start_s:row.start_s,end_s:row.end_s}],manual:[]};
  const file=info.outputPath('stale.json'); fs.writeFileSync(file,JSON.stringify(packet));
  await page.locator('#importDraft').setInputFiles(file); await expect(page.locator('#message')).toContainText('版本不匹配');
  packet.library_id='other'; fs.writeFileSync(file,JSON.stringify(packet));
  await page.locator('#importDraft').setInputFiles(file); await expect(page.locator('#message')).toContainText('身份不匹配');
  await expect(page.locator('#progress')).toContainText('0 条复核草稿'); expect(errors).toEqual([]);
});
