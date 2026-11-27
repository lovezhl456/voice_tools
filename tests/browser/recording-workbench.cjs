"use strict";
// Run against an installed wheel, via check_review.py's --out package directory.
const assert = require("node:assert/strict");
const {spawn, spawnSync} = require("node:child_process");
const {createHash} = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("@playwright/test");

const [root, python, packageRoot] = process.argv.slice(2);
const rate = 8000;

async function audio(filename, seconds, left, right) {
  const bytes = rate * seconds * 4;
  const wav = Buffer.alloc(44 + bytes);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(2, 22);
  wav.writeUInt32LE(rate, 24);
  wav.writeUInt32LE(4 * rate, 28);
  wav.writeUInt16LE(4, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(bytes, 40);
  for (const [i, spans] of [left, right].entries()) {
    for (const [a, b] of spans) {
      for (let j = a * rate; j < b * rate; j++) wav.writeInt16LE(j % 2 ? 8500 : -8500, 44 + 4 * j + 2 * i);
    }
  }
  await fs.writeFile(filename, wav);
  return createHash("sha256").update(wav).digest("hex");
}

async function until(check, wait = 5000) {
  const deadline = Date.now() + wait;
  while (!await check()) {
    if (Date.now() >= deadline) throw new Error("timed out waiting for state");
    await new Promise(resolve => setTimeout(resolve, 100));
  }
}

async function inspectWaveform(page) {
  const wave = page.locator("#duplexWave.can-seek");
  await wave.waitFor();
  const before = await wave.boundingBox();
  await page.getByRole("button", {name: "放大查看", exact: true}).click();
  const area = await wave.boundingBox();
  assert.ok(area.height > before.height * 1.5);
  const point = ratio => area.x + area.width * ratio;
  const y = area.y + area.height / 3;
  const detailResponse = predicate => page.waitForResponse(response => {
    if (!response.url().endsWith("/api/recordings/waveform") || response.status() !== 200) return false;
    return predicate(response.request().postDataJSON());
  });
  const selected = detailResponse(req => req.end - req.start < 1);
  await page.mouse.move(point(.26), y);
  await page.mouse.down();
  await page.mouse.move(point(.16), y, {steps: 5});
  await page.mouse.up();
  const detail = await (await selected).json();
  assert.equal(detail.channels.length, 2);
  assert.ok(detail.channels[0].every(([lo, hi]) => lo === 0 && hi === 0));
  assert.ok(detail.channels[1].some(([lo, hi]) => lo === -8500 / 32768 && hi === 8500 / 32768));
  assert.ok(await page.locator("#player").evaluate(el => el.paused && el.currentTime > 1 && el.currentTime < 1.5));
  const next = detailResponse(req => req.start >= 2);
  await page.getByRole("button", {name: "查看后一段波形"}).click();
  await next;
  await page.getByRole("button", {name: "查看前一段波形"}).click();
  const fine = detailResponse(req => req.end - req.start <= .021);
  const plus = page.getByRole("button", {name: "放大录音时间轴"});
  for (let i = 0; i < 10 && await plus.isEnabled(); i++) await plus.click();
  const samples = await (await fine).json();
  assert.equal(samples.samples, true);
  assert.equal(samples.bin_s, 1 / rate);
  assert.ok(samples.channels[1].every(([lo, hi]) => lo === hi && Math.abs(lo) === 8500 / 32768));
  await page.getByRole("button", {name: "全长", exact: true}).click();
  assert.ok(await page.locator("#wavePan").isHidden());
  await page.getByRole("button", {name: "收起大图"}).click();
  await wave.click({position: {x: before.width / 2, y: before.height / 2}});
  assert.ok(await page.locator("#player").evaluate(el => Math.abs(el.currentTime - 6.5) < .1));
}

async function main() {
  await fs.mkdir(root);
  const inputs = path.join(root, "inputs");
  await fs.mkdir(inputs);
  await fs.mkdir(path.join(inputs, ".隐藏目录"));
  const digestA = await audio(path.join(inputs, "a.wav"), 13, [[0, 1], [11, 12]], [[0, 2], [4, 5], [7, 8], [10, 13]]);
  const digestB = await audio(path.join(inputs, "b.wav"), 12, [[0, 1], [6, 7]], [[3, 4], [8, 9]]);
  const dense = Array.from({length: 50}, (_, i) => [5 + i * 12, 6 + i * 12]);
  await audio(path.join(inputs, "dense.wav"), 720, [[0, 5]], dense);
  const state = path.join(root, "workspace");
  const options = {env: {...process.env, VOICE_TOOLS_OPENAI_MODEL: "gpt-4o-mini", PYTHONPATH: packageRoot}, stdio: ["ignore", "pipe", "pipe"]};
  const mockedOpenAI = `
import io, json
from voice_tools.tools.detect import rule_drafting
from voice_tools.cli import main

class OpenAIStub:
    def open(self, request, timeout):
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer fixture-test-key"
        payload = json.loads(request.data)
        assert payload["store"] is False and payload["text"]["format"]["strict"] is True
        assert all(set(message) == {"role", "content"} for message in payload["input"])
        second = len(payload["input"]) > 1
        assert "能力目录" in payload["instructions"]
        if payload["model"] == "custom-model-test":
            assert payload["service_tier"] == "default" and "reasoning" not in payload
        elif second:
            assert payload["model"] == "gpt-4o-mini" and "reasoning" not in payload
            assert payload["service_tier"] == "fast" and "每次给出独立区间" in payload["instructions"]
        else:
            assert payload["model"] == "gpt-6.2-sol" and payload["reasoning"] == {"effort": "high"}
            assert payload["service_tier"] == "ultrafast" and payload["instructions"].startswith("逐次标出真实区间。")
        assert all("逐次标出真实区间。" not in message["content"] for message in payload["input"])
        config = {"schema_version":"2.0","id":"ai-quiet","version":"1","name":"AI 生成静音规则","kind":"quiet","min_s":3}
        answer = {"explanation":"按双轨交集标记", "question":"" if second else "阈值用几秒？",
                  "rule_json":json.dumps(config) if second else ""}
        result = {"status":"completed","output":[{"type":"message","content":[
            {"type":"output_text","text":json.dumps(answer, ensure_ascii=False)}]}]}
        return io.BytesIO(json.dumps(result).encode())

rule_drafting.build_opener = lambda *args: OpenAIStub()
raise SystemExit(main())
`;
  const server = spawn(python, ["-c", mockedOpenAI, "--json", "detect", "serve", "--db",
    path.join(state, "library.sqlite3"), "--out", state, "--port", "0"], options);
  let out = "", err = "", browser;
  server.stdout.on("data", chunk => { out += chunk.toString(); });
  server.stderr.on("data", chunk => { err += chunk.toString(); });
  try {
    await until(() => out.includes("\n") || server.exitCode !== null);
    assert.equal(server.exitCode, null, err);
    const entry = JSON.parse(out.split("\n")[0]);
    browser = await chromium.launch();
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, acceptDownloads: true});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(entry.recording_workbench_url);
    await page.getByRole("button", {name: "关联", exact: true}).click();
    const pathField = page.getByLabel("本机文件夹绝对路径");
    const browse = page.getByRole("button", {name: "选择文件夹…", exact: true});
    const currentFolder = page.locator(".folder-location");
    const selectFolder = page.getByRole("button", {name: "选用此文件夹"});
    await page.getByLabel("AI 所在声道").selectOption("1");
    await browse.click();
    await until(() => selectFolder.isEnabled());
    await page.getByRole("button", {name: "取消选择"}).click();
    assert.equal(await pathField.inputValue(), "");
    assert.equal(await page.getByLabel("AI 所在声道").inputValue(), "1");

    await pathField.fill(path.join(root, "不存在"));
    await browse.click();
    await page.getByRole("alert").filter({hasText: "无法打开"}).waitFor();
    assert.ok(await selectFolder.isDisabled());
    await page.getByRole("button", {name: "主目录", exact: true}).click();
    await until(() => selectFolder.isEnabled());
    await page.getByRole("button", {name: "取消选择"}).click();

    await pathField.fill(root);
    await browse.click();
    await page.getByRole("button", {name: "inputs", exact: true}).click();
    await page.getByText("没有可显示的子文件夹").waitFor();
    assert.equal(await page.getByRole("button", {name: "a.wav", exact: true}).count(), 0);
    await page.getByLabel("显示隐藏文件夹").check();
    await page.getByRole("button", {name: ".隐藏目录", exact: true}).click();
    await page.getByText("没有可显示的子文件夹").waitFor();
    assert.equal(await currentFolder.textContent(), path.join(inputs, ".隐藏目录"));
    await page.getByRole("button", {name: "上一级"}).click();
    await page.getByRole("button", {name: ".隐藏目录", exact: true}).waitFor();
    await selectFolder.click();
    assert.equal(await pathField.inputValue(), inputs);
    assert.equal(await page.getByLabel("AI 所在声道").inputValue(), "1");
    assert.equal(await page.locator("#files button").count(), 0);
    await page.getByRole("button", {name: "关联并扫描"}).click();
    await page.getByRole("button", {name: "a.wav", exact: true}).click();
    await page.locator("#player").waitFor();
    assert.match(await page.locator("#marks").textContent(), /暂无标注/);
    await inspectWaveform(page);
    await page.getByRole("checkbox", {name: "导出 a.wav"}).check();
    await page.getByRole("checkbox", {name: "导出 b.wav"}).check();
    await page.getByRole("button", {name: "b.wav", exact: true}).click();
    await page.getByRole("button", {name: "导出所选录音（2）"}).waitFor();

    async function importRule(id, kind, name) {
      await page.getByRole("button", {name: "导入规则", exact: true}).click();
      const rule = {schema_version: "2.0", id, version: "1", name, kind, min_s: 0.8};
      const rulePath = path.join(root, id + ".json");
      await fs.writeFile(rulePath, JSON.stringify(rule));
      await page.getByLabel("选择规则 JSON").setInputFiles(rulePath);
      await page.getByRole("button", {name: "存入全局规则库"}).click();
      await page.getByRole("status").filter({hasText: "已保存。"}).waitFor();
      await page.getByRole("button", {name: "关闭窗口"}).click();
    }
    await importRule("quiet", "quiet", "双轨安静");
    await importRule("pause", "output_pause", "输出停顿");
    await importRule("wait", "response_wait", "应答等待");
    async function choose(names) {
      await page.getByRole("button", {name: "选择本次规则"}).click();
      for (const title of ["双轨安静", "输出停顿", "应答等待"]) {
        const control = page.getByRole("checkbox", {name: "本次启用 " + title + " 1"});
        if (await control.isChecked() !== names.includes(title)) await control.click();
      }
      await page.getByRole("button", {name: "关闭窗口"}).click();
    }
    async function record() {
      const before = await page.locator("#job").getAttribute("data-job-id");
      await page.getByRole("button", {name: "标注当前录音"}).click();
      await page.getByRole("button", {name: "开始标注"}).click();
      await page.waitForFunction(previous => {
        const job = document.querySelector("#job");
        return job.dataset.jobId !== previous && /标注完成|部分完成/.test(job.textContent);
      }, before);
    }
    await choose(["双轨安静"]);
    await page.getByRole("button", {name: "a.wav", exact: true}).click();
    await record();
    await page.locator("#marks tr").filter({hasText: "双轨安静"}).first().waitFor();
    assert.equal(await page.locator("#marks tr").filter({hasText: "双轨安静"}).count(), 3);
    await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).check();
    await page.locator("#notice").filter({hasText: "复核已保存"}).waitFor();
    await choose(["输出停顿"]);
    await record();
    await page.getByRole("checkbox", {name: "确认 输出停顿 00:02.00"}).waitFor();
    assert.equal(await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).isChecked(), true);
    assert.equal(await page.getByRole("checkbox", {name: "确认 输出停顿 00:02.00"}).isChecked(), false);
    await page.screenshot({path: path.join(root, "overlap.png")});
    await page.getByRole("button", {name: "导出所选录音（2）"}).click();
    await page.getByRole("checkbox", {name: "仅已确认标注"}).check();
    const pendingDownload = page.waitForEvent("download");
    await page.getByRole("button", {name: "下载 ZIP"}).click();
    await (await pendingDownload).saveAs(path.join(root, "review.zip"));

    await page.reload();
    await page.getByRole("button", {name: "a.wav", exact: true}).click();
    await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).waitFor();
    assert.equal(await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).isChecked(), true);
    await choose(["应答等待"]);
    await page.getByRole("button", {name: "b.wav", exact: true}).click();
    await record();
    await page.getByRole("checkbox", {name: "确认 应答等待 00:01.00"}).waitFor();
    await choose(["双轨安静"]);
    await page.getByRole("button", {name: "dense.wav", exact: true}).click();
    await record();
    await page.locator("#distribution").filter({hasText: "全程分布"}).waitFor();
    assert.match(await page.locator("#mark-count").textContent(), /50 \/ 50/);
    await page.getByRole("button", {name: /^双轨安静 04:00/}).click();
    await page.locator("#lanes button").first().waitFor();
    assert.ok(await page.locator("#marks tr").count() < 50);
    await page.emulateMedia({colorScheme: "dark"});
    await page.screenshot({path: path.join(root, "dense-dark.png")});

    await page.getByRole("button", {name: "与 AI 定规则"}).click();
    await page.getByLabel("用户提示词").fill("静音超过两秒");
    await page.getByRole("button", {name: "生成或修改规则"}).click();
    await page.locator("#dialog-status").filter({hasText: "GPT API Key"}).waitFor();
    await page.getByLabel("GPT API Key").fill("fixture-test-key");
    assert.ok(await page.getByLabel("推理强度").isDisabled());
    assert.equal(await page.getByLabel("速度模式").locator("option").count(), 2);
    assert.equal(await page.getByLabel("给模型的规则设计指导").isVisible(), false);
    await page.getByText("系统提示词", {exact: true}).click();
    const prompt = page.getByLabel("给模型的规则设计指导");
    assert.match(await prompt.inputValue(), /每次给出独立区间/);
    await prompt.fill("逐次标出真实区间。");
    await page.getByLabel("模型", {exact: true}).selectOption("gpt-6.2-sol");
    await page.getByLabel("推理强度").selectOption("high");
    await page.getByLabel("速度模式").selectOption("ultrafast");
    assert.equal(await page.getByLabel("用户提示词").inputValue(), "静音超过两秒");
    await page.getByRole("button", {name: "生成或修改规则"}).click();
    await page.locator("#dialog-body").filter({hasText: "阈值用几秒？"}).waitFor();
    assert.equal(await prompt.inputValue(), "逐次标出真实区间。");
    await page.getByRole("button", {name: "恢复默认提示词"}).click();
    await page.getByLabel("模型", {exact: true}).selectOption("gpt-4o-mini");
    assert.equal(await page.getByLabel("速度模式").inputValue(), "default");
    await page.getByLabel("速度模式").selectOption("fast");
    await page.getByLabel("用户提示词").fill("改用三秒");
    await page.getByRole("button", {name: "生成或修改规则"}).click();
    await page.getByRole("button", {name: "预览并保存草稿"}).click();
    await page.getByRole("button", {name: "存入全局规则库"}).click();
    await page.getByRole("status").filter({hasText: "已保存。"}).waitFor();
    await page.getByRole("button", {name: "关闭窗口"}).click();
    await page.getByRole("button", {name: "与 AI 定规则"}).click();
    await page.getByRole("button", {name: "开始新讨论"}).click();
    await page.getByLabel("模型", {exact: true}).selectOption("custom");
    await page.getByLabel("自定义模型 ID").fill("custom-model-test");
    assert.equal(await page.getByLabel("速度模式").inputValue(), "default");
    assert.ok(await page.getByLabel("推理强度").isDisabled());
    await page.getByLabel("用户提示词").fill("请按标准速度生成规则");
    await page.getByRole("button", {name: "生成或修改规则"}).click();
    await page.locator("#dialog-body").filter({hasText: "阈值用几秒？"}).waitFor();
    await page.getByRole("button", {name: "关闭窗口"}).click();
    async function associate(folder, channel) {
      await page.getByRole("button", {name: "关联", exact: true}).click();
      await page.getByLabel("本机文件夹绝对路径").fill(folder);
      await page.getByLabel("AI 所在声道").selectOption(channel);
      await page.getByRole("button", {name: "关联并扫描"}).click();
      await page.getByRole("dialog").waitFor({state: "hidden"});
    }
    await page.getByRole("button", {name: "a.wav", exact: true}).click();
    await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).waitFor();
    const originalRows = await page.locator("#marks tr").count();
    await choose(["双轨安静", "输出停顿"]);
    await record();
    assert.equal(await page.locator("#marks tr").count(), originalRows);
    assert.ok(await page.getByRole("checkbox", {name: "确认 双轨安静 00:02.00"}).isChecked());

    await associate(inputs, "0");
    await page.locator("#mapping-notice").filter({hasText: "输出停顿"}).waitFor();
    assert.equal(await page.locator("#marks tr").filter({hasText: "输出停顿"}).count(), 0);
    await page.getByRole("checkbox", {name: "导出 a.wav"}).check();
    for (const history of [false, true]) {
      await page.getByRole("button", {name: "导出所选录音（1）"}).click();
      await page.getByRole("checkbox", {name: "仅数据", exact: true}).check();
      if (history) await page.getByRole("checkbox", {name: "包含历史批次"}).check();
      const download = page.waitForEvent("download");
      await page.getByRole("button", {name: "下载 ZIP"}).click();
      await (await download).saveAs(path.join(root, history ? "history.zip" : "current.zip"));
    }
    await associate(inputs, "1");
    await page.getByRole("checkbox", {name: "确认 输出停顿 00:02.00"}).waitFor();
    await page.getByRole("button", {name: "导入规则", exact: true}).click();
    await page.getByLabel("选择规则 JSON").setInputFiles(path.join(root, "quiet.json"));
    await page.getByLabel("规则版本").fill("2");
    await page.getByRole("button", {name: "存入全局规则库"}).click();
    await page.getByRole("status").filter({hasText: "@2 已保存。"}).waitFor();
    const ruleDownload = page.waitForEvent("download");
    await page.getByRole("button", {name: "导出规则 JSON"}).click();
    const savedRule = await ruleDownload;
    assert.equal(savedRule.suggestedFilename(), "quiet-2.json");
    await savedRule.saveAs(path.join(root, "saved-rule.json"));
    assert.equal(JSON.parse(await fs.readFile(path.join(root, "saved-rule.json"))).version, "2");
    await page.getByRole("button", {name: "关闭窗口"}).click();

    const short = {schema_version: "1.0", id: "windows", name: "短窗", version: "1",
      window: {kind: "sliding", length_s: 0.1, step_s: 0.1},
      metrics: {level: {kind: "rms_dbfs", channel: 0}},
      rules: ["甲", "乙"].map((label, i) => ({id: "short-" + i, label, when: {metric: "level", op: "lt", value: -40}}))};
    await page.getByRole("button", {name: "导入规则", exact: true}).click();
    await page.getByLabel("选择规则 JSON").setInputFiles({name: "short.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(short))});
    await page.getByRole("button", {name: "存入全局规则库"}).click();
    await page.getByRole("status").filter({hasText: "已保存。"}).waitFor();
    await page.getByRole("button", {name: "关闭窗口"}).click();
    await page.getByRole("button", {name: "选择本次规则"}).click();
    await page.getByRole("checkbox", {name: "本次启用 短窗 1"}).check();
    await page.getByRole("button", {name: "关闭窗口"}).click();
    await record();
    await page.locator("#distribution").filter({hasText: "全程分布"}).waitFor();
    assert.ok(await page.getByRole("button", {name: /^甲 · .*处$/}).isVisible());
    assert.ok(await page.getByRole("button", {name: /^乙 · .*处$/}).isVisible());
    await page.getByRole("button", {name: /^甲 00:01/}).first().click();
    const lane = page.locator(".lane").filter({has: page.getByRole("button", {name: /^甲 ·/})});
    assert.ok(await lane.locator(".lane-plot button").count() > 0);
    await page.waitForFunction(() => !document.querySelector("#player").seeking);
    assert.equal(await page.locator("#annotationTime span").first().textContent(), "00:01.30");
    await lane.locator(".lane-name").click();
    assert.equal(await lane.locator(".lane-plot button").count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    const emptyFolder = path.join(root, "empty");
    await fs.mkdir(emptyFolder);
    await associate(emptyFolder, "");
    assert.ok(await page.locator("#job").isHidden());
    const verify = spawnSync(python, ["-c", `
import csv, hashlib, io, json, sys
from zipfile import ZipFile
with ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None
    manifest = json.loads(archive.read("recordings.json"))["recordings"]
    assert [item["file"] for item in manifest] == ["a.wav", "b.wav"]
    assert [hashlib.sha256(archive.read(item["path"])).hexdigest() for item in manifest] == sys.argv[2:]
    assert manifest[1]["status"] == "not_run"
    entries = json.loads(archive.read("annotations.json"))["findings"]
    entry, = entries
    assert (entry["start_s"], entry["end_s"], entry["status"], entry["version"]) == (2, 4, "confirmed", "1")
    row, = csv.DictReader(io.StringIO(archive.read("annotations.csv").decode("utf-8-sig")))
    assert row["status"] == "confirmed" and row["sha256"] == sys.argv[2]
    assert any(filename.startswith("rules/") for filename in archive.namelist())

for filename, history in [('current.zip', False), ('history.zip', True)]:
    from pathlib import Path
    with ZipFile(Path(sys.argv[1]).with_name(filename)) as archive:
        data = json.loads(archive.read('recordings.json'))
        assert data['selected_ai_channel'] == 0 and data['ai_channel'] == 1
        rows = json.loads(archive.read('annotations.json'))['findings']
        assert all(row['mapping_recorded'] and row['ai_channel'] == 1 for row in rows)
        assert any(row['definition_id'] == 'pause' for row in rows) == history
        if not history:
            assert len(rows) == 3 and sum(row['status'] == 'confirmed' for row in rows) == 1
`, path.join(root, "review.zip"), digestA, digestB], {encoding: "utf8"});
    assert.equal(verify.status, 0, verify.stderr);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(root, "verification.json"), JSON.stringify({digestA, digestB, entry, browserErrors: errors, openAI: "mocked-provider", zipVerified: true}, null, 2));
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
    await fs.writeFile(path.join(root, "server.log"), out + err);
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
