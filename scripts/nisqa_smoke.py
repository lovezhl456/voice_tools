"""Exercise the installed CLI with explicitly supplied local speech; no downloads."""
import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="本地 NISQA 实战检查；请提供含说话的 WAV/FLAC，权重须先下载")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channel", choices=("left", "right", "both"))
    args = parser.parse_args()
    command = [sys.executable, "-m", "voice_tools", "--json", "nisqa", "analyze", str(args.audio), "--out", str(args.out)]
    if args.model_dir:
        command.extend(["--model-dir", str(args.model_dir)])
    if args.channel:
        command.extend(["--channel", args.channel])
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout, file=sys.stderr)
        return result.returncode or 2
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_mib = rss / (1024 ** 2 if sys.platform == "darwin" else 1024)
    summary = payload.get("summary", {})
    passed = result.returncode in (0, 1) and summary.get("scored", 0) > 0 and summary.get("errors", 0) == 0
    measurement = {"passed": passed, "scope": "real_model_execution_not_quality_accuracy",
                   "command": command, "wall_seconds_including_interpreter": time.perf_counter() - started,
                   "child_peak_rss_mib": peak_mib, "result": payload}
    print(json.dumps(measurement, ensure_ascii=False, allow_nan=False, indent=2))
    # Failure records stay with the CLI artifacts; never overwrite an unrelated path.
    report = args.out / "smoke.json"
    if "artifacts" in payload and payload["artifacts"] and report.parent.is_dir() and not report.exists():
        report.write_text(json.dumps(measurement, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else result.returncode or 3


if __name__ == "__main__":
    raise SystemExit(main())
