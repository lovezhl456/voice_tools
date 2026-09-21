"""仅注册参数；按命令延迟导入检测依赖。"""
from pathlib import Path
from voice_tools.core.command import artifacts, emit_result


def register(commands):
    parser = commands.add_parser("qa", help="双声道热线录音质检")
    actions = parser.add_subparsers(dest="action", required=True)
    from .assessment_cli import register as register_assessment
    register_assessment(actions)
    generate = actions.add_parser("generate", help="生成合成回归录音（不是黄金集）")
    generate.add_argument("--out", type=Path, required=True)
    generate.add_argument("--sample-rate", type=int, default=8000, choices=(8000, 16000, 32000, 48000))
    generate.add_argument("--seed", type=int, default=20260916)
    generate.set_defaults(run=run_generate)

    analyze = actions.add_parser("analyze", help="批量筛查 WAV，并导出报告和复核 CSV")
    analyze.add_argument("inputs", type=Path, nargs="+")
    analyze.add_argument("--out", type=Path, required=True)
    analyze.add_argument("--timeout", type=float, default=5, help="候选延迟阈值，秒；默认 5")
    analyze.add_argument("--threshold-db", type=float, default=-45)
    analyze.add_argument("--minimum-active", type=float, default=0.16)
    analyze.add_argument("--join-gap", type=float, default=0.3)
    analyze.add_argument("--system-channel", type=int, choices=(0, 1), help="0 左 / 1 右；默认读取事件文件，否则为 1")
    analyze.add_argument("--channels-verified", action="store_true", help="已通过受控通话核实声道角色")
    analyze.add_argument("--ai-start", type=float, help="已知 AI 接管时间；事件文件优先")
    analyze.add_argument("--backend", choices=("energy", "webrtcvad"), default="energy")
    analyze.add_argument("--include-audio", action="store_true", help="复制原录音到本地报告中供试听，注意报告包含音频")
    analyze.add_argument("--hide-paths", action="store_true", help="HTML 隐藏本机绝对路径；JSONL 仍保留完整溯源")
    analyze.add_argument("--fail-on-findings", action="store_true", help="发现候选时退出码为 1")
    analyze.set_defaults(run=run_analyze)

    promote = actions.add_parser("promote", help="将已人工填写的复核 CSV 晋升为黄金标签")
    promote.add_argument("review", type=Path)
    promote.add_argument("--results", type=Path, required=True)
    promote.add_argument("--out", type=Path, required=True)
    promote.add_argument("--dataset-kind", choices=("synthetic", "real"), required=True)
    promote.set_defaults(run=run_promote)

    evaluate = actions.add_parser("evaluate", help="在人工黄金标签上评估候选检出")
    evaluate.add_argument("golden", type=Path)
    evaluate.add_argument("--results", type=Path, required=True)
    evaluate.add_argument("--out", type=Path, required=True)
    evaluate.set_defaults(run=run_evaluate)

    freeze = actions.add_parser("freeze", help="冻结当前时间轴供人工核对（不会生成黄金标签）")
    freeze.add_argument("--results", type=Path, required=True)
    freeze.add_argument("--out", type=Path, required=True)
    freeze.set_defaults(run=run_freeze)

    compare = actions.add_parser("compare", help="在同一固定时间轴和黄金集上比较两个版本")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--golden", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    compare.set_defaults(run=run_compare)


def run_generate(args):
    from .scenarios import generate
    manifest = generate(args.out, args.sample_rate, args.seed)
    return emit_result(args, {"cases": len(manifest["cases"]), "dataset_kind": "synthetic"},
                       f"已生成 {len(manifest['cases'])} 个合成场景：{args.out}（未作为黄金集）", artifacts(args.out, "manifest.json"))


def run_analyze(args):
    from .batch import analyze_batch
    from .detector import Config
    config = Config(timeout_s=args.timeout, threshold_db=args.threshold_db,
                    minimum_s=args.minimum_active, join_gap_s=args.join_gap,
                    system_channel=1 if args.system_channel is None else args.system_channel,
                    channel_verified=args.channels_verified, ai_start_s=args.ai_start, backend=args.backend)
    summary = analyze_batch(args.inputs, args.out, config, args.include_audio, args.system_channel is None, args.hide_paths)
    code = 3 if summary["errors"] else 1 if args.fail_on_findings and summary["candidates"] else 0
    return emit_result(args, summary, f"已处理 {summary['files']} 个文件，候选 {summary['candidates']} 个，错误 {summary['errors']} 个。报告：{args.out / 'report.html'}",
                       artifacts(args.out, "results.jsonl", "run.json", "report.html", "review.html", "summary.csv", "review.csv"), code)


def run_promote(args):
    from .review import promote
    result = promote(args.review, args.results, args.out, args.dataset_kind)
    return emit_result(args, {"labels": len(result["labels"]), "dataset_kind": result["dataset_kind"]},
                       f"已保存 {len(result['labels'])} 条人工复核标签：{args.out}", {"golden": args.out})


def run_evaluate(args):
    from voice_tools.core.files import write_json
    from .review import evaluate, new_file
    result = evaluate(args.golden, args.results)
    write_json(new_file(args.out), result)
    return emit_result(args, result, f"已评估 {result['evaluated']} 条标签；precision={result['precision']}，recall={result['recall']}。{args.out}", {"metrics": args.out})


def run_freeze(args):
    from .dataset import freeze
    result = freeze(args.results, args.out)
    return emit_result(args, {"recordings": len(result["recordings"]), "notice": result["notice"]},
                       f"已冻结 {len(result['recordings'])} 个录音的时间轴，请人工核对事件后重新分析和标注：{args.out}", artifacts(args.out, "manifest.json"))


def run_compare(args):
    from .compare import compare
    result = compare(args.baseline, args.candidate, args.golden, args.out)
    return emit_result(args, {"changes": len(result["changes"]), "baseline": result["baseline"], "candidate": result["candidate"]},
                       f"对比完成，{len(result['changes'])} 个机会有变化：{args.out / 'report.html'}", artifacts(args.out, "report.html", "comparison.json"))
