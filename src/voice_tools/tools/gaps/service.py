"""Batch execution keeps audio detection even when optional evidence fails."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from voice_tools import __version__
from voice_tools.audio.io import read_wav
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .detector import Config, analyze


def discover(inputs):
    paths = set()
    for item in inputs:
        path = Path(item)
        if not path.exists():
            raise ValueError(f"输入不存在：{path}")
        if path.is_dir():
            paths.update(p.resolve() for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".wav")
        elif path.suffix.lower() == ".wav":
            paths.add(path.resolve())
        else:
            raise ValueError("gaps 仅接收 PCM16 WAV；其他格式请先使用 audio prepare")
    if not paths:
        raise ValueError("未发现 WAV 文件")
    if len(paths) > 200:
        raise ValueError("单批最多 200 个录音，请分批处理")
    return sorted(paths)


def analyze_batch(inputs, output, config=None, events=None, evidence=None, include_audio=False,
                  use_event_channel=True, hide_paths=False):
    config = config or Config()
    config.validate()
    paths = discover(inputs)
    if events and len(paths) != 1:
        raise ValueError("--events 只适用于单录音；批量请使用同名 .events.json")
    output = new_output(output)
    records = []
    result_bytes = 0
    interrupted = False
    for path in paths:
        record = {"schema_version": "1.0", "tool": "gaps", "tool_version": __version__, "input": str(path)}
        try:
            record["audio_sha256"] = sha256(path)
            event_path = Path(events) if events else path.with_suffix(".events.json")
            if event_path.exists() and event_path.stat().st_size > 4 * 1024**2:
                raise ValueError("事件文件超过 4 MiB 额度")
            metadata = read_json(event_path) if events or event_path.exists() else {}
            if not isinstance(metadata, dict):
                raise ValueError("事件文件须为对象")
            if event_path.exists():
                record.update(events=metadata, events_sha256=sha256(event_path))
            provenance_path = path.with_suffix(".provenance.json")
            detection_events = metadata
            mapping_unverified = False
            if provenance_path.exists():
                preparation = read_json(provenance_path)
                if not isinstance(preparation, dict) or preparation.get("output_sha256") != record["audio_sha256"]:
                    raise ValueError("音频准备溯源摘要不匹配")
                record["preparation"] = preparation
                mapping = preparation.get("time_mapping", {})
                if not isinstance(mapping, dict):
                    raise ValueError("音频准备 time_mapping 须为对象")
                if metadata and mapping.get("verified") is not True:
                    mapping_unverified = True
                    detection_events = {key: metadata[key] for key in ("schema_version", "system_channel", "channel_verified") if key in metadata}
            effective = replace(config, system_channel=metadata.get("system_channel", config.system_channel)) if use_event_channel else config
            audio = read_wav(path)
            record["result"] = analyze(audio, record["audio_sha256"], detection_events, effective)
            if mapping_unverified:
                record["result"]["warnings"].append("转换时间映射未核实，原事件仅作旁证，未参与间隙判断")
            if record["result"].get("processing_error"):
                record["error"] = record["result"]["processing_error"]
            record["sample_id"] = record["result"]["fingerprint"]
            if evidence:
                from .evidence import associate
                try:
                    record["evidence"] = associate(evidence, record)
                except (ValueError, OSError, KeyError, TypeError) as error:
                    record["evidence"] = {"status": "error", "error": str(error)}
            else:
                record["evidence"] = {"status": "not_provided"}
            from .reports import previews
            previews(output, record, audio, include_audio)
            if sha256(path) != record["audio_sha256"]:
                raise ValueError("源录音在检测期间发生变化，结果不能绑定原摘要")
            size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode())
            if result_bytes + size > 12 * 1024**2:
                raise ValueError("批次结构化结果超过 12 MiB 额度；前部结果已保留，请分批处理")
            result_bytes += size
        except (ValueError, OSError) as error:
            record = {k: record[k] for k in ("schema_version", "tool", "tool_version", "input", "audio_sha256") if k in record}
            record["error"] = str(error)
        except KeyboardInterrupt:
            record["error"] = "检测被中断；已完成录音的结果保留"
            interrupted = True
        records.append(record)
        with (output / "results.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        write_json(output / "run.json", {"tool": "gaps", "state": "running", "completed_files": len(records), "requested_files": len(paths)})
        if interrupted:
            break
    summary = {"schema_version": "1.0", "tool_version": __version__, "tool": "gaps",
        "created_at": datetime.now(timezone.utc).isoformat(), "files": len(records),
        "requested_files": len(paths), "interrupted": interrupted,
        "errors": sum("error" in r or r.get("evidence", {}).get("status") == "error" for r in records),
        "candidates": sum(r.get("result", {}).get("candidate_count", 0) for r in records),
        "clusters": sum(r.get("result", {}).get("cluster_count", 0) for r in records),
        "insufficient_evidence": sum(r.get("result", {}).get("status") == "INSUFFICIENT_EVIDENCE" for r in records)}
    from .reports import render
    render(output, records, summary, hide_paths)
    write_json(output / "run.json", summary)
    return summary
