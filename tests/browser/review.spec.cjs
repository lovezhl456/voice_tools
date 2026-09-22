const {test: base, expect} = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

// Every case fails on a browser error or an unexpected network dependency.
const test = base.extend({
  page: async ({page, baseURL}, use) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.route('**/*', route => {
      const url = route.request().url();
      if (/^https?:/.test(url) && new URL(url).origin !== new URL(baseURL).origin) {
        errors.push(`Unexpected external request: ${url}`);
        return route.abort();
      }
      return route.continue();
    });
    await use(page);
    expect(errors, 'browser errors / external requests').toEqual([]);
  },
});

const queue = page => page.getByRole('complementary').getByRole('button');
const search = page => page.getByRole('searchbox', {name: '检索录音', exact: true});
const player = page => page.locator('audio');
const start = page => page.getByRole('spinbutton', {name: '试听起点（秒）', exact: true});
const end = page => page.getByRole('spinbutton', {name: '试听终点（秒）', exact: true});

async function waveReady(page) {
  await expect(page.getByRole('button', {name: '放大波形', exact: true})).toBeEnabled();
}

async function expectDrawing(page, channels = 2) {
  await waveReady(page);
  const wave = page.getByRole('group', {name: channels === 2 ? '同步双轨录音波形' : '单轨录音波形'});
  await expect(wave).toBeVisible();
  await expect.poll(() => wave.locator('canvas').count()).toBeGreaterThanOrEqual(channels * 2);
  const painted = await wave.locator('canvas').evaluateAll(canvases => canvases.some(canvas => {
    if (!canvas.width || !canvas.height) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  }));
  expect(painted, 'waveform canvas must contain rendered pixels').toBe(true);
}

async function setRange(page, from, until) {
  await start(page).fill(String(from));
  await start(page).dispatchEvent('change');
  await end(page).fill(String(until));
  await end(page).dispatchEvent('change');
}

async function expectPlaying(page) {
  await expect.poll(() => player(page).evaluate(audio => !audio.paused && audio.readyState >= 2)).toBe(true);
}

async function pause(page) {
  await page.getByRole('button', {name: '暂停试听', exact: true}).click();
  await expect.poll(() => player(page).evaluate(audio => audio.paused)).toBe(true);
}

async function adjustVolumeAndMute(page) {
  const volume = page.getByRole('slider', {name: '试听音量', exact: true});
  await volume.focus();
  await page.keyboard.press('Home');
  for (let step = 0; step < 7; step++) await page.keyboard.press('ArrowRight');
  await expect.poll(() => player(page).evaluate(audio => audio.volume)).toBeCloseTo(.35, 3);
  await page.getByRole('checkbox', {name: '静音', exact: true}).check();
  await expect.poll(() => player(page).evaluate(audio => audio.muted)).toBe(true);
}

test('R01 directory and fuzzy search retain the whole-call exception queue', async ({page}) => {
  await page.goto('/');
  await page.getByRole('link', {name: 'main/report.html', exact: true}).click();
  await expect(queue(page)).toHaveCount(6);
  await page.getByRole('combobox', {name: '查看范围', exact: true}).selectOption('all');
  await expect(queue(page)).toHaveCount(8);
  await search(page).fill('MISSING CALL');
  await expect(queue(page)).toHaveCount(1);
  await expect(queue(page).first()).toContainText('missing');
  await search(page).fill('');
  const directory = page.getByRole('combobox', {name: '目录', exact: true});
  const missing = await directory.locator('option').evaluateAll(options => options.find(option => option.value.endsWith('/missing')).value);
  await directory.selectOption(missing);
  await expect(queue(page)).toHaveCount(1);
  await page.getByRole('combobox', {name: '查看范围', exact: true}).selectOption('AUTO_PASS');
  await expect(queue(page)).toHaveCount(0);
  await directory.selectOption('');
  await expect(queue(page)).toHaveCount(2);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('R02 waveform, roles, zoom and adjustable range remain available', async ({page}, testInfo) => {
  await page.goto('/main/report.html');
  await expectDrawing(page);
  await expect(page.locator('#leftTitle')).toContainText('用户');
  await expect(page.locator('#rightTitle')).toContainText('AI');
  await page.getByRole('button', {name: '放大波形', exact: true}).click();
  await expect(page.locator('#zoomValue')).toHaveText('×2');
  await page.getByRole('button', {name: '缩小波形', exact: true}).click();
  await expect(page.locator('#zoomValue')).toHaveText('全长');
  const volume = await player(page).evaluate(audio => audio.volume);
  await page.getByRole('combobox', {name: '显示增益', exact: true}).selectOption('4');
  await expect(page.locator('#waveScale')).toContainText('×4');
  expect(await player(page).evaluate(audio => audio.volume)).toBe(volume);
  const before = Number(await start(page).inputValue());
  const handle = page.getByRole('slider', {name: '拖动试听起点', exact: true});
  await handle.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(async () => Number(await start(page).inputValue())).toBeCloseTo(before + .1, 3);
  await handle.scrollIntoViewIfNeeded();
  const box = await handle.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  // The shared component deliberately waits 100 ms before a coarse-pointer drag.
  if (testInfo.project.name === 'mobile') await page.waitForTimeout(150);
  await page.mouse.move(box.x + box.width / 2 + 20, box.y + box.height / 2, {steps: 6});
  await page.mouse.up();
  expect(Number(await start(page).inputValue())).toBeGreaterThan(before + .1);
  await page.getByRole('button', {name: '当前范围', exact: true}).click();
  await expect(page.locator('#zoomValue')).not.toHaveText('全长');
  await page.getByRole('button', {name: '全长', exact: true}).click();
  const wave = page.getByRole('group', {name: '同步双轨录音波形'});
  await wave.scrollIntoViewIfNeeded();
  const bounds = await wave.boundingBox();
  if (testInfo.project.name === 'mobile') await page.touchscreen.tap(bounds.x + bounds.width * .6, bounds.y + 30);
  else await wave.click({position: {x: bounds.width * .6, y: 30}});
  await expect.poll(() => player(page).evaluate(audio => audio.currentTime)).toBeGreaterThan(1);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('waveform.png'), fullPage: true});
});

