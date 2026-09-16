"""仅注册参数；按命令延迟导入检测依赖。"""
from pathlib import Path


def register(commands):
    parser = commands.add_parser("qa", help="双声道热线录音质检")
    actions = parser.add_subparsers(dest="action", required=True)
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


def run_generate(args):
    from .scenarios import generate
    manifest = generate(args.out, args.sample_rate, args.seed)
    print(f"已生成 {len(manifest['cases'])} 个合成场景：{args.out}（未作为黄金集）")


def run_analyze(args):
    from .batch import analyze_batch
    from .detector import Config
    config = Config(timeout_s=args.timeout, threshold_db=args.threshold_db,
                    minimum_s=args.minimum_active, join_gap_s=args.join_gap,
                    system_channel=1 if args.system_channel is None else args.system_channel,
                    channel_verified=args.channels_verified, ai_start_s=args.ai_start, backend=args.backend)
    summary = analyze_batch(args.inputs, args.out, config, args.include_audio, args.system_channel is None)
    print(f"已处理 {summary['files']} 个文件，候选 {summary['candidates']} 个，错误 {summary['errors']} 个。报告：{args.out / 'report.html'}")
    return 3 if summary["errors"] else 1 if args.fail_on_findings and summary["candidates"] else 0


def run_promote(args):
    from .review import promote
    result = promote(args.review, args.results, args.out, args.dataset_kind)
    print(f"已保存 {len(result['labels'])} 条人工复核标签：{args.out}")


def run_evaluate(args):
    from voice_tools.core.files import write_json
    from .review import evaluate, new_file
    result = evaluate(args.golden, args.results)
    write_json(new_file(args.out), result)
    print(f"已评估 {result['evaluated']} 条标签；precision={result['precision']}，recall={result['recall']}。{args.out}")
