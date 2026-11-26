"use strict";

const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="voice-tools-session"]').content;
const player = $("player");
const modal = $("dialog");
const lightMarks = ["#994a2b", "#2c6c73", "#714c9e", "#457334", "#9b3d69"];
const darkMarks = ["#e29c7b", "#91c9ce", "#c6a8ec", "#b5d79d", "#e4a0bd"];
const darkMode = matchMedia("(prefers-color-scheme: dark)");
const ruleColor = index => (darkMode.matches ? darkMarks : lightMarks)[index % lightMarks.length];
const statuses = {
  pending: "待核", confirmed: "已确认", false_positive: "误报", corrected: "已修正",
  ok: "完成", no_windows: "无法判断", partial_evidence: "部分无法判断", error: "失败",
};
const checkedFiles = new Set();
const enabled = new Set();
try {
  JSON.parse(localStorage.getItem("voice-tools.workbench.rules") || "[]").forEach(ref => enabled.add(ref));
} catch { /* A malformed preference can be reset in the rule selector. */ }

let data = {files: [], rules: [], jobs: []}, active = null, recording = null;
let hit = null, view = [0, 0], listenEnd = null, requestVersion = 0, loadingState = false, poll;
let messages = [], draft = null, dialogGeneration = 0;

function node(type, content, options = {}) {
  const el = document.createElement(type);
  if (content != null) el.textContent = content;
  Object.assign(el, options);
  return el;
}

function button(label, callback, primary = false) {
  return node("button", label, {type: "button", onclick: callback, className: primary ? "primary" : ""});
}

function label(title, field) {
  const el = node("label", title);
  el.append(field);
  return el;
}

function checkbox(title) {
  const input = node("input", null, {type: "checkbox"});
  input.setAttribute("aria-label", title);
  return input;
}

async function post(action, params = {}, binary = false) {
  const response = await fetch("/api/recordings/" + action, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Voice-Tools-Session": token},
    body: JSON.stringify(params),
  });
  if (response.ok && binary) return response.blob();
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || "工作台请求失败");
  return result;
}

function report(message, error = false) {
  $("notice").hidden = !message;
  $("notice").textContent = message;
  $("notice").dataset.error = error;
}

function attempt(fn, inDialog = false) {
  return async event => {
    const control = event?.currentTarget;
    try {
      if (control?.tagName === "BUTTON") control.disabled = true;
      await fn(event);
    } catch (error) {
      if (inDialog && modal.open) $("dialog-status").textContent = error.message;
      else report(error.message, true);
    } finally {
      if (control?.tagName === "BUTTON") control.disabled = false;
      updateActions();
    }
  };
}

const ruleRef = rule => rule.id + "@" + rule.version;
const hitRef = item => item.definition_id + "@" + item.version;
const byTime = (a, b) => a.start_s - b.start_s || a.end_s - b.end_s;
const minuteText = time => String(Math.floor(time / 60)).padStart(2, "0") + ":" +
  String(Math.floor(time % 60)).padStart(2, "0");
const timeText = time => minuteText(time) + "." + String(Math.floor(time * 100 + 1e-6) % 100).padStart(2, "0");
const visibleFiles = () => data.files.filter(row => row.id.toLowerCase().includes($("file-search").value.toLowerCase()));
const running = () => data.jobs.some(job => job.state === "running");
const chosenRules = () => data.rules.filter(rule => enabled.has(ruleRef(rule)));

function updateActions() {
  $("export-open").textContent = "导出所选录音（" + checkedFiles.size + "）";
  $("export-open").disabled = !checkedFiles.size;
  $("run-current").disabled = !recording || !chosenRules().length || running();
  $("run-batch").disabled = !data.files.some(file => file.status !== "missing") || !chosenRules().length || running();
  const ids = visibleFiles();
  $("select-all").checked = !!ids.length && ids.every(row => checkedFiles.has(row.id));
  $("select-all").indeterminate = !($("select-all").checked) && ids.some(row => checkedFiles.has(row.id));
}

