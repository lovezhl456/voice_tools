from pathlib import Path


def register(commands):
    parser = commands.add_parser("audio", help="录音体检、格式准备与转换溯源")
    actions = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (("inspect", "查看格式与逐声道音频健康指标"), ("prepare", "转为保留声道的 PCM16 WAV")):
        cmd = actions.add_parser(action, help=help_text)
        cmd.add_argument("inputs", nargs="+", type=Path)
        cmd.add_argument("--out", required=True, type=Path)
        cmd.add_argument("--raw-format", choices=("s16le", "alaw", "mulaw"))
        cmd.add_argument("--raw-sample-rate", type=int)
        cmd.add_argument("--raw-channels", type=int, choices=(1, 2))
        if action == "prepare":
            cmd.add_argument("--sample-rate", type=int, choices=(8000, 16000, 32000, 48000), default=16000)
        else:
            cmd.add_argument("--threshold-db", type=float, default=-45)
        cmd.set_defaults(run=run)


def run(args):
    from .service import process
    fields = (args.raw_format, args.raw_sample_rate, args.raw_channels)
    if any(x is not None for x in fields) and not all(x is not None for x in fields):
        raise ValueError("裸音频格式、采样率、声道数须同时提供")
    raw = dict(zip(("format", "sample_rate", "channels"), fields)) if args.raw_format else None
    summary = process(args.inputs, args.out, args.action, raw=raw,
                      sample_rate=getattr(args, "sample_rate", 16000), threshold_db=getattr(args, "threshold_db", -45))
    from voice_tools.core.command import artifacts, emit_result
    return emit_result(args, summary, f"已处理 {summary['files']} 个录音，错误 {summary['errors']} 个。报告：{args.out / 'report.html'}",
                       artifacts(args.out, "results.jsonl", "run.json", "report.html"), 3 if summary["errors"] else 0)
