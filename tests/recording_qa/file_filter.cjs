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
  const context = vm.createContext({
    Option, document: { getElementById: (id) => ({ directoryFilter, fileFilter })[id] }
  });
  vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  const filter = context.createReviewFileFilter(records, recordId);
  return {
    directoryFilter, fileFilter, filter,
    chooseDirectory(value) { directoryFilter.value = value; filter.updateFiles(); },
    visible() { return records.filter((record) => record.result && filter.matches(record)); }
  };
}

function record(input, id) { return { input, sample_id: id, result: { fingerprint: id } }; }
const optionValues = (select) => select.options.map((option) => option.value);

test('all directories preserves the original recording order and identities', () => {
  const records = [record('b/call.wav', 'b'), record('a/call.wav', 'a')];
  const ui = fixture(records);
  assert.deepEqual(optionValues(ui.fileFilter), ['', 'b', 'a']);
  assert.deepEqual(ui.visible(), records);
  ui.fileFilter.value = 'a';
  assert.deepEqual(ui.visible(), [records[1]]);
});

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

test('directory and filename text is passed literally to options', () => {
  const directory = '中文 <img onerror=alert(1)> & "录音"';
  const ui = fixture([record(`${directory}/<b>call.wav`, '1')]);
  assert.equal(ui.directoryFilter.options[1].text, directory);
  ui.chooseDirectory(directory);
  assert.equal(ui.fileFilter.options[1].text, '<b>call.wav');
});
