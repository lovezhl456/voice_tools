from datetime import datetime, timezone
import html
import json
from pathlib import Path

from voice_tools import __version__
from voice_tools.audio.formats import EXTENSIONS, decode, load_for_inspection, probe
from voice_tools.audio.health import inspect_health
from voice_tools.core.files import new_output, read_json, sha256, write_json


def discover(inputs):
    files = set()
    for value in inputs:
        path = Path(value)
        if not path.exists():
            raise ValueError(f"输入不存在：{path}")
        if path.is_dir():
            files.update(p.resolve() for p in path.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
        elif path.suffix.lower() in EXTENSIONS:
            files.add(path.resolve())
        else:
            raise ValueError(f"不支持的音频扩展名：{path.suffix}")
    if not files:
        raise ValueError("未找到支持的音频文件")
    return sorted(files)


def process(inputs, output, action, raw=None, sample_rate=16000, threshold_db=-45):
    if action not in ("inspect", "prepare"):
        raise ValueError("未知音频操作")
    paths = discover(inputs)
    output = new_output(output)
    records = []
    for index, path in enumerate(paths, 1):
        item = {"schema_version": "1.0", "tool": "audio", "tool_version": __version__, "input": str(path)}
        target = None
        try:
            item["input_sha256"] = sha256(path)
            if action == "inspect":
                audio, info = load_for_inspection(path, raw)
                item.update(info=info, health=inspect_health(audio, threshold_db))
            else:
                info = probe(path, raw)
                target = output / f"{index:04d}-{item['input_sha256'][:16]}.wav"
                audio, mapping = decode(path, target, sample_rate, raw, info)
                item.update(info=info, output=target.name, output_sha256=sha256(target),
                            output_sample_rate=sample_rate, output_channels=audio.samples.shape[1],
                            channel_map=list(range(info["channels"])), time_mapping=mapping, raw_input=raw)
                event_path = path.with_suffix(".events.json")
                if event_path.exists() and mapping["verified"]:
                    events = read_json(event_path)
                    if not isinstance(events, dict):
                        raise ValueError("事件文件必须为 JSON 对象")
                    # 身份/角色仍来自原始事件；准备操作不替用户验证角色。
                    write_json(target.with_suffix(".events.json"), events)
                    item["source_events_sha256"] = sha256(event_path)
                elif event_path.exists():
                    item["warning"] = "时间映射未核实，未复制事件文件；请人工对齐后再做事件级分析"
                write_json(target.with_suffix(".provenance.json"), item)
        except (ValueError, OSError) as error:
            if target is not None:
                for artifact in (target, target.with_suffix(".events.json"), target.with_suffix(".provenance.json")):
                    artifact.unlink(missing_ok=True)
            item["error"] = str(error)
        records.append(item)
    summary = {"schema_version": "1.0", "tool_version": __version__, "action": action,
               "created_at": datetime.now(timezone.utc).isoformat(), "files": len(records),
               "errors": sum("error" in r for r in records)}
    write_json(output / "run.json", summary)
    (output / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records), encoding="utf-8")
    sections = []
    for item in records:
        detail = json.dumps({k: v for k, v in item.items() if k not in ("input",)}, ensure_ascii=False, indent=2)
        sections.append(f"<section><h2>{html.escape(Path(item['input']).name)}</h2><pre>{html.escape(detail)}</pre></section>")
    (output / "report.html").write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>录音体检与准备</title><style>body{font:16px/1.6 system-ui;background:#f4f7fa;color:#172b40;margin:0}main{max-width:1000px;margin:auto;padding:20px}section{background:white;padding:20px;border-radius:12px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}h2{overflow-wrap:anywhere}</style><main><h1>录音体检与准备</h1>'''
                                          + f"<p>{summary['files']} 个文件 · {summary['errors']} 个错误</p><p>声道按原顺序保留。指标仅描述波形，不能自动确认角色、串音或通话成功。</p>"
                                          + "".join(sections) + "</main></html>", encoding="utf-8")
    return summary
