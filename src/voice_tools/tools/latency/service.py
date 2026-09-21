"""Serial file processing, explicit measurement evidence and portable artifacts."""
from collections import Counter
import json
import csv
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import wave

from voice_tools import __version__
from voice_tools.core.files import read_json, sha256, write_json
from .contract import FIXED_PARAMETERS, MAX_DECODED_BYTES, MAX_SECONDS, SAMPLE_RATES, SCHEMA_VERSION, validate_parameters
from .runtime import doctor, invoke, prefix


def inputs_in_order(inputs):
    paths = set()
    for item in inputs:
        item = Path(item).expanduser().resolve()
        if item.is_dir():
            found = {p.resolve() for p in item.rglob("*") if p.is_file() and p.suffix.lower() == ".wav"}
            paths.update(found or {item})
        else:
            paths.add(item)
    if not paths:
        raise ValueError("至少需要一份输入")
    return sorted(paths, key=str)


def inspect_wav(path):
    if path.suffix.lower() != ".wav":
        raise ValueError("仅支持 WAV；请先显式使用 audio prepare 转换")
    with wave.open(str(path), "rb") as reader:
        rate, frames = reader.getframerate(), reader.getnframes()
        if reader.getnchannels() != 2 or reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
            raise ValueError("仅支持双声道 PCM16 WAV；请显式保留声道转换")
        if rate not in SAMPLE_RATES:
            raise ValueError(f"不支持采样率 {rate}，允许 {SAMPLE_RATES}")
        if not 0 < frames / rate <= MAX_SECONDS:
            raise ValueError("录音须非空且不超过 3600 秒")
        if frames * 2 * 4 > MAX_DECODED_BYTES:
            raise ValueError("解码数组超过 512 MiB 限制（不是总内存承诺）")
        if frames * 4 > path.stat().st_size:
            raise ValueError("WAV 声明的帧数超过文件大小")
    return {"sample_rate": rate, "frames": frames, "duration_s": frames / rate}


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean_s": None, "median_s": None, "p95_s": None, "min_s": None, "max_s": None}
    def percentile(fraction):
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return {"count": len(ordered), "mean_s": sum(ordered) / len(ordered), "median_s": percentile(.5),
            "p95_s": percentile(.95), "min_s": ordered[0], "max_s": ordered[-1]}


def measurement(raw):
    detection = raw["detection"]
    segments = detection["segment_audit"]
    reasons = []
    if raw["silent"]:
        reasons.append("all_silence")
    if raw["identical_channels"]:
        reasons.append("identical_channels")
    pairs = []
    if not reasons:
        for direction, key, source_key, response_key, speaker in (
            ("human_to_ai", "latencies", "human_stop", "ai_start", "human"),
            ("ai_to_human", "human_response_latencies", "ai_stop", "human_start", "ai"),
        ):
            for row in detection[key]:
                source = next(s for s in segments if s["speaker"] == speaker and s["end"] == row[source_key])
                response = next(s for s in segments if s["speaker"] != speaker and s["start"] == row[response_key])
                pairs.append({"id": f"{direction}-{len(pairs) + 1}", "direction": direction,
                              "source_segment": source["id"], "response_segment": response["id"],
                              "source_end_s": row[source_key], "response_start_s": row[response_key], "latency_s": row["latency"]})
    if not pairs and not reasons:
        reasons.append("no_valid_pairs")
    eligible = [s for s in segments if s["speaker"] == "human" and s["eligible"]]
    numerator = sum(p["direction"] == "human_to_ai" for p in pairs)
    coverage = {"paired": numerator, "eligible_human_segments": len(eligible),
                "ratio": numerator / len(eligible) if eligible else None,
                "dispositions": dict(Counter(s["outgoing"] for s in eligible)),
                "definition": "pairing_coverage_not_response_success_rate"}
    return {"status": "measured" if pairs else "insufficient_evidence", "reasons": reasons,
            "coverage": coverage, "pairs": pairs, "segments": segments,
            "statistics": {direction: distribution([p["latency_s"] for p in pairs if p["direction"] == direction])
                           for direction in ("human_to_ai", "ai_to_human")}}


def analyze_file(path, folder, engine, parameters, sources, channel, timeout, include_audio, provenance):
    meta = inspect_wav(path)
    identity = sha256(path)
    with tempfile.TemporaryDirectory(prefix="latency-protocol-") as temporary:
        request = Path(temporary) / "request.json"
        response = Path(temporary) / "response.json"
        write_json(request, {"input": str(path), "system_channel": channel, "parameters": parameters})
        invoke(engine, [str(request), str(response)], timeout)
        raw = read_json(response)
    if raw.get("protocol_version") != SCHEMA_VERSION:
        raise ValueError("引擎响应协议不匹配")
    if sha256(path) != identity:
        raise ValueError("录音在分析过程中发生变化，结果未被接受")
    detail = {"kind": "latency_recording", "schema_version": SCHEMA_VERSION, "tool_version": __version__,
              "recording_id": identity, "input": str(path), "audio": {**meta, "sha256": identity, "pcm_sha256": raw["pcm_sha256"]},
              "system_channel": channel, "human_channel": "right" if channel == "left" else "left",
              "time_origin": "analyzed_recording_start", "parameters": parameters, "parameter_sources": sources,
              "fixed_parameters": FIXED_PARAMETERS, "provenance": provenance, "execution_status": "completed",
              "measurement": measurement(raw), "waveform": raw["waveform"], "resources": raw["resources"]}
    folder.mkdir()
    if include_audio:
        shutil.copyfile(path, folder / "audio.wav")
        if sha256(folder / "audio.wav") != identity:
            raise ValueError("试听副本与被分析录音摘要不匹配")
        detail["playback"] = "audio.wav"
    write_json(folder / "recording.json", detail)
    return detail


