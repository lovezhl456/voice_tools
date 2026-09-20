"""严格准备成对输入，调用本地官方 CLI，保留结果和失败证据。"""
import csv
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import wave

import numpy as np

from voice_tools import __version__
from voice_tools.audio.io import read_wav
from voice_tools.core.files import new_output, read_json, sha256, write_json

UPSTREAM_COMMIT = "38d0b0163e441047d4429bf07ad09e5b9031d02c"
MODELS = {
    "speech": "lattice_tcditugenmeetpackhref_ls2_nl60_lr12_bs2048_learn.005_ep2400_train1_7_raw.tflite",
    "audio": "libsvm_nu_svr_model.txt",
}
LIMITATIONS = [
    "MOS-LQO 是全参考算法估计，不是人工 MOS，也不是故障根因。",
    "调用者须确认两份输入为同一段内容；程序不能验证语义配对。",
    "不自动重采样、混音或切片；保持模式、采样率和预处理口径一致。",
    "单个分数不构成业务阈值；应多样本比较并人工试听。",
]


def backend_info(directory=None, mode="speech"):
    root = Path(directory or os.environ.get("VOICE_TOOLS_VISQOL_DIR") or
                Path.home() / ".local/share/voice-tools/visqol/source").expanduser().resolve()
    binary = root / "bazel-bin/visqol"
    model = root / "model" / MODELS[mode]
    missing = []
    if not binary.is_file() or not os.access(binary, os.X_OK):
        missing.append("可执行文件 bazel-bin/visqol")
    if not model.is_file():
        missing.append("模型 model/" + MODELS[mode])
    manifest = root.parent / "installation.json"
    provenance = read_json(manifest) if manifest.is_file() else None
    return {"directory": str(root), "binary": str(binary), "model": str(model),
            "mode": mode, "ready": not missing, "missing": missing,
            "installation": provenance,
            "check_scope": "文件及执行权限检查；doctor 不代表已完成评分"}


def require_backend(directory, mode):
    info = backend_info(directory, mode)
    if not info["ready"]:
        raise ValueError("ViSQOL 后端未就绪：" + "、".join(info["missing"]) +
                         "；先运行安装脚本，并用 --visqol-dir 指定源码目录或设置 VOICE_TOOLS_VISQOL_DIR")
    info["binary_sha256"] = sha256(info["binary"])
    info["model_sha256"] = sha256(info["model"])
    return info


def read_pairs(path):
    """CSV 路径以 CSV 自身目录为基准，不依赖调用工作目录。"""
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("配对 CSV 超过 5 MiB；请拆分批次")
    pairs = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            if reader.fieldnames != ["reference", "degraded"]:
                raise ValueError("CSV 表头必须为 reference,degraded")
            for number, row in enumerate(reader, 2):
                if None in row or any(not row.get(k, "") or not row[k].strip() for k in ("reference", "degraded")):
                    raise ValueError(f"CSV 第 {number} 行须有且只有 reference,degraded 两项路径")
                pair = {}
                for key in ("reference", "degraded"):
                    value = Path(row[key]).expanduser()
                    pair[key] = value.resolve() if value.is_absolute() else (path.parent / value).resolve()
                pairs.append(pair)
                if len(pairs) > 10000:
                    raise ValueError("单批最多 10000 对音频，请拆分批次")
        except csv.Error as error:
            raise ValueError(f"无效配对 CSV：{error}") from error
    if not pairs:
        raise ValueError("配对 CSV 没有音频行")
    return pairs


def inspect_input(path, mode):
    expected_rate = 16000 if mode == "speech" else 48000
    try:
        with wave.open(str(path), "rb") as stream:
            rate, channels = stream.getframerate(), stream.getnchannels()
            if channels != 1 or stream.getsampwidth() != 2 or stream.getcomptype() != "NONE":
                raise ValueError("ViSQOL 入口仅接收单声道 PCM16 WAV；双方分轨须先按真实角色提取，不能混音")
            if rate != expected_rate:
                raise ValueError(f"{mode} 模式要求 {expected_rate} Hz，收到 {rate} Hz；请先显式转换")
            duration = stream.getnframes() / rate
            if not 1 <= duration <= 60:
                raise ValueError("本入口每份输入须为 1–60 秒；建议使用约 3–10 秒对应有声片段")
    except (wave.Error, EOFError) as error:
        raise ValueError("输入不是有效 PCM16 WAV") from error
    audio = read_wav(path, max_seconds=60)
    rms = float(np.sqrt(np.mean(np.square(audio.samples, dtype=np.float64))))
    if rms == 0:
        raise ValueError("音频完全静音，不输出具有误导性的 MOS-LQO")
    peak = float(np.max(np.abs(audio.samples)))
    warnings = []
    if duration < 3 or duration > 10:
        warnings.append("时长超出通常的 3–10 秒短片段范围，请核对适用性")
    if rms < 0.001:
        warnings.append("能量很低，请核对是否以静音或底噪为主")
    if peak >= 32767 / 32768:
        warnings.append("存在触顶样本，请核对削波；不据此认定损伤原因")
    return {"sample_rate": rate, "channels": channels, "sample_width_bytes": 2,
            "duration_s": duration, "rms": rms, "peak": peak, "sha256": sha256(path),
            "warnings": warnings}


