"""Stream files and segments; preserve explicit per-channel evidence and failures."""
import csv
import math
from pathlib import Path
import time
import json

from voice_tools import __version__
from voice_tools.core.files import new_output, write_json
from .backend import SCORE_NAMES, Scorer, doctor
from . import weights

SUFFIXES = {".wav", ".flac"}


def collect_inputs(inputs):
    found = []
    for item in inputs:
        path = Path(item).expanduser().resolve()
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES))
        elif path.is_file() and path.suffix.lower() in SUFFIXES:
            found.append(path)
        else:
            raise ValueError(f"输入须为 WAV/FLAC 文件或存在的目录：{path}")
    found = list(dict.fromkeys(p.resolve() for p in found))
    if not found:
        raise ValueError("输入中没有 WAV/FLAC 文件")
    return found


def channels_for(count, selected):
    if count == 1:
        if selected not in (None, "left"):
            raise ValueError("单声道只可不指定 --channel 或选择 left；不能伪造右声道")
        return [(0, "mono")]
    if count != 2:
        raise ValueError(f"仅支持单声道或双声道，实际为 {count} 声道")
    if selected is None:
        raise ValueError("双声道须显式指定 --channel left/right/both；不自动混音或推断角色")
    return [(0, "left"), (1, "right")] if selected == "both" else [(0 if selected == "left" else 1, selected)]


def _evaluate_channel_segment(samples, rate, scorer, *, min_seconds, min_rms_dbfs):
    """Apply evidence gates and isolate scoring errors to this channel segment."""
    import numpy as np

    result = {"scores": None}
    try:
        if not np.isfinite(samples).all():
            raise ValueError("non_finite_audio")
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        result["rms_dbfs"] = 20 * math.log10(rms) if rms > 0 else None
        if len(samples) / rate < min_seconds:
            result.update(status="insufficient_evidence", reason="too_short")
        elif rms == 0 or result["rms_dbfs"] < min_rms_dbfs:
            result.update(status="insufficient_evidence", reason="silent_or_below_rms_gate")
        else:
            scores = scorer(samples, rate)
            if set(scores) != set(SCORE_NAMES) or not all(math.isfinite(float(v)) for v in scores.values()):
                raise ValueError("模型未返回五项有限分数")
            result.update(status="ok", reason=None, scores={k: float(v) for k, v in scores.items()})
    except Exception as error:
        result.update(status="error", reason=str(error)[:500], scores=None)
    return result


def analyze(inputs, output, model_dir=None, channel=None, segment_seconds=10.0,
            min_seconds=1.0, min_rms_dbfs=-60.0, threads=2, scorer_factory=Scorer):
    if channel not in (None, "left", "right", "both"):
        raise ValueError("声道只能选择 left/right/both")
    if not math.isfinite(segment_seconds) or not 1 <= segment_seconds <= 20:
        raise ValueError("--segment-seconds 须在 1～20 秒之间")
    if not math.isfinite(min_seconds) or not 0.5 <= min_seconds <= segment_seconds:
        raise ValueError("--min-seconds 须在 0.5 秒与分段时长之间")
    if not math.isfinite(min_rms_dbfs) or not -120 <= min_rms_dbfs <= 0:
        raise ValueError("--min-rms-dbfs 须在 -120～0 dBFS 之间")
    if not 1 <= threads <= 64:
        raise ValueError("--threads 须在 1～64 之间")
    files = collect_inputs(inputs)
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"输出目录必须不存在或为空：{output}")
    started = time.perf_counter()
    scorer = scorer_factory(model_dir, threads=threads)
    try:
        import soundfile as sf
        import numpy  # Check optional dependencies before creating any output, including empty audio.
    except ImportError as error:
        raise ValueError("请先安装 NISQA 可选依赖：python -m pip install -e '.[nisqa]'") from error
    load_seconds = time.perf_counter() - started
    output = new_output(output)
    summary = {"files": len(files), "records": 0, "scored": 0, "insufficient_evidence": 0,
               "errors": 0, "scored_audio_seconds": 0.0}
    fields = ["file", "channel", "segment_index", "start_seconds", "end_seconds", "sample_rate",
              "rms_dbfs", "status", "reason", *SCORE_NAMES]
    with (output / "results.jsonl").open("w", encoding="utf-8") as jsonl, (output / "results.csv").open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields)
        writer.writeheader()

        def emit(row):
            summary["records"] += 1
            status = row["status"]
            summary[{"ok": "scored", "insufficient_evidence": "insufficient_evidence", "error": "errors"}[status]] += 1
            if status == "ok":
                summary["scored_audio_seconds"] += row["end_seconds"] - row["start_seconds"]
            jsonl.write(json.dumps({"schema_version": "1.0", **row}, ensure_ascii=False, allow_nan=False) + "\n")
            jsonl.flush()
            scores = row.get("scores") or {}
            writer.writerow({key: row.get(key, scores.get(key)) for key in fields})

        for path in files:
            try:
                with sf.SoundFile(path) as audio:
                    selected = channels_for(audio.channels, channel)
                    rate = int(audio.samplerate)
                    if not 8000 <= rate <= 96000:
                        raise ValueError("仅支持 8～96 kHz 采样率；请确认录音格式和转换参数")
                    chunk_frames = max(1, int(segment_seconds * rate))
                    offset, index = 0, 0
                    if audio.frames == 0:
                        emit({"file": str(path), "status": "insufficient_evidence", "reason": "empty_audio", "scores": None})
                    while True:
                        block = audio.read(chunk_frames, dtype="float32", always_2d=True)
                        if not len(block):
                            break
                        for number, name in selected:
                            samples = block[:, number].copy()
                            row = {"file": str(path), "channel": name, "segment_index": index,
                                   "start_seconds": offset / rate, "end_seconds": (offset + len(block)) / rate,
                                   "sample_rate": rate, "scores": None}
                            row.update(_evaluate_channel_segment(
                                samples, rate, scorer,
                                min_seconds=min_seconds, min_rms_dbfs=min_rms_dbfs,
                            ))
                            emit(row)
                        offset += len(block)
                        index += 1
            except Exception as error:
                emit({"file": str(path), "status": "error", "reason": str(error)[:500], "scores": None})
    elapsed = time.perf_counter() - started
    summary.update(elapsed_seconds=elapsed, load_seconds=load_seconds,
                   processing_seconds=elapsed - load_seconds,
                   rtf_including_load=elapsed / summary["scored_audio_seconds"] if summary["scored_audio_seconds"] else None)
    exit_code = 3 if summary["errors"] else 1 if summary["insufficient_evidence"] else 0
    write_json(output / "run.json", {"schema_version": "1.0", "tool_version": __version__, "tool": "nisqa",
        "exit_code": exit_code, "summary": summary, "model": weights.describe(model_dir),
        "versions": doctor(model_dir)["versions"], "device": "cpu",
        "settings": {"channel": channel, "segment_seconds": segment_seconds, "min_seconds": min_seconds,
                     "min_rms_dbfs": min_rms_dbfs, "threads": threads},
        "limitations": ["RMS 门槛不是语音识别/VAD；噪声或音调也可能通过。", "分数是听感预测，不是人工 MOS 或故障根因。",
                        "原始响度与采样率保持不变；左右声道角色须由实际录制配置确认。"]})
    return summary, exit_code
