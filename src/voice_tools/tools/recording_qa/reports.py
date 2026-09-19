import csv
import html
import json
from pathlib import Path

LABELS = {
    "NO_OUTPUT_CANDIDATE": "无输出候选", "LATE_OUTPUT_CANDIDATE": "延迟输出候选",
    "OUTPUT_NEEDS_REVIEW": "有活动，需试听", "CENSORED": "观察时长不足",
    "EXCLUDED": "不计入应答", "INSUFFICIENT_EVIDENCE": "证据不足",
    "NO_OPPORTUNITIES": "无应答机会", "ERROR": "处理失败",
}
EVIDENCE = {"event_aligned": "已对齐会话事件", "acoustic_only": "仅声学证据"}
REASONS = {
    "outside_response_window": "处于排除阶段或无需回复",
    "duplicate_channels": "双声道相同，无法确认双方角色",
    "activity_after_deadline": "超时后才出现系统轨活动，需确认是否为有效回答",
    "activity_is_not_semantic_response": "检测到活动，请试听排除噪声、提示音和无关语音",
    "no_detected_output_before_deadline": "应答窗口达到阈值，未检测到系统轨活动",
    "observation_shorter_than_timeout": "挂机、打断或窗口结束发生在超时之前",
}
REVIEW_FIELDS = ["sample_id", "audio_sha256", "opportunity_id", "at_s", "observed_until_s",
                 "status", "decision", "reviewer", "reviewed_at", "notes"]
EXTRA_REVIEW_FIELDS = ["first_audible_s", "expected_response", "deadline_s", "policy_id", "scenario", "line_id", "group_id", "split"]


def csv_safe(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_safe(row.get(key, "")) for key in fields})


def render_report(output, records, summary, hide_paths=False):
    with (output / "results.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    summaries, reviews, sections = [], [], []
    esc = lambda value: html.escape(str(value), quote=True)
    for record in records:
        result = record.get("result", {})
        summaries.append({"file": record["input"], "audio_sha256": record.get("audio_sha256", ""),
                          "status": result.get("status", "ERROR"), "candidates": result.get("candidate_count", 0),
                          "opportunities": len(result.get("opportunities", [])), "error": record.get("error", "")})
        if "error" in record:
            sections.append(f'<section><h2>{esc(Path(record["input"]).name)}</h2><p class="bad">处理失败：{esc(record["error"])}</p></section>')
            continue
        rows = []
        for item in result["opportunities"]:
            reviews.append({"sample_id": record["sample_id"], "audio_sha256": record["audio_sha256"], "opportunity_id": item["id"],
                            "at_s": item["at_s"], "observed_until_s": item["observed_until_s"],
                            "status": item["status"]})
            playback = ""
            if record.get("audio_copy"):
                start = max(0, item["at_s"] - 2)
                end = min(result["duration_s"], item["observed_until_s"] + 1)
                playback = f'<audio controls preload="none" src="{esc(record["audio_copy"])}#t={start},{end}"></audio>'
            rows.append(f'<tr><td>{esc(item["id"])}</td><td>{item["at_s"]:.2f}–{item["observed_until_s"]:.2f}s</td>'
                        f'<td>{esc(LABELS[item["status"]])}</td><td>{esc(EVIDENCE[item["evidence_level"]])}</td>'
                        f'<td>{esc(REASONS[item["reason_code"]])}{playback}</td></tr>')
        warnings = "".join(f"<li>{esc(warning)}</li>" for warning in result["warnings"])
        table = ('<div class="table"><table><thead><tr><th>机会</th><th>观察窗口</th><th>结果</th><th>证据</th><th>理由 / 试听</th></tr></thead><tbody>'
                 + "".join(rows) + '</tbody></table></div>') if rows else f'<p>{esc(LABELS.get(result["status"], result["status"]))}</p>'
        display_path = Path(record["input"]).name if hide_paths else record["input"]
        sections.append(f'<section><h2>{esc(Path(record["input"]).name)}</h2><details><summary>来源</summary><p>{esc(display_path)}</p></details>'
                        f'<p>采样率 {result["sample_rate"]} Hz · {result["duration_s"]:.2f} 秒 · '
                        f'AI 声道 {result["config"]["system_channel"]}（0 左 / 1 右）</p><ul>{warnings}</ul>{table}</section>')
    write_csv(output / "summary.csv", ["file", "audio_sha256", "status", "candidates", "opportunities", "error"], summaries)
    write_csv(output / "review.csv", REVIEW_FIELDS + EXTRA_REVIEW_FIELDS, reviews)
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>双声道录音质检报告</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f3f6fa;color:#182535;margin:0}main{max-width:1120px;margin:auto;padding:24px}
h1{font-size:30px}h2{font-size:19px;overflow-wrap:anywhere}section,.intro{background:#fff;padding:20px;margin:18px 0;border-radius:12px;border:1px solid #dce3ed}
li,p{overflow-wrap:anywhere}.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:12px;border-bottom:1px solid #dce3ed;vertical-align:top}
audio{display:block;width:240px;margin-top:8px}.bad{color:#a62924}.count{font-size:20px;font-weight:600}@media(max-width:600px){main{padding:12px}section,.intro{padding:14px}h1{font-size:25px}}
</style><main><h1>双声道录音质检报告</h1><div class="intro">'''
    document += f'<p class="count">{summary["files"]} 个文件 · {summary["candidates"]} 个候选 · {summary["errors"]} 个处理错误</p>'
    document += '<p><a href="review.html"><strong>进入交互复核：双轨波形、单轨试听与人工标注 →</strong></a></p>'
    document += ('<p>所有结果都需要复核。有活动不等于 AI 正确回答；录音无法单独定位模型、TTS 或 RTP 根因。'
                 '缺少事件的结果仅是声学候选。未附带音频时，可使用 --include-audio 生成便于试听的本地报告。</p>'
                 '<p>人工标注：编辑同目录 review.csv 的 decision、reviewer、reviewed_at、notes。'
                 'decision 取 missing / delayed / audible / exclude / uncertain；时间使用含时区的 ISO 8601。'
                 '确认后使用 qa promote 晋升，自动输出不会被自动标为黄金数据。</p>'
                 '<p><a href="review.csv" download>下载人工复核表</a> · <a href="summary.csv" download>下载文件汇总</a></p></div>')
    document += "".join(sections) + "</main></html>"
    (output / "report.html").write_text(document, encoding="utf-8")
    from .workbench import render_workbench
    render_workbench(output, records, summary, hide_paths)
