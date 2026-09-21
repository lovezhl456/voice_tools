'use strict';

function createReviewFileFilter(records, recordId) {
  const directorySelect = document.getElementById('directoryFilter');
  const fileSelect = document.getElementById('fileFilter');
  const files = records.filter((record) => record.result).map((record) => {
    const path = record.input.replace(/\\/g, '/');
    const separator = path.lastIndexOf('/');
    let directory = separator < 0 ? '.' : path.slice(0, separator) || '/';
    if (/^[A-Za-z]:$/.test(directory)) directory += '/';
    return { record, directory, name: path.slice(separator + 1), id: recordId(record) };
  });
  const directories = new Map(files.map((file) => [file.record, file.directory]));

  directorySelect.replaceChildren(new Option('全部目录', ''));
  for (const directory of new Set(files.map((file) => file.directory))) {
    directorySelect.add(new Option(directory === '.' ? '当前目录（.）' : directory, directory));
  }

  function updateFiles() {
    const selected = fileSelect.value;
    const visible = files.filter((file) => !directorySelect.value || file.directory === directorySelect.value);
    fileSelect.replaceChildren(new Option('全部录音', ''));
    for (const file of visible) fileSelect.add(new Option(file.name, file.id));
    if (visible.some((file) => file.id === selected)) fileSelect.value = selected;
  }

  function matches(record) {
    // The directory must constrain the queue even when all recordings are selected.
    return (!directorySelect.value || directories.get(record) === directorySelect.value) &&
      (!fileSelect.value || recordId(record) === fileSelect.value);
  }

  updateFiles();
  return { updateFiles, matches };
}
