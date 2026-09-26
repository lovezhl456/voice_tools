'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

function fixture(records, recordId = (record) => record.sample_id) {
  class Option {
    constructor(text, value) { this.text = text; this.value = value; }
  }
  class Select {
    constructor() { this.options = []; this.value = ''; }
    replaceChildren(...options) { this.options = options; this.value = options[0]?.value || ''; }
    add(option) { this.options.push(option); }
  }
  const directoryFilter = new Select();
  const fileFilter = new Select();
  const fileSearch = { value: '' };
  let searchChanges = 0;
  const context = vm.createContext({
    Option, document: { getElementById: (id) => ({ directoryFilter, fileFilter, fileSearch })[id] }
  });
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  const filter = context.createReviewFileFilter(records, recordId, () => { searchChanges++; });
  return {
    directoryFilter, fileFilter, fileSearch, filter,
    get searchChanges() { return searchChanges; },
    search(value) { fileSearch.value = value; fileSearch.oninput({ isComposing: false }); },
    chooseDirectory(value) { directoryFilter.value = value; filter.updateFiles(); },
    visible() { return records.filter((record) => record.result && filter.matches(record)); }
  };
}

function record(input, id) { return { input, sample_id: id, result: { fingerprint: id } }; }
const optionValues = (select) => select.options.map((option) => option.value);

test('a directory restricts all recordings and excludes nested and sibling directories', () => {
  const records = [record('a/one.wav', '1'), record('a/two.wav', '2'),
    record('a/nested/one.wav', '3'), record('ab/one.wav', '4')];
  const ui = fixture(records);
  ui.chooseDirectory('a');
  assert.deepEqual(optionValues(ui.fileFilter), ['', '1', '2']);
  assert.deepEqual(ui.visible(), records.slice(0, 2));
  ui.chooseDirectory('');
  assert.deepEqual(ui.visible(), records);
});

test('changing directory resets an unavailable selection and retains an available one', () => {
  const records = [record('a/one.wav', '1'), record('b/two.wav', '2')];
  const ui = fixture(records);
  ui.fileFilter.value = '1';
  ui.chooseDirectory('a');
  assert.equal(ui.fileFilter.value, '1');
  ui.chooseDirectory('b');
  assert.equal(ui.fileFilter.value, '');
  assert.deepEqual(ui.visible(), [records[1]]);
  ui.fileFilter.value = '2';
  ui.chooseDirectory('');
  assert.equal(ui.fileFilter.value, '2');
});

test('identical filenames and detection identities cannot leak across directories', () => {
  const records = [record('a/call.wav', 'same'), record('b/call.wav', 'same')];
  for (const recordId of [(r) => r.sample_id, (r) => r.result.fingerprint]) {
    const ui = fixture(records, recordId);
    ui.chooseDirectory('b');
    ui.fileFilter.value = 'same';
    assert.deepEqual(ui.visible(), [records[1]]);
  }
});

test('paths support bare files, POSIX roots, Windows drive roots and UNC directories', () => {
  const records = [record('bare.wav', '0'), record('./local.wav', '1'), record('/root.wav', '2'),
    record('C:\\root.wav', '3'), record('C:\\calls\\one.wav', '4'), record('C:/calls/two.wav', '5'),
    record('\\\\server\\share\\call.wav', '6')];
  const ui = fixture(records);
  assert.deepEqual(optionValues(ui.directoryFilter), ['', '.', '/', 'C:/', 'C:/calls', '//server/share']);
  ui.chooseDirectory('.');
  assert.deepEqual(ui.visible(), records.slice(0, 2));
  ui.chooseDirectory('C:/calls');
  assert.deepEqual(optionValues(ui.fileFilter), ['', '4', '5']);
  assert.deepEqual(ui.fileFilter.options.map((option) => option.text), ['全部录音', 'one.wav', 'two.wav']);
});

test('failed records do not create options and an empty batch stays selectable', () => {
  const ui = fixture([{ input: 'broken/call.wav', error: 'invalid WAV' }]);
  assert.deepEqual(optionValues(ui.directoryFilter), ['']);
  assert.deepEqual(optionValues(ui.fileFilter), ['']);
  assert.deepEqual(ui.visible(), []);
  ui.chooseDirectory('');
  assert.equal(ui.fileFilter.value, '');
});

