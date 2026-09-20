// Dependency-free sink tests. Real DOM/event execution is checked separately
// in the browser with checksum-valid result bundles; these are not a DOM shim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(process.argv[2], 'utf8');
const payload = '<img src="missing.png" onerror="document.body.dataset.latencyXss=1">';

function summary() {
  return {
    execution_status: 'completed', measurement_status: 'measured',
    coverage: {paired: 1, eligible_human_segments: 2, ratio: .5, dispositions: {paired: 1}},
    statistics: {
      human_to_ai: {count: 1, median_s: .5, p95_s: .5},
      ai_to_human: {count: 0, median_s: null, p95_s: null},
    },
  };
}

function assign(data, path, value) {
  const keys = path.split('.');
  const leaf = keys.pop();
  const parent = keys.reduce((item, key) => item[key], data);
  parent[leaf] = value;
}

async function render(data, location) {
  // Capture every HTML assignment, including transient writes before an error.
  const writes = [];
  function sink() {
    let html = '';
    return {
      textContent: '',
      set innerHTML(value) { html = value; writes.push(value); },
      get innerHTML() { return html; },
    };
  }
  const root = sink(), detail = sink(), turns = sink();
  root.classList = {add() {}};
  root.isConnected = true;
  root.querySelector = selector => ({'.lat-recording': detail, '.lat-turns': turns}[selector] || null);
  const report = {
    kind: 'latency_recording', schema_version: '1.0',
    execution_status: data.execution_status, system_channel: 'right',
    measurement: {...data, status: data.measurement_status, pairs: [], segments: [], reasons: []},
  };
  const context = vm.createContext({
    window: {}, URL, document: {baseURI: 'http://localhost/report/'},
    location: {origin: 'http://localhost'},
    fetch: async () => ({ok: true, json: async () => report}),
  });
  vm.runInContext(source, context);
  context.window.VoiceLatency.mount(root, {
    summary: location === 'summary' ? data : summary(),
    rows: location === 'detail' ? [{input: 'example.wav', detail: 'recording.json'}] : [],
  });
  await new Promise(resolve => setImmediate(resolve));
  return {root, detail, turns, writes};
}

function assertRejected(result, location, field) {
  const error = location === 'summary' ? result.root.textContent : result.detail.innerHTML;
  assert.ok(error.includes('latency 结果数据无效'), error);
  assert.ok(error.includes(field), error);
  for (const html of result.writes) {
    assert.ok(!html.includes('<img'), html);
    assert.ok(!html.includes('onerror='), html);
  }
  assert.equal(result.turns.innerHTML, '');
}

const countFields = [
  'coverage.paired', 'coverage.eligible_human_segments',
  'statistics.human_to_ai.count', 'statistics.ai_to_human.count',
  'coverage.dispositions.paired',
];
for (const location of ['summary', 'detail']) {
  for (const field of countFields) {
    test(`${location}: reject malformed ${field} before writing HTML`, async () => {
      for (const value of [payload, '1', '', true, false, {}, [], [1], -1, .5,
        Number.MAX_SAFE_INTEGER + 1, NaN, Infinity, null, undefined]) {
        const data = summary();
        assign(data, field, value);
        assertRejected(await render(data, location), location,
          field.startsWith('coverage.dispositions') ? 'coverage.dispositions' : field);
      }
    });
  }
  test(`${location}: ratio rejects coercion and nonfinite/out-of-range values`, async () => {
    for (const value of [payload, '0.5', true, false, {}, [], -.1, 1.1, NaN, Infinity, undefined]) {
      const data = summary();
      data.coverage.ratio = value;
      assertRejected(await render(data, location), location, 'coverage.ratio');
    }
  });
  test(`${location}: valid counts and zero/null evidence remain readable`, async () => {
    const normal = await render(summary(), location);
    const html = location === 'summary' ? normal.root.innerHTML : normal.detail.innerHTML;
    assert.ok(html.includes('50.0%'));
    assert.ok(html.includes('1 / 2 个合格人声片段'));
    assert.ok(html.includes('1 轮 · P95 0.500 s'));
    const data = summary();
    data.measurement_status = 'insufficient_evidence';
    data.coverage = {paired: 0, eligible_human_segments: 0, ratio: null, dispositions: {}};
    data.statistics.human_to_ai = {count: 0, median_s: null, p95_s: null};
    const empty = await render(data, location);
    const emptyHTML = location === 'summary' ? empty.root.innerHTML : empty.detail.innerHTML;
    assert.ok(emptyHTML.includes('证据不足'));
    assert.ok(emptyHTML.includes('0 / 0 个合格人声片段'));
    assert.ok(emptyHTML.includes('0 轮 · P95 —'));
    assert.ok(!emptyHTML.includes('0.000 s'));
  });
  test(`${location}: free text stays escaped and never creates a tag`, async () => {
    const data = summary();
    data.execution_status = payload;
    data.measurement_status = payload;
    data.coverage.dispositions = {[payload]: 1};
    const result = await render(data, location);
    const html = location === 'summary' ? result.root.innerHTML : result.detail.innerHTML;
    assert.ok(html.includes('&lt;img'));
    assert.ok(html.includes('&quot;'));
    assert.ok(!result.writes.some(value => value.includes('<img')));
  });
}
