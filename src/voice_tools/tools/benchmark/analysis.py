"""Recompute acoustic timing from saved PCM and a verified callback timeline."""
import json
import math
import wave
from pathlib import Path

import numpy as np

from voice_tools.audio.activity import detect_activity
from voice_tools.audio.io import Audio, read_wav
from voice_tools.core.files import read_json, sha256, write_json
from .config import configuration


def read_frames(path):
    if path.stat().st_size > 64 * 1024**2:
        raise ValueError("媒体帧记录超过64 MiB")
    frames = {"rx": [], "tx": []}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("direction") not in frames:
                raise ValueError("媒体帧方向无效")
            for key in ("sample_start", "samples", "sequence", "source_sample", "epoch"):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise ValueError(f"媒体帧 {key} 无效")
            at = row.get("at_s")
            if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or not 0 <= at <= 1100:
                raise ValueError("媒体帧时间无效")
            if not 0 < row["samples"] <= 8000:
                raise ValueError("媒体帧样本数无效")
            frames[row["direction"]].append(row)
            if sum(map(len, frames.values())) > 220000:
                raise ValueError("媒体帧数量超限")
    return frames


def verify_frames(frames, count):
    offset = 0
    previous = None
    for row in frames:
        if row["sample_start"] != offset or row["source_sample"] != offset:
            raise ValueError("媒体帧样本位置不连续")
        if previous is not None:
            if row["sequence"] != previous["sequence"] + 1 or row["at_s"] < previous["at_s"]:
                raise ValueError("媒体帧次序或时间不连续")
            if row["epoch"] != previous["epoch"]:
                raise ValueError("媒体帧跨越媒体重建，无法确认连续时间轴")
            same_source = (row.get("playback", {}).get("step_index") ==
                           previous.get("playback", {}).get("step_index"))
            if same_source and row["at_s"] - previous["at_s"] > max(.08, 3 * previous["samples"] / 8000):
                raise ValueError("媒体帧时钟存在未覆盖的间断")
        elif row["sequence"] != 0:
            raise ValueError("媒体帧开头缺失")
        offset += row["samples"]
        previous = row
    if offset != count:
        raise ValueError("媒体帧记录与PCM长度不一致")


def mapped_time(frames, sample, end=False):
    starts = [r["sample_start"] for r in frames]
    index = int(np.searchsorted(starts, sample, side="left" if end else "right")) - 1
    row = frames[max(0, index)]
    return row["at_s"] + (sample - row["sample_start"]) / 8000


def activity_spans(audio, frames, config, plan, direction):
    if not frames:
        return []
    # Source callbacks carry step/sample provenance. Never join two playback steps.
    groups = []
    for row in frames:
        step = row.get("playback", {}).get("step_index") if direction == "tx" else None
        if not groups or groups[-1][0] != step:
            groups.append((step, []))
        groups[-1][1].append(row)
    result = []
    for step, group in groups:
        start = group[0]["sample_start"]
        end = group[-1]["sample_start"] + group[-1]["samples"]
        subset = Audio(audio.samples[start:end], 8000)
        annotations = None
        if direction == "tx" and type(step) is int and 0 <= step < len(plan.get("steps", [])):
            annotations = plan["steps"][step].get("speech")
        if annotations is not None:
            active = [(r["start_s"], r["end_s"]) for r in annotations]
            for frame in group:
                playback = frame.get("playback", {})
                if playback.get("sample_start") != frame["sample_start"] - start:
                    raise ValueError("人工标注与发送素材位置不一致")
            if any(b > subset.duration_s + 1 / 8000 for _, b in active):
                raise ValueError("人工标注超出实际发送的素材范围")
        else:
            detected, _ = detect_activity(subset, threshold_db=config["threshold_db"],
                minimum_s=config["minimum_ms"] / 1000, gap_s=config["join_gap_ms"] / 1000,
                backend=config["backend"])
            active = detected[0]
        for a, b in active:
            begin_sample, end_sample = start + round(a * 8000), start + round(b * 8000)
            result.append({"start_s": mapped_time(group, begin_sample), "end_s": mapped_time(group, end_sample, True),
                           "audio_start_s": begin_sample / 8000, "audio_end_s": end_sample / 8000,
                           "step_index": step, "annotation": "human" if annotations is not None else "activity_detector"})
    return result


