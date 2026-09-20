"""Offline timing commands; native calls remain under the SIP tool."""
from pathlib import Path


def register(commands):
    parser = commands.add_parser("benchmark", help="中文电话时序模板、媒体桥证据分析与批次汇总")
    actions = parser.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="生成五类中文场景与待补素材清单")
    init.add_argument("--out", type=Path, required=True)
    init.set_defaults(run=run)
    for name, parameter, help_text in (
            ("analyze", "run_dir", "从单次SIP媒体桥证据离线重算时序"),
            ("summarize", "batch_dir", "汇总批次，保留无效、失败与证据不足样本")):
        cmd = actions.add_parser(name, help=help_text)
        cmd.add_argument(parameter, type=Path)
        cmd.add_argument("--out", type=Path, required=True)
        cmd.set_defaults(run=run)


def run(args):
    from voice_tools.core.command import emit_result
    from voice_tools.core.files import new_output
    if args.action == "init":
        from .templates import initialize
        result = initialize(args.out)
        code = 0
    elif args.action == "analyze":
        from .analysis import save_analysis
        output = new_output(args.out)
        result = save_analysis(args.run_dir, output)
        code = {"completed": 0, "findings": 1}.get(result["status"], 3)
    else:
        from .summary import summarize
        output = new_output(args.out)
        result = summarize(args.batch_dir, output)
        code = 1 if result["counts"]["failed"] else 0
        if result["counts"]["insufficient_evidence"] or result["counts"]["invalid"]:
            code = 3
    return emit_result(args, result, str(result), {"output": args.out}, exit_code=code)
