"""Register commands without importing torch, librosa or soundfile."""
from pathlib import Path


def register(commands):
    parser = commands.add_parser("nisqa", help="本地 CPU 听感评分、权重下载与环境检查")
    actions = parser.add_subparsers(dest="action", required=True)
    download = actions.add_parser("download", help="显式下载固定版本权重并校验 SHA-256；CC BY-NC-SA 4.0")
    download.add_argument("--force", action="store_true", help="重新下载并在验证通过后替换现有权重")
    doctor = actions.add_parser("doctor", help="离线检查依赖元数据和权重，不加载模型")
    analyze = actions.add_parser("analyze", help="离线递归处理 WAV/FLAC，保留逐声道分段评分")
    for cmd in (download, doctor, analyze):
        cmd.add_argument("--model-dir", type=Path, help="权重目录，默认 XDG_CACHE_HOME 或 ~/.cache 下 voice-tools/nisqa")
        cmd.set_defaults(run=run)
    analyze.add_argument("inputs", nargs="+", type=Path)
    analyze.add_argument("--out", required=True, type=Path, help="新的或空的输出目录")
    analyze.add_argument("--channel", choices=("left", "right", "both"), help="双声道必须显式选择；both 分开评分")
    analyze.add_argument("--segment-seconds", type=float, default=10.0, help="分段时长，1～20 秒")
    analyze.add_argument("--min-seconds", type=float, default=1.0, help="过短片段不打分，至少 0.5 秒")
    analyze.add_argument("--min-rms-dbfs", type=float, default=-60.0, help="低于此 RMS 门槛不打分；不是 VAD")
    analyze.add_argument("--provenance", action="store_true", help="额外保存源录音与结果摘要，不改变评分列")
    analyze.add_argument("--threads", type=int, default=2, help="PyTorch CPU 计算线程数，1～64")


def run(args):
    from voice_tools.core.command import artifacts, emit_result
    if args.action == "download":
        from .weights import download
        result = download(args.model_dir, args.force)
        return emit_result(args, result, f"权重校验通过：{result['path']}（CC BY-NC-SA 4.0）")
    if args.action == "doctor":
        from .backend import doctor
        result = doctor(args.model_dir)
        message = "依赖元数据及权重检查通过（未加载运行库/模型）" if result["ready"] else result["install_hint"]
        if not result["ready"]:
            message += f"\n依赖：{result['versions']}\n权重：{result['model'].get('error', '校验通过')}"
        return emit_result(args, result, message, exit_code=0 if result["ready"] else 1)
    from .service import analyze
    result, code = analyze(args.inputs, args.out, args.model_dir, args.channel, args.segment_seconds,
                           args.min_seconds, args.min_rms_dbfs, args.threads, provenance=args.provenance)
    return emit_result(args, result,
        f"NISQA：{result['scored']} 段已评分，{result['insufficient_evidence']} 段证据不足，{result['errors']} 项错误。结果：{args.out}",
        artifacts(args.out, "results.jsonl", "results.csv", "run.json", *(["provenance.json"] if args.provenance else [])), code)
