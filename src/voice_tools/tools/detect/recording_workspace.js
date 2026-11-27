"use strict";

const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="voice-tools-session"]').content;
const player = $("player");
const modal = $("dialog");
const darkMode = matchMedia("(prefers-color-scheme: dark)");
function ruleColor(key) {
  let hash = 0;
  for (const char of key) hash = (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0;
  return "hsl(" + hash % 360 + (darkMode.matches ? " 52% 75%)" : " 56% 34%)");
}
const hiddenLanes = new Set();
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

async function post(action, params = {}, binary = false, signal) {
  const response = await fetch("/api/recordings/" + action, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Voice-Tools-Session": token},
    body: JSON.stringify(params), signal,
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
const hitRef = item => JSON.stringify([item.definition_id, item.version, item.rule_id ?? item.label]);
const byTime = (a, b) => a.start_s - b.start_s || a.end_s - b.end_s;
const minuteText = time => String(Math.floor(time / 60)).padStart(2, "0") + ":" +
  String(Math.floor(time % 60)).padStart(2, "0");
const timeText = time => minuteText(time) + "." + String(Math.floor(time * 100 + 1e-6) % 100).padStart(2, "0");
const visibleFiles = () => data.files.filter(row => row.id.toLowerCase().includes($("file-search").value.toLowerCase()));
const running = () => data.busy || data.jobs.some(job => job.state === "running");
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
  $("folder-name").textContent = data.folder?.split("/").filter(Boolean).pop() || data.folder || "尚未关联";
  $("folder-name").title = data.folder;
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
    const parts = file.id.split("/");
    const parent = parts.at(-2);
    const display = parent?.startsWith("probe-") ? parent.slice(6, 14) + " / " + parts.at(-1) : file.id;
    const title = button(display, () => openFile(file.id));
    title.title = file.id;
    title.setAttribute("aria-label", file.id);
    if (active === file.id) title.setAttribute("aria-current", "true");
    const summary = file.findings ? file.findings + " 处标注" :
      {missing: "源文件不可用", not_run: "未运行", history: "有历史记录", completed: "有检测记录"}[file.status];
    item.append(check, title, node("small", (file.duration != null ? timeText(file.duration) + " · " : "") + summary, {className: "muted"}));
    return item;
  }));
  $("enabled").replaceChildren(...chosenRules().map(rule => node("span", rule.name + " · v" + rule.version, {className: "chip"})));
  updateActions();
}

function renderJob() {
  const job = data.jobs[0];
  $("job").hidden = !job;
  $("job").dataset.jobId = job?.id || "";
  if (!job) return;
  const state = {running: "标注进行中", completed: "标注完成", partial: "部分完成", interrupted: "任务中断"}[job.state];
  const notes = job.evidence_notes || [];
  const errors = Object.entries(job.errors).map(([file, message]) => file + " · " + message);
  $("job").replaceChildren(node("span", state + " · " + job.done + "/" + job.total + " 通" +
    (notes.length ? "\n" + notes.join("；") : "") + (job.error ? "\n" + job.error : "") + (errors.length ? "\n" + errors.join("\n") : "")));
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
    const wasBusy = running();
    data = await post("state", {scan});
    renderFiles();
    renderJob();
    clearTimeout(poll);
    if (running()) poll = setTimeout(() => refresh().catch(error => report(error.message, true)), 650);
    else if (active && data.jobs[0] &&
        (wasBusy || priorJob?.id !== data.jobs[0].id)) await openFile(active, true);
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
    if (end - start < 1) tick.textContent += Math.floor(seconds * 1000 + 1e-6) % 10;
  });
  renderMarks();
}, (source, start, end, bins, signal) => post("waveform", {...source, start, end, bins}, false, signal));

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
    timeline.load(loaded.waveform, loaded.duration, {file, digest: loaded.id});
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
  const current = new Set(recording.evaluations.filter(row => row.current).map(row => row.config_hash));
  const stale = recording.evaluations.filter(row => !row.applicable && !current.has(row.config_hash));
  const notice = $("mapping-notice");
  notice.hidden = !stale.length;
  notice.textContent = stale.length ? "当前声道映射需重新标注：" + [...new Set(stale.map(row => row.name))].join("、") : "";
  $("evaluations").hidden = !recording.evaluations.length;
  $("evaluation-list").replaceChildren(...recording.evaluations.map(row => node("li",
    row.name + " · v" + row.version + " · " + (row.current ? "当前" : "历史") + " · " +
    (statuses[row.status] || row.status) + " · " + mappingText(row) + " · 批次 " + row.batch_id.slice(0, 8) +
    (row.note ? " · " + row.note : ""))));
}