def summarize(rows, values, interrupted=False):
    eligible = sum(row.get("coverage", {}).get("eligible_human_segments", 0) for row in rows)
    paired = sum(row.get("coverage", {}).get("paired", 0) for row in rows)
    counts = Counter(row["execution_status"] for row in rows)
    dispositions = Counter()
    for row in rows:
        dispositions.update(row.get("coverage", {}).get("dispositions", {}))
    return {"files": len(rows), "completed": counts["completed"], "errors": counts["failed"],
            "execution_status": "interrupted" if interrupted else "partial_error" if counts["failed"] else "completed",
            "measurement_status": "measured" if any(values.values()) else "insufficient_evidence",
            "coverage": {"paired": paired, "eligible_human_segments": eligible, "ratio": paired / eligible if eligible else None,
                         "dispositions": dict(dispositions)},
            "statistics": {direction: distribution(numbers) for direction, numbers in values.items()}}


def write_run(output, run):
    temporary = output / "run.json.tmp"
    write_json(temporary, run)
    os.replace(temporary, output / "run.json")


def process(inputs, output, *, system_channel, directory=None, timeout=600, include_audio=False, parameters=None):
    if system_channel not in ("left", "right"):
        raise ValueError("system_channel 须显式为 left 或 right")
    if not math.isfinite(timeout) or not .1 <= timeout <= 86400:
        raise ValueError("timeout 范围为 0.1–86400 秒")
    values, sources = validate_parameters(parameters)
    input_roots = [Path(item).expanduser().resolve() for item in inputs if Path(item).expanduser().is_dir()]
    paths = inputs_in_order(inputs)
    info = doctor(directory)
    if not info["ready"]:
        raise ValueError("latency 引擎未就绪：" + "; ".join(info["issues"]))
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("输出目录已存在，禁止覆盖")
    if any(path.is_dir() and (output == path or path in output.parents) for path in input_roots):
        raise ValueError("结果目录不能位于输入目录内")
    output.mkdir(parents=True)
    (output / "recordings").mkdir()
    rows, seen = [], {}
    pooled = {"human_to_ai": [], "ai_to_human": []}
    run = {"kind": "latency_run", "schema_version": SCHEMA_VERSION, "tool_version": __version__,
           "requested_files": len(paths), "files_index": "files.jsonl", "turns": "turns.csv", "provenance": info["provenance"],
           "system_channel": system_channel, "parameters": values, "parameter_sources": sources,
           "fixed_parameters": FIXED_PARAMETERS, "file_timeout_s": timeout,
           "time_origin": "analyzed_recording_start", "include_audio": include_audio,
           "display_limits": {"recordings_per_page": 50, "turns_per_page": 50, "waveform_bins_per_track": 1600}}
    run.update(summarize(rows, pooled))
    run["execution_status"] = "running"
    write_run(output, run)
    interrupted = False
    previous_term = signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        raise KeyboardInterrupt("termination requested")
    signal.signal(signal.SIGTERM, terminate)
    try:
        with (output / "files.jsonl").open("w", encoding="utf-8") as index, (output / "turns.csv").open("w", newline="", encoding="utf-8") as csv_file:
            fields = ["file_index", "recording_id", "id", "direction", "source_segment", "response_segment", "source_end_s", "response_start_s", "latency_s"]
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            for number, path in enumerate(paths, 1):
                started = time.monotonic()
                row = {"file_index": number, "input": str(path), "execution_status": "failed", "measurement_status": "not_measured"}
                folder = output / "recordings" / f"{number:06d}"
                try:
                    detail = analyze_file(path, folder, prefix(directory), values, sources, system_channel, timeout, include_audio, info["provenance"])
                    measured = detail["measurement"]
                    row.update(recording_id=detail["recording_id"], pcm_sha256=detail["audio"]["pcm_sha256"],
                               execution_status="completed", measurement_status=measured["status"], reasons=measured["reasons"],
                               coverage=measured["coverage"], statistics=measured["statistics"], resources=detail["resources"],
                               detail=(folder / "recording.json").relative_to(output).as_posix())
                    if include_audio:
                        row["playback"] = (folder / "audio.wav").relative_to(output).as_posix()
                    duplicate = seen.setdefault(row["pcm_sha256"], number)
                    if duplicate != number:
                        row["duplicate_of_file_index"] = duplicate
                        row["warning"] = "identical_pcm_content_retained_in_statistics"
                    for pair in measured["pairs"]:
                        writer.writerow({"file_index": number, "recording_id": row["recording_id"], **pair})
                        pooled[pair["direction"]].append(pair["latency_s"])
                except KeyboardInterrupt:
                    interrupted = True
                    row.update(execution_status="interrupted", error={"code": "INTERRUPTED", "message": "已中断并清理引擎进程"})
                    raise
                except (OSError, ValueError, wave.Error, EOFError, subprocess.SubprocessError) as error:
                    row["error"] = {"code": "FILE_TIMEOUT" if isinstance(error, subprocess.TimeoutExpired) else "FILE_ERROR", "message": str(error)}
                finally:
                    row["wall_seconds"] = time.monotonic() - started
                    rows.append(row)
                    index.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"); index.flush(); csv_file.flush()
                    run.update(summarize(rows, pooled, interrupted))
                    write_run(output, run)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        from .report import render
        render(output, run, rows)
    return run