def row(identifier, kind, value=None, status="measured", reason=None, at=None, end=None, **extra):
    result = {"id": identifier, "kind": kind, "status": status,
              "value_ms": round(value, 2) if value is not None else None,
              "at_s": at, "end_s": end, "verdict": "not_configured", **extra}
    if reason:
        result["reason"] = reason
    return result


def silence_metrics(spans, start, end):
    intervals = sorted((max(start, s["start_s"]), min(end, s["end_s"])) for s in spans
                       if s["end_s"] > start and s["start_s"] < end)
    cursor, quiet = start, []
    for a, b in intervals:
        if a > cursor:
            quiet.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        quiet.append((cursor, end))
    longest = max(quiet, key=lambda x: x[1] - x[0], default=(start, start))
    return (longest[1] - longest[0]) * 1000, sum(b - a for a, b in quiet) * 1000, longest


def before_shutdown(spans, end):
    """Teardown silence cannot count as a successful response to an interruption."""
    clipped = []
    for span in spans:
        if span["start_s"] >= end:
            continue
        item = dict(span)
        if item["end_s"] > end:
            item["audio_end_s"] -= item["end_s"] - end
            item["end_s"] = end
        clipped.append(item)
    return clipped


def metrics(rx, tx, begin, observed_start, observed_end, config):
    """Pure temporal rules. A first confirmed quiet interval ends the old acoustic segment."""
    rows = []
    first = next((s for s in rx if s["end_s"] > begin), None)
    if first:
        at = max(begin, first["start_s"])
        rows.append(row("opening", "first_audio", (at - begin) * 1000, at=at))
    else:
        rows.append(row("opening", "first_audio", status="insufficient_evidence", reason="观察窗口内没有检测到接收声音"))
    quiet_s = config["detector"]["stop_silence_ms"] / 1000
    for index, utterance in enumerate(tx):
        start, end = utterance["start_s"], utterance["end_s"]
        overlap = next((s for s in rx if s["start_s"] <= start < s["end_s"]), None)
        prefix = f"utterance-{index + 1}"
        measure_interrupt = overlap is not None or any(key in config["expectations"] for key in ("expect_interrupt", "stop_max_ms"))
        if overlap is None and measure_interrupt:
            rows.append(row(prefix + "-stop", "barge_stop", status="invalid", reason="用户开始时没有接收声音重叠", at=start))
        elif overlap is not None:
            position = rx.index(overlap)
            stop = None
            for i in range(position, len(rx)):
                candidate = rx[i]["end_s"]
                next_start = rx[i + 1]["start_s"] if i + 1 < len(rx) else observed_end
                if next_start - candidate >= quiet_s - 1e-6:
                    stop = candidate
                    break
            if stop is None:
                rows.append(row(prefix + "-stop", "barge_stop", status="insufficient_evidence",
                    reason="未观察到足够长的停声；只提供观察下界", at=start, end=observed_end,
                    lower_bound_ms=round(max(0, rx[-1]["end_s"] - start) * 1000, 2)))
            else:
                resumed = next((s["start_s"] for s in rx if s["start_s"] > stop), None)
                rows.append(row(prefix + "-stop", "barge_stop", (stop - start) * 1000, at=start, end=stop,
                                overlapping=True, resumed_at_s=resumed))
        # A continuing old sound is not a new answer. Only a new acoustic onset qualifies.
        following = next((s for s in rx if s["start_s"] >= end), None)
        if following:
            rows.append(row(prefix + "-response", "response", (following["start_s"] - end) * 1000,
                            at=following["start_s"], end=end))
        else:
            rows.append(row(prefix + "-response", "response", status="insufficient_evidence",
                            reason="用户语音结束后未观察到可分离的新声音起点", at=end))
    windows = config["windows"] or [{"id": "observed", "start_s": max(begin, observed_start), "end_s": observed_end}]
    for window in windows:
        a, b = window["start_s"], window["end_s"]
        if a < observed_start - .02 or b > observed_end + .001 or b <= a:
            rows.append(row(window["id"] + "-silence", "longest_silence", status="insufficient_evidence",
                            reason="测量窗口超出接收证据", at=a, end=b))
            continue
        longest, total, segment = silence_metrics(rx, a, b)
        rows.append(row(window["id"] + "-silence", "longest_silence", longest, at=segment[0], end=segment[1],
                        total_silence_ms=round(total, 2), window_start_s=a, window_end_s=b))
    # A configured measurement must not silently pass when there is no observed TX utterance.
    required = {"response": "response_max_ms", "barge_stop": "stop_max_ms"}
    for kind, key in required.items():
        needed = key in config["expectations"] or (kind == "barge_stop" and config["expectations"].get("expect_interrupt") is True)
        if needed and not any(item["kind"] == kind for item in rows):
            rows.append(row("missing-" + kind, kind, status="insufficient_evidence", reason="没有可用的用户语音证据"))
    return rows


