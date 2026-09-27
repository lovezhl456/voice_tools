const tools = JSON.parse(document.querySelector('#workbench-tabs').textContent);
const tablist = document.querySelector('#tabs');
const panels = document.querySelector('#panels');
const views = new Map();
let activeId;
const storageKey = `voice-workbench-tab:${location.pathname}`;
let savedTab;
try { savedTab = localStorage.getItem(storageKey); } catch {}

for (const tool of tools) {
  const tab = document.createElement('button');
  tab.type = 'button';
  tab.id = `tab-${tool.id}`;
  tab.textContent = tool.label;
  tab.setAttribute('role', 'tab');
  tab.setAttribute('aria-controls', `panel-${tool.id}`);
  const panel = document.createElement('section');
  panel.id = `panel-${tool.id}`;
  panel.className = 'panel';
  panel.setAttribute('role', 'tabpanel');
  panel.setAttribute('aria-labelledby', tab.id);
  panel.hidden = true;
  const status = document.createElement('div');
  status.className = 'status';
  status.setAttribute('role', 'status');
  const message = document.createElement('p');
  const retry = document.createElement('button');
  retry.textContent = '重新加载';
  retry.type = 'button';
  const direct = document.createElement('a');
  direct.href = tool.url;
  direct.target = '_blank';
  direct.rel = 'noopener';
  direct.textContent = '单独打开页面 ↗';
  status.append(message, retry, direct);
  panel.append(status);
  panels.append(panel);
  tablist.append(tab);
  const view = {tool, tab, panel, status, message, retry, frame:null, loading:false, timer:null};
  views.set(tool.id, view);
  tab.addEventListener('click', () => selectTab(tool.id, true));
  retry.addEventListener('click', () => loadPage(view));
}

async function loadPage(view) {
  if (view.loading) return;
  clearTimeout(view.timer);
  view.frame?.remove();
  view.frame = null;
  view.loading = true;
  view.status.hidden = false;
  view.message.textContent = '正在加载页面…';
  view.retry.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    // file:// pages cannot fetch sibling files; iframe navigation still works offline.
    if (location.protocol !== 'file:') {
      const response = await fetch(view.tool.url, {method:'HEAD', signal:controller.signal, cache:'no-cache'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    }
    const frame = document.createElement('iframe');
    frame.title = view.tool.label;
    frame.src = view.tool.url;
    // Keep loaded frames mounted so forms, subpages and scroll positions survive tab changes.
    frame.addEventListener('load', () => {
      if (view.frame !== frame) return;
      view.status.hidden = true;
      view.loading = false;
      clearTimeout(view.timer);
    }, {once:true});
    view.timer = setTimeout(() => {
      view.message.textContent = '页面加载较慢，可继续等待或重新加载。';
      view.retry.hidden = false;
      view.loading = false;
    }, 15000);
    view.frame = frame;
    view.panel.prepend(frame);
  } catch (error) {
    view.message.textContent = error.name === 'AbortError' ? '页面连接超时，请重试。' : '这个页面暂时无法打开，请重试。';
    view.retry.hidden = false;
    view.loading = false;
  } finally {
    clearTimeout(timeout);
  }
}

function selectTab(id, updateHistory = false) {
  const selected = views.get(id) || views.get(tools[0].id);
  // Stop audio when leaving a page, while retaining the playback position and form state.
  if (activeId && activeId !== selected.tool.id) {
    try { views.get(activeId).frame?.contentDocument.querySelectorAll('audio,video').forEach(media => media.pause()); } catch {}
  }
  activeId = selected.tool.id;
  for (const view of views.values()) {
    const active = view === selected;
    view.tab.setAttribute('aria-selected', String(active));
    view.tab.tabIndex = active ? 0 : -1;
    view.panel.hidden = !active;
  }
  document.querySelector('#description').textContent = selected.tool.description;
  document.querySelector('#open-page').href = selected.tool.url;
  document.title = `${selected.tool.label} · voice_tools 工作台`;
  selected.tab.scrollIntoView({block:'nearest', inline:'nearest'});
  if (updateHistory && location.hash !== `#${activeId}`) history.pushState(null, '', `#${activeId}`);
  try { localStorage.setItem(storageKey, activeId); } catch {}
  if (!selected.frame && !selected.loading) loadPage(selected);
}

tablist.addEventListener('keydown', event => {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const index = tools.findIndex(tool => tool.id === activeId);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tools.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tools.length) % tools.length;
  selectTab(tools[next].id, true);
  views.get(tools[next].id).tab.focus();
});
window.addEventListener('hashchange', () => selectTab(location.hash.slice(1)));
selectTab(location.hash.slice(1) || savedTab);
