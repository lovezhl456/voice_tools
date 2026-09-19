import html
from pathlib import Path

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .dataset import records_from
from .detector import CANDIDATES
from .review import check_window, evaluate, result_index


def compare(baseline_path, candidate_path, golden_path, output):
    before, after = result_index(baseline_path), result_index(candidate_path)
    if set(before) != set(after):
        raise ValueError("两个版本的样本/机会集合不一致；不能静默缩小比较范围")
    baseline_records, candidate_records = records_from(baseline_path), records_from(candidate_path)
    if any("error" in r for r in baseline_records + candidate_records):
        raise ValueError("版本结果包含失败录音，请先处理错误")
    if {r["sample_id"] for r in baseline_records} != {r["sample_id"] for r in candidate_records}:
        raise ValueError("两个版本录音集合不一致")
    old_configs = {r["sample_id"]: r["result"]["config"] for r in baseline_records}
    new_configs = {r["sample_id"]: r["result"]["config"] for r in candidate_records}
    for record in baseline_records + candidate_records:
        events = record.get("events", {})
        if not ("opportunities" in events and "user_speech" in events):
            raise ValueError("版本对比需要固定机会和用户讲话区间；请先 qa freeze，人工核对事件后重新分析")
    for sample in old_configs:
        if old_configs[sample]["timeout_s"] != new_configs[sample]["timeout_s"]:
            raise ValueError("应答阈值不同会改变迟答口径；请使用相同业务阈值比较检测器")
    for key in before:
        check_window(before[key], after[key])
    old_metrics, new_metrics = evaluate(golden_path, baseline_path), evaluate(golden_path, candidate_path)
    human = {(r["sample_id"], r["opportunity_id"]): r for r in read_json(golden_path)["labels"]}
    changes = []
    for key in sorted(before):
        a, b = before[key], after[key]
        if a["status"] == b["status"] and a.get("latency_s") == b.get("latency_s"):
            continue
        kind = "new_candidate" if a["status"] not in CANDIDATES and b["status"] in CANDIDATES else "removed_candidate" if a["status"] in CANDIDATES and b["status"] not in CANDIDATES else "changed"
        changes.append({"sample_id": key[0], "opportunity_id": key[1], "at_s": a["at_s"], "observed_until_s": a["observed_until_s"],
                        "change": kind, "before": a["status"], "after": b["status"], "before_latency_s": a.get("latency_s"),
                        "after_latency_s": b.get("latency_s"), "human_decision": human.get(key, {}).get("decision")})
    result = {"schema_version": "1.0", "tool_version": __version__, "baseline_sha256": sha256(baseline_path),
              "candidate_sha256": sha256(candidate_path), "golden_sha256": sha256(golden_path),
              "baseline": old_metrics, "candidate": new_metrics, "changes": changes,
              "configuration_changes": {s: {k: [old_configs[s].get(k), new_configs[s].get(k)] for k in set(old_configs[s]) | set(new_configs[s]) if old_configs[s].get(k) != new_configs[s].get(k)} for s in old_configs},
              "notice": "候选消失不等于改进；请结合人工标签、漏报、观察不足和分层覆盖率判断。"}
    output = new_output(output)
    write_json(output / "comparison.json", result)
    def percent(value):
        return "—" if value is None else f"{value:.1%}"
    def interval(value):
        return "—" if value is None else "–".join(percent(x) for x in value)
    rows = []
    for name, metrics in (("基线", old_metrics), ("候选版本", new_metrics)):
        rows.append(f"<tr><th>{name}</th><td>{metrics['evaluated']}</td><td>{percent(metrics['precision'])} / {metrics['precision_denominator']}</td><td>{interval(metrics['precision_ci95'])}</td><td>{percent(metrics['recall'])} / {metrics['recall_denominator']}</td><td>{interval(metrics['recall_ci95'])}</td><td>{metrics['abstained']}</td></tr>")
    changes_html = "".join("<tr>" + "".join(f"<td>{html.escape(str(item.get(k, '')))}</td>" for k in ("sample_id", "opportunity_id", "change", "before", "after", "human_decision")) + "</tr>" for item in changes)
    import json
    details = html.escape(json.dumps({"baseline": old_metrics, "candidate": new_metrics, "configuration_changes": result["configuration_changes"]}, ensure_ascii=False, indent=2))
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>录音质检版本对比</title><style>body{font:15px/1.6 system-ui;color:#183246;background:#f3f6f9;margin:0}main{max-width:1200px;padding:20px;margin:auto}section{background:white;padding:18px;border-radius:12px;margin:16px 0}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#076e79}</style><main><h1>录音质检版本对比</h1><p>候选减少不代表改进。指标仅代表当前人工标签集；观察不足不会算作通过。比例后的数字为分母，区间为 Wilson 95% 区间（按机会计算，不替代通话级抽样验证）。</p><section class="scroll"><table><tr><th>版本</th><th>计分数</th><th>Precision / 分母</th><th>95% 区间</th><th>Recall / 分母</th><th>95% 区间</th><th>无法判定</th></tr>'''
    document += "".join(rows) + "</table></section><section class='scroll'><h2>逐条变化</h2><table><tr><th>样本</th><th>机会</th><th>变化</th><th>原结果</th><th>新结果</th><th>人工标签</th></tr>" + changes_html + "</table>"
    if not changes:
        document += "<p>没有状态或延迟变化。</p>"
    document += "</section><section><h2>分层指标与参数</h2><pre>" + details + "</pre></section><a href='comparison.json'>下载完整 JSON</a></main></html>"
    (output / "report.html").write_text(document, encoding="utf-8")
    return result
