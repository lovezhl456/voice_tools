"""CLI assembly never imports the external engine."""
from pathlib import Path
from .contract import PARAMETERS


def register(commands):
    parser = commands.add_parser("latency", help="隔离引擎分析双轨录音的双向逐轮延迟")
    actions = parser.add_subparsers(dest="action", required=True)
    doctor = actions.add_parser("doctor", help="离线检查安装完整性和可导入性，不代表录音测量通过")
    doctor.add_argument("--latency-dir", type=Path, help="执行端引擎前缀；优先于 VOICE_TOOLS_LATENCY_DIR 和默认目录")
    doctor.set_defaults(run=run_doctor)
    for action in ("analyze", "batch"):
        cmd = actions.add_parser(action, help="分析一份 WAV" if action == "analyze" else "串行处理文件或递归目录中的 WAV")
        if action == "analyze":
            cmd.add_argument("wav", type=Path, help="双声道 PCM16 WAV")
        else:
            cmd.add_argument("inputs", type=Path, nargs="+", help="WAV 文件或目录；规范化路径去重排序")
        cmd.add_argument("--system-channel", required=True, choices=("left", "right"), help="AI/系统所在声道；另一轨为人声")
        cmd.add_argument("--out", required=True, type=Path, help="新结果目录，禁止覆盖")
        cmd.add_argument("--latency-dir", type=Path, help="执行端引擎前缀（不随任务包收集）")
        cmd.add_argument("--timeout", type=float, default=600, help="每文件引擎超时秒数，0.1–86400；外层步骤超时也有效")
        cmd.add_argument("--include-audio", action="store_true", help="将音频复制进独立报告；任务包原本携带的输入不受此项控制")
        for name, rule in PARAMETERS.items():
            cmd.add_argument("--" + name.replace("_", "-"), type=int if "multiple_of" in rule else float,
                             help=f"单位 {rule['unit']}；默认 {rule['default']}；范围 {rule['min']}–{rule['max']}" +
                                  ("；10ms 整数倍" if "multiple_of" in rule else ""))
        cmd.set_defaults(run=run_analysis)


def run_doctor(args):
    from .runtime import doctor
    from voice_tools.core.command import emit_result
    info = doctor(args.latency_dir)
    if not info["ready"]:
        raise ValueError("latency 引擎未就绪：" + "; ".join(info["issues"]) + "；参阅 docs/latency-install.md")
    return emit_result(args, info, "latency 安装检查通过；未执行录音测量。")


def run_analysis(args):
    from .service import process
    from voice_tools.core.command import emit_result, artifacts
    inputs = [args.wav] if args.action == "analyze" else args.inputs
    result = process(inputs, args.out, system_channel=args.system_channel, directory=args.latency_dir,
                     timeout=args.timeout, include_audio=args.include_audio,
                     parameters={name: getattr(args, name) for name in PARAMETERS})
    return emit_result(args, result, f"已处理 {result['files']} 份录音，失败 {result['errors']}；测量状态请查看 {args.out}/index.html",
                       artifacts(args.out, "run.json", "files.jsonl", "turns.csv", "index.html"), 3 if result["errors"] else 0)
