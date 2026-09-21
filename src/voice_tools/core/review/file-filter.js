'use strict';

function createReviewFileFilter(records, recordId, onSearchChange) {
  const directorySelect = document.getElementById('directoryFilter');
  const fileSelect = document.getElementById('fileFilter');
  const searchInput = document.getElementById('fileSearch');
  let searchTerms = [];
  const files = records.filter((record) => record.result).map((record) => {
    const path = record.input.replace(/\\/g, '/');
    const separator = path.lastIndexOf('/');
    let directory = separator < 0 ? '.' : path.slice(0, separator) || '/';
    if (/^[A-Za-z]:$/.test(directory)) directory += '/';
    return { record, directory, name: path.slice(separator + 1), id: recordId(record), searchText: path.toLowerCase() };
  });
  const filesByRecord = new Map(files.map((file) => [file.record, file]));

  directorySelect.replaceChildren(new Option('全部目录', ''));
  for (const directory of new Set(files.map((file) => file.directory))) {
    directorySelect.add(new Option(directory === '.' ? '当前目录（.）' : directory, directory));
  }

  function matchesDirectoryAndSearch(file) {
    return (!directorySelect.value || file.directory === directorySelect.value) &&
      searchTerms.every((term) => file.searchText.includes(term));
  }

  function updateFiles() {
    searchTerms = searchInput.value.replace(/\\/g, '/').toLowerCase().split(/\s+/).filter(Boolean);
    const selected = fileSelect.value;
    const visible = files.filter(matchesDirectoryAndSearch);
    fileSelect.replaceChildren(new Option('全部录音', ''));
    for (const file of visible) fileSelect.add(new Option(file.name, file.id));
    if (visible.some((file) => file.id === selected)) fileSelect.value = selected;
  }

  function matches(record) {
    // Directory and search constrain the queue even when all recordings are selected.
    const file = filesByRecord.get(record);
    return Boolean(file && matchesDirectoryAndSearch(file) &&
      (!fileSelect.value || file.id === fileSelect.value));
  }

  function searchChanged(event) {
    if (event.isComposing) return;
    updateFiles();
    onSearchChange();
  }
  searchInput.oninput = searchChanged;
  searchInput.oncompositionend = searchChanged;
  updateFiles();
  return { updateFiles, matches };
}
