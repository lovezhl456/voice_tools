const {test, expect} = require('../browser/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawnSync} = require('node:child_process');
const site = path.join(process.env.VT_DETECT_OUTPUT, 'site');

function cli(arguments_, info, name) {
  const run = spawnSync(process.env.VT_DETECT_PYTHON, ['-m', 'voice_tools', '--json', ...arguments_],
    {encoding:'utf8', env:process.env});
  const result = {code:run.status, value:JSON.parse(run.stdout), stderr:run.stderr};
  fs.writeFileSync(info.outputPath(name + '.json'), JSON.stringify({arguments:arguments_, ...result}, null, 2));
  return result;
}

async function select(page, label) {
  await page.getByRole('tab', {name:label, exact:true}).click();
  await expect(page.getByRole('tabpanel').locator('.status')).toBeHidden();
  return page.frameLocator('section.panel:not([hidden]) iframe');
}

async function saveDownload(page, click, target) {
  const pending = page.waitForEvent('download');
  await click();
  await (await pending).saveAs(target);
  return JSON.parse(fs.readFileSync(target, 'utf8'));
}

test('H01 relocated CLI export loads five tabs and retains input, playback and navigation', async ({page}, info) => {
  const errors = [], failed = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {if (response.status() >= 400) failed.push(response.url());});
  // Reopen without a fragment after remembering a non-first tab.
  await page.goto('/moved-workbench/#guide');
  await expect(page.getByRole('tab', {selected:true})).toHaveText('使用说明');
  await page.goto('/moved-workbench/');
  await expect(page.getByRole('tab', {selected:true})).toHaveText('使用说明');
  await select(page, '跨主机任务');
  await page.goBack();
  await expect(page.getByRole('tab', {selected:true})).toHaveText('使用说明');
  await page.goForward();
  await expect(page.getByRole('tab', {selected:true})).toHaveText('跨主机任务');
  await expect(page.getByRole('tab')).toHaveCount(5);
  for (const label of ['检测与复核','整通质检','输出间隙','跨主机任务','使用说明']) {
    const frame = await select(page, label);
    await expect(frame.locator('h1')).toBeVisible();
    expect(await frame.locator('html').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  const detection = await select(page, '检测与复核');
  await detection.locator('#fileSearch').fill('quiet');
  await detection.locator('#play').click();
  await expect.poll(async () => detection.locator('#player').evaluate(audio => audio.paused)).toBe(false);
  await select(page, '使用说明');
  const hidden = page.frameLocator('#panel-detection iframe');
  expect(await hidden.locator('#player').evaluate(audio => audio.paused)).toBe(true);
  await select(page, '检测与复核');
  await expect(detection.locator('#fileSearch')).toHaveValue('quiet');
  await page.getByRole('tab', {name:'检测与复核',exact:true}).press('ArrowRight');
  await expect(page.getByRole('tab', {selected:true})).toHaveText('整通质检');
  await page.reload();
  await expect(page.getByRole('tab', {selected:true})).toHaveText('整通质检');
  await select(page, '输出间隙');
  await page.goBack();
  await expect(page.getByRole('tab', {selected:true})).toHaveText('整通质检');
  await expect(page.getByRole('tabpanel').locator('.status')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('workbench.png')});
  expect(errors).toEqual([]);
  expect(failed).toEqual([]);
});

test('H02 review exported inside a tab imports through CLI and reopens in a new workbench', async ({page}, info) => {
  await page.goto('/moved-workbench/#detection');
  const frame = await select(page, '检测与复核');
  await frame.locator('#reviewStatus').selectOption('corrected');
  await frame.locator('#reviewLabel').fill('多标签复核完成');
  await frame.locator('#reviewer').fill('workbench-e2e');
  await frame.locator('#reviewForm button[type=submit]').click();
  const file = info.outputPath('reviews.json');
  const packet = await saveDownload(page, () => frame.locator('#exportReviews').click(), file);
  expect(packet.reviews).toHaveLength(1);
  const db = info.outputPath('library.sqlite3');
  fs.copyFileSync(path.join(site,'library.sqlite3'), db);
  expect(cli(['detect','review-import',file,'--db',db], info,'review-import').code).toBe(0);
  const report = info.outputPath('reopened-report');
  expect(cli(['detect','report','--db',db,'--out',report,'--include-audio','--hide-paths'], info,'report').code).toBe(0);
  const name = 'reviewed-workbench-' + info.project.name;
  expect(cli(['workbench','build','--detection',report,'--out',path.join(site,name)], info,'workbench').code).toBe(0);
  await page.goto('/' + name + '/#detection');
  const reopened = await select(page, '检测与复核');
  await reopened.locator('#tags').fill('多标签复核完成');
  await expect(reopened.locator('#queue button')).toHaveCount(1);
  await expect(reopened.locator('#identity')).toContainText('复核版本 1');
});

test('H03 empty tabs are explicit and offline task export can be reimported', async ({page}, info) => {
  await page.goto(pathToFileURL(path.join(site,'empty-workbench/index.html')).href);
  for (const label of ['检测与复核','整通质检','输出间隙']) {
    const frame = await select(page,label);
    await expect(frame.getByText('本次导出未提供这个页面的数据。')).toBeVisible();
  }
  let frame = await select(page,'跨主机任务');
  await frame.locator('#title').fill('离线多标签任务');
  await frame.locator('#title').blur();
  const file = info.outputPath('task.json');
  const task = await saveDownload(page, () => frame.getByRole('button',{name:'导出任务说明',exact:true}).click(), file);
  expect(task.title).toBe('离线多标签任务');
  await page.reload();
  frame = await select(page,'跨主机任务');
  await frame.locator('#import-file').setInputFiles(file);
  await expect(frame.locator('#title')).toHaveValue('离线多标签任务');
  await page.screenshot({path:info.outputPath('offline-task.png')});
});

test('H04 invalid directories are refused without overwrites and HTTP failure can recover', async ({page}, info) => {
  const workspace = info.outputPath('invalid');
  fs.mkdirSync(workspace);
  const source = path.join(workspace,'source');
  fs.mkdirSync(source);
  const output = path.join(workspace,'output');
  let args = ['workbench','build','--detection',source,'--out',output];
  expect(cli(args,info,'missing-entry').code).toBe(2);
  expect(fs.existsSync(output)).toBe(false);
  fs.writeFileSync(path.join(source,'index.html'), '<h1>local export</h1>');
  args = ['workbench','build','--detection',source,'--out',path.join(source,'nested')];
  expect(cli(args,info,'nested-output').code).toBe(2);
  expect(fs.existsSync(path.join(source,'nested'))).toBe(false);
  fs.symlinkSync(path.join(source,'index.html'), path.join(source,'linked.html'));
  expect(cli(['workbench','build','--detection',source,'--out',output],info,'symlink').code).toBe(2);
  expect(fs.existsSync(output)).toBe(false);
  fs.mkdirSync(output);
  fs.writeFileSync(path.join(output,'keep.txt'),'do not overwrite');
  expect(cli(['workbench','build','--out',output],info,'nonempty').code).toBe(2);
  expect(fs.readFileSync(path.join(output,'keep.txt'),'utf8')).toBe('do not overwrite');
  await page.route('**/moved-workbench/guide.html', route => route.fulfill({status:503, body:'unavailable'}));
  await page.goto('/moved-workbench/#guide');
  await expect(page.getByText('这个页面暂时无法打开，请重试。')).toBeVisible();
  await page.unroute('**/moved-workbench/guide.html');
  await page.getByRole('button',{name:'重新加载',exact:true}).click();
  await expect(page.getByRole('tabpanel').locator('.status')).toBeHidden();
  await expect(page.frameLocator('#panel-guide iframe').locator('h1')).toHaveText('一个入口，多个工具。');
});