test('search matches filename fragments, Chinese paths and case-insensitive text', () => {
  const records = [record('客服/Call_13800138000.WAV', '1'), record('销售/order.wav', '2')];
  const ui = fixture(records);
  for (const query of ['1380013', 'cAlL_', '客服']) {
    ui.search(query);
    assert.deepEqual(ui.visible(), [records[0]]);
    assert.deepEqual(optionValues(ui.fileFilter), ['', '1']);
  }
  ui.search('销售');
  assert.deepEqual(ui.visible(), [records[1]]);
});

test('space-separated keywords all match and Windows search separators are normalized', () => {
  const records = [record('C:\\通话\\Call_001.wav', '1'), record('C:\\通话\\Call_002.wav', '2')];
  const ui = fixture(records);
  ui.search('  CALL\t001  c:\\通话  ');
  assert.deepEqual(ui.visible(), [records[0]]);
  ui.search('通话 missing');
  assert.deepEqual(ui.visible(), []);
});

test('search intersects directories without narrowing the directory choices', () => {
  const records = [record('a/one.wav', '1'), record('a/two.wav', '2'), record('b/one.wav', '3')];
  const ui = fixture(records);
  ui.search('one');
  assert.deepEqual(ui.visible(), [records[0], records[2]]);
  assert.deepEqual(optionValues(ui.directoryFilter), ['', 'a', 'b']);
  ui.chooseDirectory('a');
  assert.deepEqual(ui.visible(), [records[0]]);
  ui.search('   ');
  assert.deepEqual(ui.visible(), records.slice(0, 2));
  ui.chooseDirectory('');
  assert.deepEqual(ui.visible(), records);
});

test('search clears unavailable recording selection, preserves a valid one and handles no matches', () => {
  const records = [record('a/one.wav', '1'), record('a/two.wav', '2')];
  const ui = fixture(records);
  ui.fileFilter.value = '1';
  ui.search('one');
  assert.equal(ui.fileFilter.value, '1');
  ui.search('two');
  assert.equal(ui.fileFilter.value, '');
  assert.deepEqual(ui.visible(), [records[1]]);
  ui.search('absent');
  assert.deepEqual(optionValues(ui.fileFilter), ['']);
  assert.deepEqual(ui.visible(), []);
  ui.search('');
  assert.deepEqual(ui.visible(), records);
});

test('matching identity cannot bypass a different path search', () => {
  const records = [record('a/call.wav', 'same'), record('b/call.wav', 'same')];
  for (const recordId of [(r) => r.sample_id, (r) => r.result.fingerprint]) {
    const ui = fixture(records, recordId);
    ui.fileFilter.value = 'same';
    ui.search('b/');
    assert.deepEqual(ui.visible(), [records[1]]);
  }
});

test('search treats regex and HTML characters literally', () => {
  const records = [record('a/call[1]+.wav', '1'), record('b/<img>.wav', '2')];
  const ui = fixture(records);
  ui.search('[1]+');
  assert.deepEqual(ui.visible(), [records[0]]);
  ui.search('<img>');
  assert.deepEqual(ui.visible(), [records[1]]);
  ui.search('.*');
  assert.deepEqual(ui.visible(), []);
});

test('Chinese composition waits until committed and clearing input refreshes results', () => {
  const records = [record('客服/call.wav', '1'), record('销售/call.wav', '2')];
  const ui = fixture(records);
  ui.fileSearch.value = '客服';
  ui.fileSearch.oninput({ isComposing: true });
  assert.deepEqual(ui.visible(), records);
  assert.equal(ui.searchChanges, 0);
  ui.fileSearch.oncompositionend({ isComposing: false });
  assert.deepEqual(ui.visible(), [records[0]]);
  assert.equal(ui.searchChanges, 1);
  ui.search('');
  assert.deepEqual(ui.visible(), records);
  assert.equal(ui.searchChanges, 2);
});