test('R03 evidence selection, looping, stop-at-end and channel switching work', async ({page}) => {
  await page.goto('/main/report.html');
  await search(page).fill('missing');
  await expectDrawing(page);
  await page.getByRole('button', {name: '定位 0.50–7.50 秒', exact: true}).click();
  await expectPlaying(page);
  await expect(start(page)).toHaveValue('0.5');
  await expect(end(page)).toHaveValue('7.5');
  await adjustVolumeAndMute(page);
  await page.getByRole('combobox', {name: '试听声道', exact: true}).selectOption('left');
  await expect.poll(() => player(page).evaluate(audio => audio.currentSrc.endsWith('-left.wav') && !audio.paused)).toBe(true);
  expect(await player(page).evaluate(audio => [audio.volume, audio.muted])).toEqual([.35, true]);
  await page.getByRole('checkbox', {name: '静音', exact: true}).uncheck();
  await expect.poll(() => player(page).evaluate(audio => audio.muted)).toBe(false);
  await expectDrawing(page);
  await pause(page);
  await setRange(page, 2, 2.35);
  await page.getByRole('checkbox', {name: '循环所选区间', exact: true}).uncheck();
  await page.getByRole('button', {name: '播放试听', exact: true}).click();
  await expect.poll(() => player(page).evaluate(audio => audio.paused && audio.currentTime >= 2.34 && audio.currentTime < 2.4)).toBe(true);
  await player(page).evaluate(audio => {
    audio.dataset.seekCount = '0';
    audio.addEventListener('seeked', () => { audio.dataset.seekCount = String(Number(audio.dataset.seekCount) + 1); });
  });
  await page.getByRole('checkbox', {name: '循环所选区间', exact: true}).check();
  await page.getByRole('button', {name: '播放试听', exact: true}).click();
  await expect.poll(() => player(page).evaluate(audio => Number(audio.dataset.seekCount))).toBeGreaterThanOrEqual(2);
  await expectPlaying(page);
  await pause(page);
  await search(page).fill('late');
  expect(await player(page).evaluate(audio => audio.volume)).toBeCloseTo(.35, 3);
});

test('R04 draft protection and whole-call CSV round trip pass the installed CLI', async ({page}, testInfo) => {
  await page.goto('/main/report.html');
  await search(page).fill('missing');
  await page.getByRole('textbox', {name: '复核说明', exact: true}).fill('@按整通确认异常');
  await search(page).fill('normal');
  await expect(page.getByRole('textbox', {name: '复核说明', exact: true})).toHaveValue('@按整通确认异常');
  await expect(page.locator('#message')).toContainText('当前表单尚未记下');
  await search(page).fill('missing');
  await page.getByRole('combobox', {name: '人工判断', exact: true}).selectOption('abnormal');
  await page.getByRole('textbox', {name: '复核人', exact: true}).fill('browser-acceptance');
  await page.getByRole('button', {name: '记下整通结论', exact: true}).click();
  await expect(queue(page)).toHaveCount(0);
  const pending = page.waitForEvent('download');
  await page.getByRole('button', {name: '导出整通复核 CSV', exact: true}).click();
  const download = await pending;
  const csv = testInfo.outputPath('labels.csv');
  await download.saveAs(csv);
  const result = JSON.parse(execFileSync(process.env.VT_REVIEW_PYTHON, ['-m', 'voice_tools', '--json',
    'qa', 'assess-check', csv, '--results', path.join(process.env.VT_REVIEW_OUTPUT, 'site/main/assessment.jsonl')],
    {encoding: 'utf8', env: process.env}));
  expect(result.summary).toMatchObject({recordings: 8, human_labels: 1});
  await page.reload();
  await page.getByLabel('导入整通复核 CSV', {exact: true}).setInputFiles(csv);
  await expect(page.locator('#message')).toContainText('已导入 1 通人工标签');
  await expect(queue(page)).toHaveCount(5);
  await page.getByRole('combobox', {name: '查看范围', exact: true}).selectOption('all');
  await search(page).fill('missing');
  await expect(page.getByRole('textbox', {name: '复核说明', exact: true})).toHaveValue('@按整通确认异常');
  const invalid = testInfo.outputPath('invalid-labels.csv');
  fs.writeFileSync(invalid, fs.readFileSync(csv, 'utf8').replace(/[a-f0-9]{64}/, '0'.repeat(64)));
  await page.getByLabel('导入整通复核 CSV', {exact: true}).setInputFiles(invalid);
  await expect(page.locator('#message')).toContainText('复核身份未知或重复');
  await expect(page.getByRole('textbox', {name: '复核说明', exact: true})).toHaveValue('@按整通确认异常');
});

