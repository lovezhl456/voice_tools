#!/usr/bin/env python3
"""使用固定 ViSQOL 官方样本跑通 CLI；需已安装 voice_tools、ViSQOL 和 FFmpeg。"""
import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

from voice_tools.audio.io import read_wav, write_wav
from voice_tools.core.files import new_output, sha256, write_json
from voice_tools.tools.visqol.service import require_backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visqol-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="不存在或为空的本地演示目录")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    backend = require_backend(args.visqol_dir, "speech")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("演示转采样需要 FFmpeg；ViSQOL 评分本身不需要它")
    source = Path(backend["directory"]) / "testdata/clean_speech"
    originals = {"reference": source / "CA01_01.wav", "transcoded": source / "transcoded_CA01_01.wav"}
    if not all(p.is_file() for p in originals.values()):
        raise ValueError("后端目录缺少官方 clean_speech 演示样本")
    out = new_output(args.out.expanduser().resolve())
    inputs = out / "inputs"; inputs.mkdir()
    conversions = []
    for name, path in originals.items():
        dest = inputs / (name + "16.wav")
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                   "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dest)]
        subprocess.run(command, check=True, timeout=120, capture_output=True)
        conversions.append({"source": str(path), "source_sha256": sha256(path), "argv": command,
                            "output": str(dest), "output_sha256": sha256(dest)})
    audio = read_wav(inputs / "reference16.wav")
    noise = np.random.default_rng(20260920).normal(0, .08, audio.samples.shape)
    write_wav(inputs / "noise16.wav", audio.samples + noise, 16000)
    with (out / "pairs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream); writer.writerow(["reference", "degraded"])
        writer.writerow(["inputs/reference16.wav", "inputs/transcoded16.wav"])
        writer.writerow(["inputs/reference16.wav", "inputs/noise16.wav"])
    runs = []
    plans = [
        ("identity", ["score", "--reference", str(inputs / "reference16.wav"), "--degraded", str(inputs / "reference16.wav")]),
        ("speech-batch", ["batch", "--pairs", str(out / "pairs.csv")]),
        ("audio", ["score", "--mode", "audio", "--reference", str(originals["reference"]), "--degraded", str(originals["transcoded"])]),
    ]
    for name, flags in plans:
        command = [sys.executable, "-m", "voice_tools", "--json", "visqol", *flags,
                   "--visqol-dir", backend["directory"], "--out", str(out / name), "--timeout", str(args.timeout)]
        result = subprocess.run(command, capture_output=True, text=True)
        (out / (name + ".stdout.json")).write_text(result.stdout)
        (out / (name + ".stderr.log")).write_text(result.stderr)
        if result.returncode:
            raise ValueError(f"{name} 未完成，退出码 {result.returncode}；请查看 {out}")
        value = json.loads(result.stdout)
        runs.append({"name": name, "argv": command, "exit_code": result.returncode, "summary": value["summary"]})
    # 上游该固定提交 conformance.h 的 audio 基准值；不是人工听评分。
    expected = 1.7658378752958486
    observed = next(x["summary"]["moslqo_mean"] for x in runs if x["name"] == "audio")
    conformance = {"case": "kCA01_01AsAudio", "expected": expected, "observed": observed,
                   "tolerance": .0001, "passed": abs(observed - expected) <= .0001}
    if not conformance["passed"]:
        write_json(out / "conformance-failed.json", conformance)
        raise ValueError("官方音频基准超出上游容差，不能把运行结束当成正确验收")
    write_json(out / "demo.json", {"schema_version": "1.0", "backend": backend,
                                  "sample_duration_s": audio.duration_s, "conversions": conversions,
                                  "synthetic_noise": {"seed": 20260920, "standard_deviation": .08},
                                  "runs": runs, "upstream_conformance": conformance,
                                  "limitations": ["官方样本约 2.74 秒，只验收接入流程，不代表中文或生产通话质量。",
                                                   "noise16 是人工加噪的对照，不能冒充真实故障录音。",
                                                   "不要求默认 lattice 模型的同文件分数一定为 5。"]})
    print(json.dumps({"ok": True, "out": str(out), "runs": runs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"演示失败：{error}", file=sys.stderr)
        raise SystemExit(2)
