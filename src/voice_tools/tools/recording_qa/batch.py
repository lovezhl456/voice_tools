from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil

from voice_tools import __version__
from voice_tools.audio.io import read_wav
from voice_tools.core.files import new_output, read_json, sha256, write_json
from .detector import Config, analyze
from .reports import render_report


def discover(inputs):
    paths = set()
    for raw in inputs:
        path = Path(raw)
        if not path.exists():
            raise ValueError(f"输入不存在：{path}")
        if path.is_dir():
            paths.update(p.resolve() for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".wav")
        elif path.suffix.lower() == ".wav":
            paths.add(path.resolve())
        else:
            raise ValueError(f"目前仅接收 PCM16 WAV：{path}")
    if not paths:
        raise ValueError("未找到 WAV 文件")
    return sorted(paths)


def load_evidence(path, audio_hash):
    record = {}
    event_path = path.with_suffix(".events.json")
    metadata = read_json(event_path) if event_path.exists() else {}
    if not isinstance(metadata, dict):
        raise ValueError("事件文件必须为 JSON 对象")
    provenance_path = path.with_suffix(".provenance.json")
    if provenance_path.exists():
        provenance = read_json(provenance_path)
        if not isinstance(provenance, dict) or provenance.get("output_sha256") != audio_hash:
            raise ValueError("格式准备溯源与录音摘要不一致")
        mapping = provenance.get("time_mapping")
        if not isinstance(mapping, dict) or type(mapping.get("verified")) is not bool:
            raise ValueError("格式准备 time_mapping 缺少布尔 verified 状态")
        record["preparation"] = provenance
        if not mapping["verified"] and metadata:
            from .review import timestamp
            alignment = metadata.get("alignment", {})
            if (not isinstance(alignment, dict) or alignment.get("audio_sha256") != audio_hash
                    or not isinstance(alignment.get("reviewer"), str) or not alignment["reviewer"].strip()):
                raise ValueError("转换时间映射未核实；事件需人工对齐并填写 alignment 的录音摘要、复核人与含时区时间")
            timestamp(alignment.get("reviewed_at"))
    if event_path.exists():
        record["events_sha256"] = sha256(event_path)
        record["events"] = metadata
    return metadata, record


def analyze_batch(inputs, output, config=None, include_audio=False, use_event_channel=True, hide_paths=False):
    config = config or Config()
    config.validate()
    paths = discover(inputs)
    output = new_output(output)
    records = []
    for path in paths:
        record = {"schema_version": "1.0", "tool": "recording_qa", "tool_version": __version__, "input": str(path)}
        try:
            record["audio_sha256"] = sha256(path)
            metadata, evidence = load_evidence(path, record["audio_sha256"])
            record.update(evidence)
            identity = record["audio_sha256"] + ":" + record.get("events_sha256", "none")
            record["sample_id"] = hashlib.sha256(identity.encode()).hexdigest()
            effective = replace(config, system_channel=metadata.get("system_channel", config.system_channel)) if use_event_channel else config
            audio = read_wav(path)
            record["result"] = analyze(audio, metadata, effective)
            if include_audio:
                target = output / "audio" / (record["audio_sha256"] + ".wav")
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    shutil.copyfile(path, target)
                record["audio_copy"] = str(target.relative_to(output))
            from .workbench import add_previews
            add_previews(output, record, audio, include_audio)
        except (ValueError, OSError) as error:
            record.pop("result", None)
            record["error"] = str(error)
        records.append(record)
    summary = {"schema_version": "1.0", "tool_version": __version__,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "files": len(records), "errors": sum("error" in record for record in records),
               "candidates": sum(record.get("result", {}).get("candidate_count", 0) for record in records)}
    render_report(output, records, summary, hide_paths)
    write_json(output / "run.json", summary)
    return summary