def run_backend(argv, cwd, output, timeout):
    """二进制输出只进日志，保证顶层 --json 的 stdout 是单个对象。"""
    start = time.perf_counter()
    with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                   start_new_session=os.name == "posix")
        try:
            code = process.wait(timeout=timeout)
        except BaseException as error:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise ValueError(f"ViSQOL 未在 {timeout:g} 秒内完成；保留日志，不输出分数") from error
            raise
    elapsed = time.perf_counter() - start
    if code:
        raise ValueError(f"ViSQOL 退出码 {code}；请查看该音频对的 stderr.log / stdout.log")
    return elapsed


def evaluate_pair(pair, backend, output, mode, timeout):
    result = {"schema_version": "1.0", "status": "error", "moslqo": None, "mode": mode,
              "reference": str(Path(pair["reference"]).expanduser().resolve()),
              "degraded": str(Path(pair["degraded"]).expanduser().resolve()),
              "warnings": [], "artifacts_dir": str(output)}
    output.mkdir()
    try:
        snapshots = {}
        metadata = {}
        # 先限制输入大小，再复制快照，防止读取中途源文件改变造成不可追溯结果。
        for role in ("reference", "degraded"):
            original = Path(result[role])
            if not original.is_file():
                raise ValueError(f"{role} 文件不存在或不是普通文件：{original}")
            if original.stat().st_size > 16 * 1024 * 1024:
                raise ValueError(f"{role} 超过本入口 16 MiB 文件上限；请先切片")
            snapshot = output / f"{role}.wav"
            with original.open("rb") as stream:
                payload = stream.read(16 * 1024 * 1024 + 1)
            if len(payload) > 16 * 1024 * 1024:
                raise ValueError(f"{role} 读取期间超出 16 MiB 上限")
            snapshot.write_bytes(payload)
            metadata[role] = inspect_input(snapshot, mode)
            snapshots[role] = snapshot
        result["inputs"] = metadata
        for role, value in metadata.items():
            result["warnings"].extend(f"{role}: {warning}" for warning in value["warnings"])
        if abs(metadata["reference"]["duration_s"] - metadata["degraded"]["duration_s"]) > 0.5:
            result["warnings"].append("两份音频时长相差超过 0.5 秒，请核对是否同一片段；后端会尝试对齐")
        argv = [backend["binary"], "--reference_file", str(snapshots["reference"]),
                "--degraded_file", str(snapshots["degraded"]),
                "--similarity_to_quality_model", backend["model"],
                "--results_csv", str(output / "upstream.csv"),
                "--output_debug", str(output / "upstream.json"), "--verbose"]
        if mode == "speech":
            argv.append("--use_speech_mode")
        write_json(output / "invocation.json", {"argv": argv, "cwd": backend["directory"], "timeout_s": timeout})
        result["elapsed_s"] = run_backend(argv, backend["directory"], output, timeout)
        debug = read_json(output / "upstream.json")
        if not isinstance(debug, dict):
            raise ValueError("后端没有输出有效的单对 JSON 结果")
        score = debug.get("moslqo")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 1 <= score <= 5:
            raise ValueError("后端 MOS-LQO 缺失、非有限或不在 1–5 内，不采信该结果")
        for role in ("reference", "degraded"):
            if debug.get(role + "Filepath") != str(snapshots[role]):
                raise ValueError("后端 JSON 的输入路径与本次快照不一致，不采信该结果")
        result.update(status="completed", moslqo=score)
    except (ValueError, OSError) as error:
        result["error"] = {"code": "PAIR_FAILED", "message": str(error)}
    write_json(output / "result.json", result)
    return result


def process(pairs, output, *, directory=None, mode="speech", timeout=120):
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("timeout 必须大于 0 且不超过 3600 秒")
    backend = require_backend(directory, mode)
    output = new_output(Path(output).expanduser().resolve())
    started = time.perf_counter()
    rows = []
    (output / "pairs").mkdir()
    with (output / "results.jsonl").open("w", encoding="utf-8") as jsonl:
        for index, pair in enumerate(pairs, 1):
            item = evaluate_pair(pair, backend, output / "pairs" / f"{index:05d}", mode, timeout)
            item["pair_index"] = index
            rows.append(item)
            jsonl.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            jsonl.flush()
    scores = [row["moslqo"] for row in rows if row["status"] == "completed"]
    summary = {"pairs": len(rows), "completed": len(scores), "errors": len(rows) - len(scores),
               "mode": mode, "moslqo_mean": sum(scores) / len(scores) if scores else None,
               "aggregation_scope": "仅已完成音频对；部分失败时不能当成全批质量",
               "elapsed_s": time.perf_counter() - started}
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["pair_index", "reference", "degraded", "status", "moslqo", "error"])
        for index, row in enumerate(rows, 1):
            writer.writerow([index, row["reference"], row["degraded"], row["status"],
                             row["moslqo"] if row["moslqo"] is not None else "", row.get("error", {}).get("message", "")])
    write_json(output / "run.json", {"schema_version": "1.0", "tool_version": __version__,
                                   "summary": summary, "backend": backend, "limitations": LIMITATIONS})
    return summary