def evaluate_expectations(rows, expectations):
    limits = {"first_audio": "first_audio_max_ms", "response": "response_max_ms", "barge_stop": "stop_max_ms"}
    for item in rows:
        key = limits.get(item["kind"])
        limit = expectations.get(key)
        if item["kind"] == "barge_stop" and expectations.get("expect_interrupt") is False:
            # A normal pause cannot prove a false interruption; leave intent for human review.
            item["review_required"] = True
            continue
        requires_stop = item["kind"] == "barge_stop" and expectations.get("expect_interrupt") is True
        if limit is None and not requires_stop:
            continue
        if limit is not None:
            item["expected_max_ms"] = limit
        if item["status"] == "measured":
            item["verdict"] = "passed" if limit is None or item["value_ms"] <= limit else "failed"
        elif limit is not None and item.get("lower_bound_ms", -1) > limit:
            item["verdict"] = "failed"
        else:
            item["verdict"] = "insufficient_evidence"


def analyze(run_dir):
    root = Path(run_dir).resolve()
    plan = read_json(root / "plan.json")
    config = configuration(plan.get("benchmark", {}))
    result = read_json(root / "result.json")
    report = {"schema_version": "1.0", "kind": "voice_benchmark", "case_id": config["case_id"],
              "language": config["language"], "tags": config["tags"], "configuration": config,
              "execution_status": result.get("execution_status", result.get("status")),
              "status": "insufficient_evidence", "metrics": [], "activity": {"rx": [], "tx": []},
              "issues": [], "source_directory": str(root), "evidence": {},
              "observation_point": "local_pjsua_media_bridge", "resolution_ms": 20,
              "semantic_evaluation": "not_evaluated", "notes": [
                  "声音片段不等于语义轮次；停声可能是自然停顿，需要结合受控用例人工复查。",
                  "接收PCM在抖动缓冲之后；发送PCM是在本机送入媒体桥，不证明远端已收到。"]}
    try:
        observation = read_json(root / "media-observation.json")
        if (observation.get("schema_version") != "1.0" or observation.get("sample_rate") != 8000
                or observation.get("timebase") != "run_monotonic"):
            raise ValueError("不支持的媒体观测格式或时基")
        report["issues"].extend(observation.get("issues", []))
        if not observation.get("complete") or observation.get("dropped_frames"):
            report["issues"].append("媒体观测未完成或丢帧")
        frames = read_frames(root / "media-frames.jsonl")
        for direction in ("rx", "tx"):
            path = root / f"bridge_{direction}.wav"
            with wave.open(str(path)) as wav:
                count = wav.getnframes()
                if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (8000, 1, 2):
                    raise ValueError("观测音频须为单声道8kHz PCM16")
            verify_frames(frames[direction], count)
            if observation.get("samples", {}).get(direction) != count:
                raise ValueError("观测清单与PCM长度不一致")
            if count:
                audio = read_wav(path, max_seconds=1100)
                report["activity"][direction] = activity_spans(audio, frames[direction], config["detector"], plan, direction)
            report["evidence"][path.name] = sha256(path)
        if not frames["rx"]:
            raise ValueError("缺少接收媒体帧")
        events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
        beginnings = [e["at_s"] for e in events if e.get("event") == "strategy_start"]
        if not beginnings:
            raise ValueError("未观察到接通且媒体可用的时刻")
        begin = beginnings[0]
        rx = frames["rx"]
        if rx[0]["at_s"] > begin + .04:
            report["issues"].append("接通后的接收媒体开头缺失")
        end = rx[-1]["at_s"] + rx[-1]["samples"] / 8000
        boundaries = [e["at_s"] for e in events if
                      (e.get("event") == "step_start" and e.get("action") == "hangup") or
                      (e.get("event") == "call_state" and e.get("state") == "DISCONNECTED")]
        if isinstance(result.get("duration_s"), (int, float)):
            boundaries.append(result["duration_s"])
        if boundaries:
            end = min(end, *boundaries)
        for direction in ("rx", "tx"):
            report["activity"][direction] = before_shutdown(report["activity"][direction], end)
        report["observed_start_s"], report["observed_end_s"] = rx[0]["at_s"], end
        report["early_media"] = [s for s in report["activity"]["rx"] if s["start_s"] < begin]
        if not report["issues"]:
            report["metrics"] = metrics(report["activity"]["rx"], report["activity"]["tx"], begin, rx[0]["at_s"], end, config)
            for item in report["metrics"]:
                for field, audio_field in (("at_s", "audio_at_s"), ("end_s", "audio_end_at_s"), ("resumed_at_s", "resumed_audio_at_s")):
                    if item.get(field) is None:
                        continue
                    # Player seeks use concatenated PCM offsets, never wall-clock offsets.
                    candidates = [f for f in rx if f["at_s"] <= item[field]]
                    anchor = candidates[-1] if candidates else rx[0]
                    item[audio_field] = max(0, anchor["sample_start"] / 8000 + item[field] - anchor["at_s"])
                    item["audio_file"] = "bridge_rx.wav"
            evaluate_expectations(report["metrics"], config["expectations"])
            report["status"] = "findings" if any(m["verdict"] == "failed" for m in report["metrics"]) else "completed"
            if any(m["status"] == "insufficient_evidence" or m["verdict"] == "insufficient_evidence" for m in report["metrics"]):
                report["status"] = "insufficient_evidence" if report["status"] == "completed" else report["status"]
        # result.json receives benchmark assertion summaries later; exclude that cyclic hash.
        for name in ("plan.json", "events.jsonl", "media-observation.json", "media-frames.jsonl"):
            report["evidence"][name] = sha256(root / name)
    except (OSError, ValueError, KeyError, TypeError, EOFError, wave.Error) as exc:
        report["issues"].append(str(exc))
        report["metrics"] = []
    return report