function renderFiles() {
  $("folder-name").textContent = data.folder || "尚未关联";
  $("files").replaceChildren(...visibleFiles().map(file => {
    const item = node("div", null, {className: "file" + (active === file.id ? " active" : "")});
    const check = checkbox("导出 " + file.id);
    check.dataset.file = file.id;
    check.checked = checkedFiles.has(file.id);
    check.onchange = () => {
      if (check.checked) checkedFiles.add(file.id);
      else checkedFiles.delete(file.id);
      updateActions();
    };
    const title = button(file.id, () => openFile(file.id));
    if (active === file.id) title.setAttribute("aria-current", "true");
    const summary = file.findings ? file.findings + " 处标注" :
      {missing: "源文件不可用", not_run: "未运行", history: "有历史记录", completed: "有检测记录"}[file.status];
    item.append(check, title, node("small", summary, {className: "muted"}));
    return item;
  }));
  $("enabled").replaceChildren(...chosenRules().map(rule => node("span", rule.name + " · " + rule.version, {className: "chip"})));
  updateActions();
}

function renderJob() {
  const job = data.jobs[0];
  $("job").hidden = !job;
  if (!job) return;
  const state = {running: "标注进行中", completed: "标注完成", partial: "部分完成", interrupted: "任务中断"}[job.state];
  const errors = Object.entries(job.errors).map(([file, message]) => file + " · " + message);
  $("job").replaceChildren(node("span", state + " · " + job.done + "/" + job.total + " 通" +
    (job.error ? "\n" + job.error : "") + (errors.length ? "\n" + errors.join("\n") : "")));
  if (job.state !== "running" && job.state !== "completed" && job.root === data.folder) {
    const files = job.files.filter((file, idx) => idx >= job.done || Object.hasOwn(job.errors, file));
    if (files.length) $("job").append(" ", button("重试未完成录音", attempt(() => launch(files, job.rules))));
  }
}

async function refresh(scan = false) {
  if (loadingState) return;
  loadingState = true;
  try {
    const priorJob = data.jobs[0];
    data = await post("state", {scan});
    renderFiles();
    renderJob();
    clearTimeout(poll);
    if (running()) poll = setTimeout(() => refresh().catch(error => report(error.message, true)), 650);
    else if (active && data.jobs[0] &&
        (priorJob?.state === "running" || priorJob?.id !== data.jobs[0].id)) await openFile(active, true);
  } finally {
    loadingState = false;
  }
}

const timeline = window.createRecordingDuplexTimeline(player, time => {
  listenEnd = null;
  player.currentTime = time;
}, (start, end) => {
  view = [start, end];
  [...$("annotationTime").children].forEach((tick, index, all) => {
    const seconds = start + (end - start) * index / (all.length - 1);
    tick.textContent = end - start >= 60 ? minuteText(seconds) : timeText(seconds);
  });
  renderLanes();
});

function setMedia(url, resumeAt = 0, resume = false) {
  listenEnd = null;
  player.onloadedmetadata = () => {
    if (resumeAt) player.currentTime = Math.min(resumeAt, player.duration);
    if (resume) player.play().catch(error => report(error.message, true));
  };
  player.src = url;
}

async function openFile(file, preserve = false) {
  const request = ++requestVersion;
  const previousTime = preserve ? player.currentTime : 0;
  const previousHit = preserve ? hit?.id : null;
  const resume = preserve && !player.paused;
  if (!preserve) {
    player.pause();
    player.removeAttribute("src");
    player.load();
    $("channel").value = "both";
    recording = hit = null;
    $("recording").hidden = true;
    $("empty").hidden = false;
    $("empty").textContent = "正在读取录音…";
  }
  active = file;
  $("recording-name").textContent = file;
  $("recording-meta").textContent = "";
  renderFiles();
  report("");
  try {
    const selected = $("channel").value;
    const loaded = await post("open", {file, channel: selected});
    if (request !== requestVersion) return;
    recording = loaded;
    recording.findings.sort(byTime);
    hit = recording.findings.find(row => row.id === previousHit) || null;
    $("recording").hidden = false;
    $("empty").hidden = true;
    $("channel").options[2].disabled = loaded.channels !== 2;
    $("recording-meta").textContent = (loaded.channels === 2 ? "双声道" : "单声道") + " · " + timeText(loaded.duration) +
      (loaded.time_origin === "prepared_pcm" ? " · 使用转换后音频时间轴" : "");
    $("duplexLeft").textContent = loaded.ai_channel === 0 ? "左 · AI" : loaded.ai_channel === 1 ? "左 · 用户" : "左声道";
    $("duplexRight").textContent = loaded.ai_channel === 1 ? "右 · AI" : loaded.ai_channel === 0 ? "右 · 用户" : "右声道";
    setMedia(loaded.url, previousTime, resume);
    timeline.load(loaded.waveform, loaded.duration);
    renderEvaluations();
    renderMarks();
    renderFiles();
  } catch (error) {
    if (request !== requestVersion) return;
    $("empty").textContent = error.message;
    report(error.message, true);
  }
}