function mappingText(row) {
  if (!row.mapping_recorded) return "检测声道未记录";
  return row.ai_channel == null ? "检测时 AI 声道未知" : "检测时 AI 在" + (row.ai_channel === 0 ? "左" : "右") + "声道";
}

function overviewStep() {
  if (recording.findings.length <= 24 || recording.duration <= 0) return 0;
  if (recording.duration > 60) return Math.max(60, 60 * Math.ceil(recording.duration / 3600));
  return recording.duration / 10;
}

function zoomTo(left, right) {
  hit = null;
  listenEnd = null;
  player.currentTime = left;
  timeline.showWindow(left, right);
  timeline.setEvidence(left, right, false);
  renderMarks();
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

function renderOverview(groups, step, start, end) {
  const overview = $("distribution");
  overview.replaceChildren();
  if (!step) return;
  const bins = Math.ceil(recording.duration / step - 1e-9);
  const grid = node("div", null, {className: "minute-grid"});
  grid.style.setProperty("--minutes", bins);
  grid.append(node("strong", "全程分布"));
  for (let minute = 0; minute < bins; minute++) grid.append(node("small", (step < 1 ? timeText : minuteText)(minute * step), {className: "muted"}));
  groups.forEach((items, key) => {
    const counts = Array(bins).fill(0);
    items.forEach(item => counts[Math.min(bins - 1, Math.floor(item.start_s / step))]++);
    const name = node("span", items[0].automatic.label, {className: "label", title: items[0].automatic.label});
    name.style.color = ruleColor(key);
    grid.append(name);
    counts.forEach((count, minute) => {
      const left = minute * step;
      const right = Math.min(recording.duration, left + step);
      const show = button(String(count), () => zoomTo(left, right), Math.abs(start - left) < 0.001 && end <= right + 0.001);
      show.setAttribute("aria-label", items[0].automatic.label + " " + (step < 1 ? timeText : minuteText)(left) + "，" + count + " 处");
      grid.append(show);
    });
  });
  overview.append(grid);
}

function renderLanes() {
  if (!recording || view[1] <= view[0]) {
    $("lanes").replaceChildren();
    return;
  }
  const [start, end] = view;
  const groups = groupedFindings(), step = overviewStep();
  renderOverview(groups, step, start, end);
  const lanes = [...groups].map(([key, items]) => {
    const lane = node("div", null, {className: "lane"});
    lane.style.setProperty("--mark", ruleColor(key));
    const title = items[0].automatic.label;
    const name = button(title + " · " + items.length + " 处", () => {
      if (hiddenLanes.has(key)) hiddenLanes.delete(key);
      else hiddenLanes.add(key);
      renderLanes();
    });
    name.className = "lane-name";
    name.title = title + " · v" + items[0].version + "（点击展开或收起轨道）";
    name.setAttribute("aria-expanded", !hiddenLanes.has(key));
    const plot = node("div", null, {className: "lane-plot"});
    const visible = items.filter(item => item.end_s > start && item.start_s < end);
    if (hiddenLanes.has(key)) {
      plot.append(node("small", "已收起", {className: "muted"}));
    } else if (step && end - start > step + 0.001) {
      plot.append(node("small", "选择上方时段查看标记", {className: "muted"}));
    } else if (visible.length > 24 && end - start > 0.1) {
      for (let i = 0; i < 8; i++) {
        const left = start + (end - start) * i / 8;
        const right = start + (end - start) * (i + 1) / 8;
        const count = visible.filter(item => item.start_s < right && item.end_s > left).length;
        const bucket = button(String(count), () => zoomTo(left, right));
        bucket.title = timeText(left) + "–" + timeText(right) + " · " + count + " 处";
        bucket.style.left = (12.5 * i) + "%";
        bucket.style.width = "12.5%";
        bucket.className = "density-bucket";
        plot.append(bucket);
      }
    } else {
      visible.forEach(item => {
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
  const local = overviewStep() && view[1] - view[0] < recording.duration - 0.001;
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
      row.append(node("td", item.label + " · v" + item.version),
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
  const step = overviewStep();
  if (step && (view[1] - view[0] > step + 0.001 || item.start_s < view[0] || item.start_s >= view[1])) {
    const left = Math.floor(item.start_s / step) * step;
    timeline.showWindow(left, Math.min(recording.duration, left + step));
  }
  player.currentTime = start;
  timeline.setEvidence(item.start_s, item.end_s, true);
  renderMarks();
  $("marks").querySelector("tr.active")?.scrollIntoView({block: "nearest"});
  if (!play) $("review-pane").scrollIntoView({block: "nearest"});
  if (play) player.play().catch(error => report(error.message, true));
}

function renderReview() {
  const pane = $("review-pane");
  pane.hidden = !hit;
  if (!hit) return;
  const item = hit;
  const group = groupedFindings().get(hitRef(item));
  pane.replaceChildren(node("strong", item.label + " · 第 " + (group.findIndex(row => row.id === item.id) + 1) + "/" + group.length + " 处"),
    node("span", mappingText(item) + " · " + (item.reason || "") + (item.comment ? " · " + item.comment : ""), {className: "muted"}),
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
  recording.findings = recording.findings.map(row => row.id === item.id ? {...row, ...result.finding} : row).sort(byTime);
  hit = {...item, ...result.finding};
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
    previewRule(saved, false);
    $("dialog-status").textContent = saved.name + " · " + ruleRef(saved) + " 已保存。";
    save.disabled = true;
  }, true), true);
  const downloadRule = button("导出规则 JSON", attempt(async () => {
    const config = isDraft ? (await post("validate", {config_text: JSON.stringify({...rule, version: version.value.trim()})})).config : rule;
    download(new Blob([JSON.stringify(config, null, 2)], {type: "application/json"}), config.id + "-" + config.version + ".json");
  }, true));
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

let apiKey = "", modelName = null, effort = "", speed = "default", systemPrompt = null;
let userPrompt = "", promptExpanded = false;
const effortLabels = {"": "模型默认", low: "低", medium: "中", high: "高", xhigh: "很高", max: "最高"};
const speedLabels = {default: "标准", fast: "Fast", ultrafast: "UltraFast"};

function setOptions(select, values, labels, selected) {
  select.replaceChildren(...values.map(value => new Option(labels[value], value)));
  select.value = values.includes(selected) ? selected : values[0];
}

function modelControls() {
  const model = node("select", null, {ariaLabel: "模型"});
  const custom = textField("", {placeholder: "输入模型 ID", maxLength: 100});
  const customLabel = label("自定义模型 ID", custom);
  const reasoning = node("select", null, {ariaLabel: "推理强度"});
  const tier = node("select", null, {ariaLabel: "速度模式"});
  const names = Object.fromEntries(Object.entries(data.models).map(([id, spec]) => [id, spec.name]));
  const initial = modelName ?? data.model;
  setOptions(model, [...Object.keys(names), "custom"], {...names, custom: "自定义模型"}, names[initial] ? initial : "custom");
  custom.value = names[initial] ? "" : initial;
  function sync() {
    customLabel.hidden = model.value !== "custom";
    modelName = customLabel.hidden ? model.value : custom.value.trim();
    const spec = data.models[modelName] || data.custom_model;
    setOptions(reasoning, ["", ...spec.efforts], effortLabels, effort);
    reasoning.disabled = !spec.efforts.length;
    if (reasoning.disabled) reasoning.options[0].text = "不设置";
    setOptions(tier, spec.speeds, speedLabels, speed);
    effort = reasoning.value;
    speed = tier.value;
  }
  model.onchange = sync;
  custom.oninput = sync;
  reasoning.onchange = () => { effort = reasoning.value; };
  tier.onchange = () => { speed = tier.value; };
  sync();
  const row = node("div", null, {className: "ai-settings"});
  row.append(label("模型", model), label("推理强度", reasoning), label("速度模式", tier));
  return [row, customLabel];
}

function promptEditor() {
  const details = node("details", null, {className: "prompt-editor", open: promptExpanded});
  details.ontoggle = () => { if (details.isConnected) promptExpanded = details.open; };
  const input = node("textarea", null, {value: systemPrompt ?? data.default_prompt, maxLength: 6000, rows: 7});
  input.oninput = () => { systemPrompt = input.value; };
  details.append(node("summary", "系统提示词"), label("给模型的规则设计指导", input),
    button("恢复默认提示词", () => { systemPrompt = null; input.value = data.default_prompt; }));
  return details;
}

function aiDialog() {
  if (!data.models) { report("正在读取模型设置，请稍后重试。"); return; }
  const key = textField(apiKey, {type: "password", autocomplete: "off", placeholder: data.key_on_server ? "使用本机服务的 Key" : "当前页面会话使用"});
  key.oninput = () => { apiKey = key.value; };
  const controls = modelControls();
  const question = node("textarea", null, {value: userPrompt, placeholder: "例如：用户说完之后，超过两秒 AI 才开始说话，请标出等候区间。", maxLength: 6000});
  question.oninput = () => { userPrompt = question.value; };
  const history = node("div", messages.map(m => m.role === "user" ? "你 · " + m.content : "AI · " + m.content).join("\n\n"),
    {className: "rule-preview"});
  const ask = button("生成或修改规则", attempt(async () => {
    const generation = dialogGeneration;
    const content = question.value.trim();
    if (!content) throw new Error("请描述要查什么");
    if (!modelName) throw new Error("请选择模型或填写自定义模型 ID");
    const next = [...messages.slice(-18), {role: "user", content}];
    $("dialog-status").textContent = "正在生成规则…";
    const result = await post("ai", {key: key.value, model: modelName, effort, service_tier: speed,
      system_prompt: systemPrompt ?? data.default_prompt, messages: next});
    if (!modal.open || generation !== dialogGeneration) return;
    if (userPrompt.trim() === content) userPrompt = "";
    draft = result.config;
    const reply = [result.explanation, result.question, draft ? JSON.stringify(draft) : ""].filter(Boolean).join("\n");
    messages = [...next, {role: "assistant", content: reply.slice(0, 6000)}];
    aiDialog();
  }, true), true);
  dialog("与 AI 定规则", label("GPT API Key", key), ...controls, promptEditor(),
    ...(messages.length ? [history] : []), label("用户提示词", question), ask,
    ...(draft ? [button("预览并保存草稿", () => previewRule(draft))] : []),
    ...(messages.length ? [button("开始新讨论", () => { messages = []; draft = null; userPrompt = ""; aiDialog(); })] : []));
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
function chooseFolder(initial, finish) {
  let current = null, parent = null, sequence = 0;
  const home = button("主目录", () => load(""));
  const up = button("上一级", () => load(parent));
  const hidden = checkbox("显示隐藏文件夹");
  hidden.onchange = () => load(current ?? initial, parent);
  const toolbar = node("div", null, {className: "folder-toolbar"});
  toolbar.append(home, up, label("显示隐藏文件夹", hidden));
  const location = node("div", null, {className: "folder-location", tabIndex: -1});
  const list = node("div", null, {className: "folder-list"});
  const actions = node("div", null, {className: "folder-actions"});
  const select = button("选用此文件夹", () => finish(current), true);
  actions.append(button("取消选择", () => finish(null)), select);
  dialog("选择文件夹", toolbar, location, list, actions);

  async function load(folder, back = null) {
    const request = ++sequence;
    const generation = dialogGeneration;
    const active = () => request === sequence && modal.open && generation === dialogGeneration;
    select.disabled = up.disabled = true;
    location.textContent = folder || "主目录";
    list.replaceChildren(node("span", "正在读取…", {className: "muted"}));
    try {
      const result = await post("folders", {folder, show_hidden: hidden.checked});
      if (!active()) return;
      current = result.folder;
      parent = result.parent;
      location.textContent = current;
      select.disabled = false;
      up.disabled = !parent;
      list.replaceChildren(...result.children.map(child => button(child.name, () => load(child.path, result.folder))));
      if (!list.childElementCount) list.append(node("span", "没有可显示的子文件夹", {className: "muted"}));
      location.focus({preventScroll: true});
    } catch (error) {
      if (!active()) return;
      current = folder;
      parent = back;
      up.disabled = !parent;
      list.replaceChildren(node("p", error.message, {role: "alert"}));
    }
  }
  load(initial);
}

function folderDialog(folder, channel) {
  const browse = button("选择文件夹…", () => chooseFolder(folder.value.trim(), selected => {
    if (selected !== null) folder.value = selected;
    folderDialog(folder, channel);
  }));
  const field = node("div", null, {className: "folder-field"});
  field.append(label("本机文件夹绝对路径", folder), browse);
  dialog("关联录音文件夹", field, label("AI 所在声道", channel),
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
        hiddenLanes.clear();
        $("file-search").value = "";
        report("");
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
}
$("folder-open").onclick = () => {
  const folder = textField(data.folder, {placeholder: "/绝对路径/录音文件夹", required: true});
  const channel = node("select");
  channel.append(...[["", "未知"], ["0", "左声道"], ["1", "右声道"]].map(([value, title]) =>
    node("option", title, {value})));
  channel.value = data.ai_channel == null ? "" : String(data.ai_channel);
  folderDialog(folder, channel);
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
  const history = checkbox("包含历史批次");
  const files = [...checkedFiles];
  const hidden = files.length - visibleFiles().filter(item => checkedFiles.has(item.id)).length;
  dialog("导出所选录音", node("p", "已勾选 " + files.length + " 通" + (hidden ? " · " + hidden + " 通在当前筛选外" : "")),
    label("仅数据（省略原录音）", dataOnly), label("仅已确认标注", confirmed), label("包含历史批次（逐次导出）", history),
    button("下载 ZIP", attempt(async () => {
      $("dialog-status").textContent = "正在核对所选录音并生成 ZIP…";
      const blob = await post("export", {files, data_only: dataOnly.checked, confirmed_only: confirmed.checked, history: history.checked}, true);
      download(blob, "recordings-review.zip");
      modal.close();
    }, true), true));
};
darkMode.addEventListener("change", renderLanes);
for (const id of ["duplexFit", "duplexZoomIn", "duplexZoomOut"]) $(id).addEventListener("click", renderMarks);
refresh(true).catch(error => report(error.message, true));