test('R05 conflicting evidence, preexisting output and unknown roles cannot appear as passed', async ({page}) => {
  await page.goto('/main/report.html');
  for (const [name, reason] of [['model_conflict', '缺少足够的工程声学证据'], ['old_output', '已有 AI 输出跨越']]) {
    await search(page).fill(name);
    await expect(queue(page)).toHaveCount(1);
    await expect(page.locator('#decision')).toHaveText('待人工复核');
    await expect(page.locator('#evidence')).toContainText(reason);
  }
  await search(page).fill('unknown_roles');
  await expect(page.locator('#leftTitle')).toContainText('角色未核实');
  await expect(page.locator('#decision')).toHaveText('待人工复核');
  await search(page).fill('');
  for (const name of ['missing', 'late', 'output_gap', 'missing']) {
    await page.getByRole('button', {name: new RegExp(`^call.wav ${name} ·`)}).click();
  }
  await expectDrawing(page);
  await expect(page.locator('#evidence')).toContainText('未检出 AI 语音');
});

test('R06 absent audio, old results, mono, broken input and empty windows degrade visibly', async ({page}) => {
  await page.goto('/no-audio/report.html');
  await expectDrawing(page);
  await expect(page.getByRole('button', {name: '播放试听', exact: true})).toBeDisabled();
  await page.goto('/main/legacy.html');
  await expect(page.getByText('此录音未附带波形数据。', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: '播放试听', exact: true})).toBeEnabled();
  await page.goto('/unusual/report.html');
  await page.getByRole('button', {name: /^mono.wav/}).click();
  await expectDrawing(page, 1);
  await expect(page.locator('#leftTitle')).toContainText('无法区分双方');
  await page.getByRole('button', {name: /^broken.wav/}).click();
  await expect(page.getByRole('button', {name: '播放试听', exact: true})).toBeDisabled();
  await page.goto('/main/zero-range.html');
  await expectDrawing(page);
  expect(Number(await end(page).inputValue())).toBeGreaterThan(1);
  await page.goto('/main/legacy-roles.html');
  await expectDrawing(page);
  for (const id of ['leftTitle', 'rightTitle']) {
    await expect(page.locator(`#${id}`)).toContainText('角色未核实');
    await expect(page.locator(`#${id}`)).not.toContainText('已核实');
  }
  for (const field of ['review_windows', 'checked_range']) {
    await page.goto(`/main/rounded-${field}.html`);
    await expectDrawing(page);
    await expect.poll(() => player(page).evaluate(audio => Number.isFinite(audio.duration))).toBe(true);
    const duration = await player(page).evaluate(audio => audio.duration);
    expect(Number(await end(page).inputValue())).toBe(duration);
    await page.getByRole('button', {name: '播放试听', exact: true}).click();
    await expectPlaying(page);
    await pause(page);
    if (field === 'review_windows') {
      await page.getByRole('button', {name: /^定位 1.00–/}).click();
      await expectPlaying(page);
      await pause(page);
    }
    await expect(page.locator('#message')).toHaveText('');
  }
});

test('R07 task package review opens its waveform and verified relocated audio', async ({page}, testInfo) => {
  await page.goto('/task-review/index.html');
  await page.locator('button[data-open="0"]').click();
  await page.getByRole('button', {name: '人工标签', exact: true}).click();
  const frame = page.frameLocator('iframe[title="整通自动质检与人工例外"]');
  await expectDrawing(frame);
  await expect(queue(frame)).toHaveCount(1);
  await frame.getByRole('button', {name: '播放试听', exact: true}).click();
  await expectPlaying(frame);
  expect(await player(frame).evaluate(audio => audio.currentSrc.includes('/evidence/'))).toBe(true);
  await pause(frame);
  await page.screenshot({path: testInfo.outputPath('task-review.png'), fullPage: true});
});

test('R08 original opportunity and gap reviews keep their shared waveform controls', async ({page}) => {
  for (const [url, focus] of [['/opportunity/review.html', '当前机会'], ['/gaps/review.html', '当前间隙']]) {
    await page.goto(url);
    await expectDrawing(page);
    await expect(page.getByRole('button', {name: focus, exact: true})).toBeVisible();
    await page.getByRole('button', {name: '放大波形', exact: true}).click();
    await expect(page.locator('#zoomValue')).toHaveText('×2');
    await adjustVolumeAndMute(page);
    await page.getByRole('button', {name: '播放试听', exact: true}).click();
    await expectPlaying(page);
    await pause(page);
  }
});