function renderEvaluations() {
  $("evaluations").hidden = !recording.evaluations.length;
  $("evaluation-list").replaceChildren(...recording.evaluations.map(row => node("li",
    row.name + " · " + row.version + " · " + (statuses[row.status] || row.status) + (row.note ? " · " + row.note : ""))));
}

function groupedFindings() {
  const groups = new Map();
  recording?.findings.forEach(item => {
    const key = hitRef(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  return groups;
}

function renderLanes() {
  if (!recording || view[1] <= view[0]) {
    $("lanes").replaceChildren();
    return;
  }
  const [start, end] = view;
  const groups = groupedFindings(), dense = recording.findings.length > 24 && recording.duration > 60;
  const overview = $("distribution");
  overview.replaceChildren();
  if (dense) {
    const bins = Math.ceil(recording.duration / 60);
    const grid = node("div", null, {className: "minute-grid"});
    grid.style.setProperty("--minutes", bins);
    grid.append(node("strong", "全程分布"));
    for (let minute = 0; minute < bins; minute++) grid.append(node("small", minuteText(minute * 60), {className: "muted"}));
    [...groups.values()].forEach((items, index) => {
      const counts = Array(bins).fill(0);
      items.forEach(item => counts[Math.min(bins - 1, Math.floor(item.start_s / 60))]++);
      const name = node("span", items[0].name, {className: "label", title: items[0].name});
      name.style.color = ruleColor(index);
      grid.append(name);
      counts.forEach((count, minute) => {
        const left = minute * 60;
        const right = Math.min(recording.duration, left + 60);
        const show = button(String(count), () => {
          player.currentTime = left;
          timeline.showWindow(left, right);
          renderMarks();
        }, end - start <= 60.001 && start >= left && start < right);
        show.setAttribute("aria-label", items[0].name + " " + minuteText(left) + "，" + count + " 处");
        grid.append(show);
      });
    });
    overview.append(grid);
  }

  const lanes = [...groups.values()].map((items, index) => {
    const lane = node("div", null, {className: "lane"});
    lane.style.setProperty("--mark", ruleColor(index));
    const name = node("span", items[0].name + " · " + items[0].version,
      {className: "lane-name", title: hitRef(items[0])});
    const plot = node("div", null, {className: "lane-plot"});
    if (dense && end - start > 60.001) {
      plot.append(node("small", "选择上方一分钟查看 " + items.length + " 处标记", {className: "muted"}));
    } else {
      items.filter(item => item.end_s > start && item.start_s < end).forEach(item => {
        const mark = button("", () => selectHit(item, false));
        mark.title = item.label + " · " + timeText(item.start_s) + "–" + timeText(item.end_s);
        mark.setAttribute("aria-label", mark.title);
        mark.setAttribute("aria-pressed", hit?.id === item.id);
        mark.style.left = 100 * (Math.max(start, item.start_s) - start) / (end - start) + "%";
        mark.style.width = 100 * (Math.min(end, item.end_s) - Math.max(start, item.start_s)) / (end - start) + "%";
        plot.append(mark);
      });
    }
    lane.append(name, plot);
    return lane;
  });
  $("lanes").replaceChildren(...lanes);
}

function renderMarks() {
  if (!recording) return;
  const local = recording.duration > 60 && recording.findings.length > 24 && view[1] - view[0] <= 60.001;
  const rows = recording.findings.filter(item => (!local || item.end_s > view[0] && item.start_s < view[1]) &&
    (!$("pending-only").checked || item.status === "pending"));
  $("mark-count").textContent = "· " + rows.length + " / " + recording.findings.length + " 处";
  if (!rows.length) {
    const cell = node("td", "暂无标注", {colSpan: 4, className: "muted"});
    const row = node("tr");
    row.append(cell);
    $("marks").replaceChildren(row);
  } else {
    $("marks").replaceChildren(...rows.map(item => {
      const row = node("tr", null, {className: hit?.id === item.id ? "active" : ""});
      const check = checkbox("确认 " + item.label + " " + timeText(item.start_s));
      check.checked = ["confirmed", "corrected"].includes(item.status);
      check.onchange = attempt(async () => {
        try {
          await saveReview(item, check.checked ? "confirmed" : "pending", check.checked ? {
            label: item.label, start_s: item.start_s, end_s: item.end_s,
          } : {});
        } catch (error) {
          check.checked = !check.checked;
          throw error;
        }
      });
      const state = node("td", null, {className: check.checked ? "checked" : ""});
      state.append(check, " " + statuses[item.status]);
      const actions = node("td");
      actions.append(button("试听前后", () => selectHit(item, true)), button("复核详情", () => selectHit(item, false)));
      row.append(node("td", item.label + " · " + item.version),
        node("td", timeText(item.start_s) + "–" + timeText(item.end_s) + " / " +
          (item.end_s - item.start_s).toFixed(2) + " 秒"), state, actions);
      return row;
    }));
  }
  renderLanes();
  renderReview();
}

function selectHit(item, play) {
  hit = item;
  listenEnd = play ? Math.min(recording.duration, item.end_s + 1) : null;
  const start = play ? Math.max(0, item.start_s - 1) : item.start_s;
  if (recording.findings.length > 24 && recording.duration > 60 && (view[1] - view[0] > 60 || start < view[0] || start > view[1])) {
    const minute = Math.floor(start / 60) * 60;
    timeline.showWindow(minute, Math.min(recording.duration, minute + 60));
  }
  player.currentTime = start;
  timeline.setEvidence(item.start_s, item.end_s, true);
  renderMarks();
  $("marks").querySelector("tr.active")?.scrollIntoView({block: "nearest"});
  if (play) player.play().catch(error => report(error.message, true));
}

function renderReview() {
  const pane = $("review-pane");
  pane.hidden = !hit;
  if (!hit) return;
  const item = hit;
  pane.replaceChildren(node("strong", item.label),
    node("span", (item.reason || "") + (item.comment ? " · " + item.comment : ""), {className: "muted"}),
    button("备注或修正", () => reviewDialog(item)),
    button("记为误报", () => reviewDialog(item, true)),
    button("撤回复核", attempt(() => saveReview(item, "pending"))));
}

async function saveReview(item, status, updated = {}) {
  const loaded = recording;
  const original = item.automatic;
  const defaults = status === "corrected" ? item : original;
  const payload = {finding_id: item.id, expected_revision: item.revision, status,
    label: defaults.label, start_s: defaults.start_s, end_s: defaults.end_s, comment: item.comment, ...updated};
  if (status === "confirmed" && (payload.start_s !== original.start_s || payload.end_s !== original.end_s || payload.label !== original.label)) {
    payload.status = "corrected";
  }
  const result = await post("review", payload);
  if (recording !== loaded) return;
  recording.findings = recording.findings.map(row => row.id === item.id ? result.finding : row).sort(byTime);
  hit = result.finding;
  timeline.setEvidence(hit.start_s, hit.end_s, true);
  renderMarks();
  report("复核已保存到本机。");
}

function dialog(title, ...content) {
  dialogGeneration++;
  $("dialog-title").textContent = title;
  $("dialog-status").textContent = "";
  $("dialog-body").replaceChildren(...content);
  if (!modal.open) modal.showModal();
}

function textField(value, options = {}) {
  return node("input", null, {type: "text", value, ...options});
}

function noteField(value) {
  return node("textarea", value, {maxLength: 4000});
}

function reviewDialog(item, falsePositive = false) {
  const start = textField(item.start_s, {type: "number", min: 0, step: "any"});
  const end = textField(item.end_s, {type: "number", min: 0, step: "any"});
  const note = noteField(item.comment);
  const save = button("保存复核", attempt(async () => {
    const {start_s, end_s} = item.automatic;
    const changed = start.valueAsNumber !== start_s || end.valueAsNumber !== end_s || item.label !== item.automatic.label;
    await saveReview(item, falsePositive ? "false_positive" : changed ? "corrected" :
      item.status === "false_positive" ? "false_positive" : item.status, {
        start_s: falsePositive ? start_s : start.valueAsNumber,
        end_s: falsePositive ? end_s : end.valueAsNumber,
        label: falsePositive ? item.automatic.label : item.label, comment: note.value,
      });
    modal.close();
  }, true), true);
  const bounds = falsePositive ? [] : [label("起点（秒）", start), label("终点（秒）", end)];
  dialog(falsePositive ? "记为误报" : "备注或修正", ...bounds, label("复核备注", note), save);
}

function manualDialog() {
  const start = textField(player.currentTime.toFixed(2), {type: "number", min: 0, step: "any"});
  const end = textField(Math.min(recording.duration, player.currentTime + 1).toFixed(2), {type: "number", min: 0, step: "any"});
  const name = textField("", {maxLength: 100});
  const note = noteField("");
  dialog("人工补标", label("标签", name), label("起点（秒）", start), label("终点（秒）", end), label("备注", note),
    button("保存补标", attempt(async () => {
      if (!name.value.trim()) throw new Error("请填写标签");
      const result = await post("manual", {file: active, label: name.value.trim(),
        start_s: start.valueAsNumber, end_s: end.valueAsNumber, comment: note.value});
      recording.findings.push(result.finding);
      recording.findings.sort(byTime);
      modal.close();
      selectHit(result.finding, false);
      await refresh();
    }, true), true));
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = node("a", null, {href: url, download: name});
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function setEnabled() {
  localStorage.setItem("voice-tools.workbench.rules", JSON.stringify([...enabled]));
  renderFiles();
}

function rulePicker() {
  const options = node("div", null, {className: "rule-options"});
  options.replaceChildren(...data.rules.map(rule => {
    const ref = ruleRef(rule);
    const check = checkbox("本次启用 " + rule.name + " " + rule.version);
    check.checked = enabled.has(ref);
    check.onchange = () => {
      if (check.checked) {
        [...enabled].filter(value => value.startsWith(rule.id + "@")).forEach(value => enabled.delete(value));
        enabled.add(ref);
      } else enabled.delete(ref);
      setEnabled();
      rulePicker();
    };
    const row = node("div");
    row.append(label(rule.name + " · " + ref, check), button("查看规则", () => previewRule(rule, false)));
    return row;
  }));
  if (!data.rules.length) options.append(node("p", "规则库为空，可与 AI 定规则或导入 JSON。"));
  dialog("全局规则库", options, button("与 AI 定规则", aiDialog), button("导入规则", importDialog));
}

const types = {quiet: "连续静音", output_pause: "AI 输出停顿", response_wait: "应答等待", overlap: "声道重叠"};
function explain(rule) {
  if (rule.schema_version === "2.0") return [
    types[rule.kind], "声道 " + {left: "左", right: "右", both: "双轨"}[rule.channel],
    "持续时间 " + (rule.op === "gt" ? "超过" : "至少") + " " + rule.min_s + " 秒",
    "活动阈值 " + rule.threshold_dbfs + " dBFS", "标注记录实际声学区间", rule.description,
  ].join("\n");
  const window = rule.window || {};
  return [
    {whole: "标注整段范围", sliding: "标注命中的滑动窗口", after_activity: "标注活动之后的检查窗口"}[window.kind],
    window.length_s == null ? "" : "窗口长度 " + window.length_s + " 秒",
    ...Object.entries(rule.metrics).map(([name, metric]) => name + " · " + metric.kind + " · 声道 " + (metric.channel + 1)),
    ...rule.rules.map(item => item.label + " · " + (item.description || JSON.stringify(item.when))),
  ].filter(Boolean).join("\n");
}

function previewRule(rule, isDraft = true) {
  const summary = node("p", explain(rule), {className: "rule-preview"});
  const version = textField(rule.version, {maxLength: 80});
  const save = button("存入全局规则库", attempt(async () => {
    const result = await post("save", {config: {...rule, version: version.value.trim()}});
    await refresh();
    const saved = result.config;
    $("dialog-status").textContent = saved.name + " · " + ruleRef(saved) + " 已保存。";
    save.disabled = true;
  }, true), true);
  const downloadRule = button("导出规则 JSON", () => download(
    new Blob([JSON.stringify(rule, null, 2)], {type: "application/json"}), rule.id + "-" + rule.version + ".json"));
  if (isDraft) {
    dialog(rule.name, summary, label("规则版本", version), save, downloadRule);
  } else {
    dialog(rule.name, summary, downloadRule, button("用 AI 讨论修改", () => {
      messages = [{role: "assistant", content: JSON.stringify(rule)}];
      draft = rule;
      aiDialog();
    }), button("返回规则库", rulePicker));
  }
}

function importDialog() {
  const input = node("input", null, {type: "file", accept: ".json,application/json"});
  input.onchange = attempt(async () => {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 900000) throw new Error("规则文件超过 900 KB");
    const configText = await file.text();
    const result = await post("validate", {config_text: configText});
    previewRule(result.config);
  }, true);
  dialog("导入规则", label("选择规则 JSON", input));
}

let apiKey = "", modelName = "";
function aiDialog() {
  const key = textField(apiKey, {type: "password", autocomplete: "off", placeholder: data.key_on_server ? "使用本机服务的 Key" : "当前页面会话使用"});
  const model = textField(modelName || data.model || "");
  key.oninput = () => { apiKey = key.value; };
  model.oninput = () => { modelName = model.value; };
  const question = node("textarea", null, {placeholder: "例如：用户说完之后，超过两秒 AI 才开始说话，请标出等候区间。", maxLength: 6000});
  const history = node("div", messages.map(m => m.role === "user" ? "你 · " + m.content : "AI · " + m.content).join("\n\n"),
    {className: "rule-preview"});
  const ask = button("生成或修改规则", attempt(async () => {
    const generation = dialogGeneration;
    const content = question.value.trim();
    if (!content) throw new Error("请描述要查什么");
    const next = [...messages.slice(-18), {role: "user", content}];
    $("dialog-status").textContent = "正在生成规则…";
    const result = await post("ai", {key: key.value, model: model.value, messages: next});
    if (!modal.open || generation !== dialogGeneration) return;
    draft = result.config;
    const reply = [result.explanation, result.question, draft ? JSON.stringify(draft) : ""].filter(Boolean).join("\n");
    messages = [...next, {role: "assistant", content: reply.slice(0, 6000)}];
    aiDialog();
  }, true), true);
  dialog("与 AI 定规则", label("GPT API Key", key), label("模型 ID", model),
    ...(messages.length ? [history] : []), label("告诉 AI 要检查什么", question), ask,
    ...(draft ? [button("预览并保存草稿", () => previewRule(draft))] : []),
    ...(messages.length ? [button("开始新讨论", () => { messages = []; draft = null; aiDialog(); })] : []));
}

async function launch(files, rules) {
  await post("run", {files, rules});
  modal.close();
  report("");
  await refresh();
}

function runDialog(batch) {
  const files = batch ? data.files.filter(row => row.status !== "missing").map(row => row.id) : [active];
  const rules = chosenRules();
  dialog(batch ? "批量标注文件夹" : "标注当前录音",
    node("p", files.length + " 通 · " + rules.map(rule => rule.name + " @" + rule.version).join("、")),
    button("开始标注", attempt(() => launch(files, rules.map(ruleRef)), true), true));
}

$("dialog-form").onsubmit = event => event.preventDefault();
$("close").onclick = () => modal.close();
modal.onclose = () => { dialogGeneration++; };
$("file-search").oninput = renderFiles;
$("select-all").onchange = event => {
  visibleFiles().forEach(file => event.target.checked ? checkedFiles.add(file.id) : checkedFiles.delete(file.id));
  renderFiles();
};
$("rescan").onclick = attempt(() => refresh(true));
$("folder-open").onclick = () => {
  const folder = textField(data.folder, {placeholder: "/绝对路径/录音文件夹", required: true});
  const channel = node("select");
  channel.append(...[["", "未知"], ["0", "左声道"], ["1", "右声道"]].map(([value, title]) =>
    node("option", title, {value})));
  channel.value = data.ai_channel == null ? "" : String(data.ai_channel);
  dialog("关联录音文件夹", label("本机文件夹绝对路径", folder), label("AI 所在声道", channel),
    button("关联并扫描", attempt(async () => {
      const previous = data.folder;
      data = await post("associate", {folder: folder.value.trim(), ai_channel: channel.value === "" ? null : Number(channel.value)});
      if (data.folder !== previous) {
        requestVersion++;
        player.pause();
        player.removeAttribute("src");
        player.load();
        active = recording = hit = null;
        checkedFiles.clear();
        $("recording").hidden = true;
        $("empty").textContent = "从左栏选择一通录音。";
        $("empty").hidden = false;
        $("recording-name").textContent = "选择一通录音";
        $("recording-meta").textContent = "";
      } else if (active) await openFile(active, true);
      renderFiles();
      modal.close();
      await refresh();
    }, true), true));
};
$("library-open").onclick = $("rules-open").onclick = rulePicker;
$("import-open").onclick = importDialog;
$("ai-open").onclick = aiDialog;
$("run-current").onclick = () => runDialog(false);
$("run-batch").onclick = () => runDialog(true);
$("pending-only").onchange = renderMarks;
$("manual-open").onclick = manualDialog;
$("next").onclick = () => {
  const pending = recording.findings.filter(item => item.status === "pending");
  const index = pending.findIndex(item => item.id === hit?.id);
  const next = pending[(index + 1) % pending.length];
  if (next) selectHit(next, true);
  else report("没有待核标注。");
};
$("channel").onchange = attempt(async () => {
  const source = active;
  const revision = ++requestVersion;
  const time = player.currentTime, playing = !player.paused;
  const result = await post("open", {file: source, channel: $("channel").value});
  if (revision === requestVersion) setMedia(result.url, time, playing);
});
player.ontimeupdate = () => {
  timeline.setPosition(player.currentTime);
  if (listenEnd != null && player.currentTime >= listenEnd) {
    player.pause();
    listenEnd = null;
  }
};
$("export-open").onclick = () => {
  const dataOnly = checkbox("仅数据");
  const confirmed = checkbox("仅已确认标注");
  const files = [...checkedFiles];
  const hidden = files.length - visibleFiles().filter(item => checkedFiles.has(item.id)).length;
  dialog("导出所选录音", node("p", "已勾选 " + files.length + " 通" + (hidden ? " · " + hidden + " 通在当前筛选外" : "")),
    label("仅数据（省略原录音）", dataOnly), label("仅已确认标注", confirmed),
    button("下载 ZIP", attempt(async () => {
      $("dialog-status").textContent = "正在核对所选录音并生成 ZIP…";
      const blob = await post("export", {files, data_only: dataOnly.checked, confirmed_only: confirmed.checked}, true);
      download(blob, "recordings-review.zip");
      modal.close();
    }, true), true));
};
darkMode.addEventListener("change", renderLanes);
for (const id of ["duplexFit", "duplexZoomIn", "duplexZoomOut"]) $(id).addEventListener("click", renderMarks);
refresh(true).catch(error => report(error.message, true));
