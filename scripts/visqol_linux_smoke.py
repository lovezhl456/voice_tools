#!/usr/bin/env python3
"""在 Linux 上复验 ViSQOL：官方演示、真实部分失败、原生进程耗时和 RSS。"""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

import numpy as np

from voice_tools import __version__
from voice_tools.core.files import new_output, read_json, write_json
from voice_tools.tools.visqol.service import require_backend


def invoke(argv, directory, name, timeout, expected=0, cwd=None):
    write_json(directory / (name + ".command.json"), {"argv": argv, "cwd": str(cwd) if cwd else None})
    with (directory / (name + ".stdout.log")).open("wb") as stdout, \
            (directory / (name + ".stderr.log")).open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
    if code != expected:
        raise ValueError(f"{name} 退出 {code}，预期 {expected}；查看保留日志")


def checked_score(path, expected):
    score = read_json(path).get("moslqo")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) \
            or abs(score - expected) > .0001:
        raise ValueError(f"{path.name} 与固定上游基准不符：{score}，预期 {expected}")
    return score


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visqol-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=600, help="每条验证命令的超时秒数")
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or not Path("/usr/bin/time").is_file():
        raise ValueError("此脚本需要 Linux 与 GNU /usr/bin/time；普通评分不需要 time")
    if not 0 < args.timeout <= 3600:
        raise ValueError("timeout 须大于 0 且不超过 3600")
    backend = require_backend(args.visqol_dir, "speech")
    root = Path(backend["directory"])
    out = new_output(args.out.expanduser().resolve())
    demo_script = Path(__file__).with_name("visqol_demo.py")
    invoke([sys.executable, str(demo_script), "--visqol-dir", str(root),
            "--out", str(out / "demo"), "--timeout", str(args.timeout)], out, "demo", args.timeout * 4)
    demo = read_json(out / "demo/demo.json")
    # 上游 conformance 使用原始 48 kHz 样本；不同于适配层的 16 kHz 业务输入。
    baselines = []
    for name, mode, degraded_name, expected in (
            ("speech-transcoded", "speech", "transcoded_CA01_01.wav", 3.3129234313964844),
            ("speech-identity", "speech", "CA01_01.wav", 4.505550384521484),
            ("audio-transcoded", "audio", "transcoded_CA01_01.wav", 1.7658378752958486)):
        info = require_backend(root, mode)
        debug = out / ("baseline-" + name + ".json")
        command = [info["binary"], "--reference_file", str(root / "testdata/clean_speech/CA01_01.wav"),
                   "--degraded_file", str(root / "testdata/clean_speech" / degraded_name),
                   "--similarity_to_quality_model", info["model"], "--output_debug", str(debug)]
        if mode == "speech":
            command.append("--use_speech_mode")
        invoke(command, out, "baseline-" + name, args.timeout, cwd=root)
        observed = checked_score(debug, expected)
        baselines.append({"case": name, "expected": expected, "observed": observed,
                          "tolerance": .0001, "passed": True, "source_sample_rate": 48000})
    measurements = []
    for mode in ("speech", "audio"):
        info = require_backend(root, mode)
        if mode == "speech":
            reference = out / "demo/inputs/reference16.wav"
            degraded = out / "demo/inputs/transcoded16.wav"
            expected = read_json(out / "demo/speech-batch/pairs/00001/result.json")["moslqo"]
        else:
            reference = root / "testdata/clean_speech/CA01_01.wav"
            degraded = root / "testdata/clean_speech/transcoded_CA01_01.wav"
            expected = demo["upstream_conformance"]["expected"]
        for repetition in range(1, 4):
            name = f"native-{mode}-{repetition}"
            metrics = out / (name + ".time.json")
            debug = out / (name + ".score.json")
            command = ["/usr/bin/time", "-o", str(metrics), "-f",
                       '{"elapsed_s":%e,"peak_rss_kib":%M,"exit_code":%x}',
                       info["binary"], "--reference_file", str(reference),
                       "--degraded_file", str(degraded), "--similarity_to_quality_model", info["model"],
                       "--output_debug", str(debug)]
            if mode == "speech":
                command.append("--use_speech_mode")
            invoke(command, out, name, args.timeout, cwd=root)
            timing = read_json(metrics)
            score = checked_score(debug, expected)
            if timing["exit_code"]:
                raise ValueError(f"{name} 未通过分数与状态校验")
            measurements.append({"mode": mode, "repetition": repetition, "moslqo": score, **timing})
    pairs = out / "mixed.csv"
    with pairs.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["reference", "degraded"])
        writer.writerow(["demo/inputs/reference16.wav", "missing.wav"])
        writer.writerow(["demo/inputs/reference16.wav", "demo/inputs/transcoded16.wav"])
    invoke([sys.executable, "-m", "voice_tools", "--json", "visqol", "batch",
            "--pairs", str(pairs), "--out", str(out / "mixed"), "--visqol-dir", str(root),
            "--timeout", str(args.timeout)], out, "mixed", args.timeout * 2, expected=3)
    envelope = read_json(out / "mixed.stdout.log")
    rows = [json.loads(line) for line in (out / "mixed/results.jsonl").read_text().splitlines()]
    if envelope["summary"]["completed"] != 1 or envelope["summary"]["errors"] != 1 \
            or rows[0]["moslqo"] is not None or rows[1]["status"] != "completed":
        raise ValueError("真实部分失败处理未通过")
    record = {"schema_version": "1.0", "tool_version": __version__, "status": "passed",
              "platform": {"system": platform.system(), "machine": platform.machine(),
                           "kernel": platform.release(), "python": platform.python_version(), "numpy": np.__version__},
              "backend": backend, "demo": demo, "mixed_summary": envelope["summary"],
              "upstream_short_baselines": baselines,
              "measurements": measurements,
              "measurement_scope": "新建原生子进程，系统文件缓存已预热；GNU time RSS 单位 KiB，不含 Python 调用层。",
              "limitations": ["是否跨架构仿真须由宿主机单独记录，容器内 uname 不能证明原生运行。",
                              "官方短样本验证接入，不代表生产音频质量或容量。"]}
    write_json(out / "validation.json", record)
    print(json.dumps({"status": "passed", "out": str(out), "measurements": measurements}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Linux 验证未完成：{error}", file=sys.stderr)
        raise SystemExit(2)
