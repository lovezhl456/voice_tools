"""Independent CLI; importing this module never loads optional runtimes."""
from pathlib import Path


def register(commands):
    parser = commands.add_parser("gaps", help="输出中途停顿、短断音与事件旁证复核")
    actions = parser.add_subparsers(dest="action", required=True)
    cmd = actions.add_parser("analyze", help="独立离线检测 WAV，可选导入事件和已有证据")
    cmd.add_argument("inputs", nargs="+", type=Path)
    cmd.add_argument("--out", required=True, type=Path)
    cmd.add_argument("--events", type=Path, help="单录音的标准事件 JSON；默认同名 .events.json")
    cmd.add_argument("--evidence", type=Path, help="gap_evidence 关联清单；不运行抓包或评分")
    cmd.add_argument("--system-channel", type=int, choices=(0, 1), help="默认读取事件角色，否则右轨为 AI")
    cmd.add_argument("--channels-verified", action="store_true")
    cmd.add_argument("--backend", choices=("energy", "webrtcvad"), default="energy")
    for flag, default in (("threshold-db", -45), ("dead-air-ms", 800), ("micro-min-ms", 50),
                          ("micro-max-ms", 300), ("near-silence-db", -60), ("cluster-window-ms", 1500)):
        cmd.add_argument("--" + flag, type=float, default=default)
    cmd.add_argument("--cluster-min-count", type=int, default=3)
    cmd.add_argument("--include-audio", action="store_true", help="复制原音频和分轨用于离线试听")
    cmd.add_argument("--hide-paths", action="store_true", help="报告隐藏绝对路径；JSON 保留来源")
    cmd.add_argument("--fail-on-findings", action="store_true")
    cmd.set_defaults(run=run_analyze)
    echo = actions.add_parser("echo", help="对齐回声拨测双轨，量化停顿、旧音及 RTP 旁证")
    echo.add_argument("wav", type=Path, help="双声道 PCM16 WAV，原声与浏览器实际返听各一轨")
    echo.add_argument("--out", required=True, type=Path)
    echo.add_argument("--reference-channel", choices=("left", "right"), default="left")
    echo.add_argument("--pause-ms", type=float, default=120)
    echo.add_argument("--silence-db", type=float, default=-60)
    echo.add_argument("--old-audio-ms", type=float, default=1000)
    echo.add_argument("--max-age-ms", type=float, default=20000)
    echo.add_argument("--pcap", type=Path, help="可选，单通同一采集点的 PCAP/PCAPNG")
    echo.add_argument("--recording-start-epoch", type=float, help="已核实的录音零点，对应抓包 epoch 秒")
    echo.add_argument("--flow", action="append", default=[], metavar="NAME=SRC:DST",
                      help="命名的 RTP 方向与 UDP 端口，可重复")
    echo.add_argument("--plaintext-flow", action="append", default=[], metavar="NAME",
                      help="明确为明文 G.711 的流向，可重复")
    echo.add_argument("--tshark", default="tshark", help="本机 tshark 路径")
    echo.set_defaults(run=run_echo)
    review = actions.add_parser("review-check", help="校验间隙 CSV 与检测身份、人工区间和复核信息")
    review.add_argument("review", type=Path)
    review.add_argument("--results", type=Path, required=True)
    review.set_defaults(run=run_review)


def run_analyze(args):
    from .detector import Config
    from .service import analyze_batch
    from voice_tools.core.command import emit_result, artifacts
    config = Config(system_channel=1 if args.system_channel is None else args.system_channel,
        channels_verified=args.channels_verified, backend=args.backend, threshold_db=args.threshold_db,
        dead_air_ms=args.dead_air_ms, micro_min_ms=args.micro_min_ms, micro_max_ms=args.micro_max_ms,
        near_silence_db=args.near_silence_db, cluster_window_ms=args.cluster_window_ms, cluster_min_count=args.cluster_min_count)
    result = analyze_batch(args.inputs, args.out, config, args.events, args.evidence, args.include_audio,
                           args.system_channel is None, args.hide_paths)
    code = 3 if result["errors"] else 1 if args.fail_on_findings and result["candidates"] else 0
    return emit_result(args, result, f"间隙候选 {result['candidates']}，处理错误 {result['errors']}；{args.out / 'review.html'}",
        artifacts(args.out, "results.jsonl", "summary.csv", "run.json", "report.html", "review.html", "review.csv"), code)


def run_review(args):
    from .review import check
    from voice_tools.core.command import emit_result
    result = check(args.review, args.results)
    return emit_result(args, result, f"已校验 {result['labels']} 条标注，人工补标 {result['manual']} 条")


def run_echo(args):
    from voice_tools.core.command import artifacts, emit_result
    from .echo import analyze
    from .echo_capture import capture_options

    capture = capture_options(args.pcap, args.recording_start_epoch, args.flow, args.plaintext_flow, args.tshark)
    result = analyze(args.wav, args.out, args.reference_channel, args.pause_ms, args.silence_db,
                     args.old_audio_ms, args.max_age_ms, capture)
    playback = result["playback"]
    capture_status = result.get("capture", {}).get("status", "not_requested")
    summary = {"pauses": len(result["quiet_intervals"]), "matched_windows": playback["matched_windows"],
               "max_playback_age_ms": playback["max_age_ms"], "old_audio_windows": playback["old_audio_windows"],
               "capture_status": capture_status}
    return emit_result(args, summary, f"回声分析：{args.out / 'echo.md'}",
                       artifacts(args.out, "echo.md", "echo.json"), 3 if capture_status in ("error", "partial") else 0)
