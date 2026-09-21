"""Argument registration for the whole-recording workflow; no model imports."""
from pathlib import Path

from voice_tools.core.command import artifacts, emit_result


def register(actions):
    assess = actions.add_parser('assess', help='工程规则与本地语音模型联合质检，按整通录音输出人工例外队列')
    assess.add_argument('inputs', type=Path, nargs='+')
    assess.add_argument('--out', type=Path, required=True)
    assess.add_argument('--model-dir', dest='qa_model_dir', type=Path, help='已下载的 Silero 语音模型目录；推理不联网')
    assess.add_argument('--rules-only', action='store_true', help='仅做工程检查；未运行模型的录音进入待复核')
    assess.add_argument('--system-channel', type=int, choices=(0, 1), help='AI 声道；缺省读取事件文件，否则为 1')
    assess.add_argument('--channels-verified', action='store_true', help='已核实双轨角色')
    assess.add_argument('--ai-start', type=float, help='已核实的 AI 接管秒数；缺少时不自动通过')
    assess.add_argument('--timeout', type=float, default=5, help='无应答／迟答阈值，秒')
    assess.add_argument('--turn-gap', type=float, default=.8, help='用户续说合并间隔，秒；不跨已检测到的 AI 回答')
    assess.add_argument('--output-gap', type=float, default=2, help='输出长停顿阈值，秒')
    assess.add_argument('--long-silence', type=float, default=10, help='录音中长静音复查阈值，秒')
    assess.add_argument('--audit-percent', type=float, default=5, help='自动通过录音的稳定抽检百分比，0–100')
    assess.add_argument('--include-audio', action='store_true')
    assess.add_argument('--hide-paths', action='store_true')
    assess.set_defaults(run=run_assess)
    download = actions.add_parser('model-download', help='显式下载并校验固定版本的 CPU 语音模型')
    download.add_argument('--out', type=Path, required=True)
    download.set_defaults(run=run_download)
    doctor = actions.add_parser('model-doctor', help='离线校验语音模型、CPU 依赖与一次真实推理')
    doctor.add_argument('--model-dir', dest='qa_model_dir', type=Path)
    doctor.set_defaults(run=run_doctor)
    check = actions.add_parser('assess-check', help='核对独立的整通人工标签 CSV')
    check.add_argument('review', type=Path)
    check.add_argument('--results', type=Path, required=True)
    check.set_defaults(run=run_check)
    evaluate = actions.add_parser('assess-evaluate', help='用人工整通标签评估自动判定与复核覆盖')
    evaluate.add_argument('review', type=Path)
    evaluate.add_argument('--results', type=Path, required=True)
    evaluate.add_argument('--dataset-kind', choices=('synthetic', 'real'), required=True)
    evaluate.add_argument('--out', type=Path, required=True)
    evaluate.set_defaults(run=run_evaluate)


def run_assess(args):
    from .assessment import Policy
    from .assessment_batch import run
    policy = Policy(timeout_s=args.timeout, turn_gap_s=args.turn_gap, output_gap_s=args.output_gap,
                    long_silence_s=args.long_silence, audit_percent=args.audit_percent,
                    system_channel=1 if args.system_channel is None else args.system_channel,
                    channels_verified=args.channels_verified, ai_start_s=args.ai_start)
    summary = run(args.inputs, args.out, policy, args.qa_model_dir, args.rules_only,
                  args.include_audio, args.hide_paths, args.system_channel is None)
    code = 3 if summary['errors'] else 1 if summary['decisions']['AUTO_ANOMALY'] or summary['decisions']['NEEDS_REVIEW'] else 0
    return emit_result(args, summary,
        f"已处理 {summary['files']} 通录音，自动通过 {summary['decisions']['AUTO_PASS']}，"
        f"自动异常 {summary['decisions']['AUTO_ANOMALY']}，待复核 {summary['decisions']['NEEDS_REVIEW']}；"
        f"人工队列含抽检 {summary['review_recordings']} 通。{args.out / 'report.html'}",
        artifacts(args.out, 'assessment.jsonl', 'run.json', 'summary.csv', 'recording-review.csv', 'report.html'), code)


def run_download(args):
    from .speech_model import download
    result = download(args.out)
    return emit_result(args, result, '语音模型已校验：' + result['model'], {'model': result['model']})


def run_doctor(args):
    from .speech_model import doctor
    result = doctor(args.qa_model_dir)
    return emit_result(args, result, '语音模型就绪' if result['ready'] else '; '.join(result['issues']), exit_code=0 if result['ready'] else 1)


def run_check(args):
    from .assessment_review import check
    records, labels = check(args.review, args.results)
    return emit_result(args, {'recordings': len(records), 'human_labels': len(labels)}, f'已校验 {len(labels)} 通人工标签')


def run_evaluate(args):
    from voice_tools.core.files import write_json
    from .assessment_review import evaluate
    from .review import new_file
    result = evaluate(args.review, args.results, args.dataset_kind)
    write_json(new_file(args.out), result)
    return emit_result(args, result, f"已评估 {result['human_labels']} 通人工标签：{args.out}", {'metrics': args.out})
