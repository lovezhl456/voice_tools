"""命令装配；未调用时不加载 ViSQOL 或 NumPy。"""
from pathlib import Path


def backend_args(parser):
    parser.add_argument("--visqol-dir", type=Path, help="包含 bazel-bin/visqol 和 model/ 的官方源码目录；也可设置 VOICE_TOOLS_VISQOL_DIR")
    parser.add_argument("--mode", choices=("speech", "audio"), default="speech", help="speech=16 kHz；audio=48 kHz；默认 speech")


def register(commands):
    parser = commands.add_parser("visqol", help="ViSQOL 全参考音质：原声与待测 WAV 成对比较")
    actions = parser.add_subparsers(dest="action", required=True)
    doctor = actions.add_parser("doctor", help="离线检查后端二进制、模型和安装记录")
    backend_args(doctor)
    doctor.set_defaults(run=run_doctor)
    for name, description in (("score", "比较一对原声与待测 WAV"), ("batch", "按 CSV 顺序评分，单对失败继续并返回部分失败")):
        cmd = actions.add_parser(name, help=description)
        backend_args(cmd)
        cmd.add_argument("--out", required=True, type=Path, help="新建或空的结果目录，不覆盖已有文件")
        cmd.add_argument("--timeout", type=float, default=120, help="每对后端超时秒数，0–3600，不含输入准备")
        if name == "score":
            cmd.add_argument("--reference", required=True, type=Path, help="干净的同一句原声，单声道 PCM16 WAV")
            cmd.add_argument("--degraded", required=True, type=Path, help="同一句对应的待测音频，单声道 PCM16 WAV")
        else:
            cmd.add_argument("--pairs", required=True, type=Path, help="表头 reference,degraded；相对路径以 CSV 所在目录为准")
        cmd.set_defaults(run=run_score)


def run_doctor(args):
    from .service import backend_info
    from voice_tools.core.command import emit_result
    info = backend_info(args.visqol_dir, args.mode)
    if not info["ready"]:
        raise ValueError("ViSQOL 后端未就绪：" + "、".join(info["missing"]) +
                         "；请运行 scripts/install_visqol.py 并指定 --visqol-dir")
    return emit_result(args, info, f"ViSQOL 文件检查通过：{info['directory']}（未执行评分）")


def run_score(args):
    from .service import process, read_pairs
    from voice_tools.core.command import emit_result, artifacts
    pairs = read_pairs(args.pairs) if args.action == "batch" else [{"reference": args.reference, "degraded": args.degraded}]
    summary = process(pairs, args.out, directory=args.visqol_dir, mode=args.mode, timeout=args.timeout)
    return emit_result(args, summary, f"已比较 {summary['pairs']} 对：完成 {summary['completed']}，错误 {summary['errors']}。结果：{args.out}",
                       artifacts(args.out, "run.json", "results.jsonl", "results.csv"), 3 if summary["errors"] else 0)