def save_analysis(run_dir, output):
    report = analyze(run_dir)
    write_json(Path(output) / "benchmark.json", report)
    return report


def attach_assertions(report, result):
    """Configured acoustic thresholds use SIP's existing assertion failure behavior."""
    items = result["assertions"]["items"]
    configured = report["configuration"]["expectations"]
    evaluated = [m for m in report["metrics"] if m["verdict"] != "not_configured"]
    for metric in evaluated:
        items.append({"id": "benchmark:" + metric["id"], "type": "acoustic_timing", "status": metric["verdict"],
                      "expected": configured, "actual": metric, "evidence": ["benchmark.json", "media-frames.jsonl"],
                      "reason": metric.get("reason", "声学时序判定，不包含语义评价")})
    acoustic_expectations = set(configured) - {"expect_interrupt"}
    if not evaluated and (acoustic_expectations or configured.get("expect_interrupt") is True):
        items.append({"id": "benchmark:evidence", "type": "acoustic_timing", "status": "insufficient_evidence",
                      "expected": configured, "actual": None, "evidence": ["benchmark.json"], "reason": "缺少可判定的时序证据"})
    states = {item["status"] for item in items}
    status = "not_configured"
    if items:
        status = "failed" if "failed" in states else "insufficient_evidence" if "insufficient_evidence" in states else "passed"
    result["assertions"]["status"] = status
    result["assertions"]["counts"] = {s: sum(i["status"] == s for i in items) for s in ("passed", "failed", "insufficient_evidence")}
    if result["status"] == "completed" and status in ("failed", "insufficient_evidence"):
        result["status"] = "failed"
        result["error"] = {"code": "ASSERTIONS_NOT_PASSED", "message": "声学时序断言失败或证据不足；检查 benchmark.json"}
    result["benchmark"] = {"file": "benchmark.json", "status": report["status"], "issues": report["issues"]}
