"""冻结时间轴供人工核对；快照永远不生成自己的黄金答案。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from voice_tools.core.files import new_output, sha256, write_json


def records_from(path):
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or record.get("schema_version") != "1.0":
                raise ValueError("不支持的结果 schema_version")
            if "error" not in record:
                result = record.get("result")
                if (not isinstance(record.get("input"), str) or not isinstance(result, dict)
                        or not isinstance(result.get("config"), dict) or "timeout_s" not in result["config"]
                        or not isinstance(result.get("health"), dict) or "duplicate_channels" not in result["health"]
                        or result.get("channels") not in (1, 2)):
                    raise ValueError("结果缺少录音来源、配置或健康字段")
            records.append(record)
    if not records:
        raise ValueError("结果文件为空")
    return records


def freeze(results_path, output):
    from .review import result_index
    result_index(results_path)  # 先校验所有机会、摘要和窗口。
    records = records_from(results_path)
    if any("error" in r for r in records):
        raise ValueError("结果含失败录音，请先处理错误再冻结")
    if len({r["sample_id"] for r in records}) != len(records):
        raise ValueError("结果包含重复录音/事件，请先去重")
    for record in records:
        source = Path(record["input"])
        if not source.is_file() or sha256(source) != record["audio_sha256"]:
            raise ValueError("原始录音不可读或摘要变化，不能冻结")
        if record["result"]["channels"] != 2 or record["result"]["health"]["duplicate_channels"]:
            raise ValueError("单声道或重复声道不能建立独立双方时间轴")
    output = new_output(output)
    entries = []
    for record in records:
        result, original = record["result"], record.get("events", {})
        events = {"schema_version": "1.0", "system_channel": result["config"]["system_channel"],
                  "channel_verified": result["channel_verified"], "ai_end_s": result["ai_end_s"],
                  "user_speech": original.get("user_speech", [{"start_s": a, "end_s": b} for a, b in result["user_activity"]]),
                  "exclusions": original.get("exclusions", []), "opportunities": [],
                  "timeline_origin": "analysis_snapshot", "timeline_reviewed": False}
        if result["ai_start_s"] is not None:
            events["ai_start_s"] = result["ai_start_s"]
        if "output_events" in original:
            events["output_events"] = original["output_events"]
        if "alignment" in original:
            events["alignment"] = original["alignment"]
        originals = {op["id"]: op for op in original.get("opportunities", [])}
        for op in result["opportunities"]:
            events["opportunities"].append({"id": op["id"], "at_s": op["at_s"],
                                            "window_end_s": op["observed_until_s"],
                                            "expects_response": originals.get(op["id"], {}).get("expects_response", True)})
        target = output / (record["sample_id"] + ".wav")
        shutil.copyfile(record["input"], target)
        write_json(target.with_suffix(".events.json"), events)
        if record.get("preparation"):
            write_json(target.with_suffix(".provenance.json"), record["preparation"])
        entries.append({"audio": target.name, "audio_sha256": record["audio_sha256"],
                        "previous_sample_id": record["sample_id"], "events_sha256": sha256(target.with_suffix(".events.json"))})
    manifest = {"schema_version": "1.0", "created_at": datetime.now(timezone.utc).isoformat(),
                "source_results_sha256": sha256(results_path), "timeline_source": "analysis_snapshot",
                "notice": "这是待人工核对的时间轴快照，不是黄金标签。核对/修改事件后重新分析、试听、标注并晋升。", "recordings": entries}
    write_json(output / "manifest.json", manifest)
    return manifest
