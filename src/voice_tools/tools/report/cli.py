from pathlib import Path


def register(commands):
    parser = commands.add_parser("report", help="基于多录音和 PCAP 生成离线 HTML 分析报告")
    actions = parser.add_subparsers(dest="action", required=True)
    cmd = actions.add_parser("build", help="生成 report.html 与可追溯的 report.json")
    cmd.add_argument("--audio", type=Path, nargs="+", action="extend", default=[], help="单/双声道录音，可重复；非 PCM16 WAV 需要 FFmpeg")
    cmd.add_argument("--pcap", type=Path, nargs="+", action="extend", default=[], help="PCAP/PCAPNG，可重复")
    cmd.add_argument("--capture", type=Path, action="append", default=[], help="本工具抓包目录，自动读取端口及校验 SHA-256")
    cmd.add_argument('--session-export', type=Path, action='append', default=[], help='sessions export 目录，读取候选媒体与端口信息')
    cmd.add_argument('--correlation', type=Path, action='append', default=[], help='sessions correlate 目录，纳入 HOMER 查询状态与关联证据')
    cmd.add_argument("--rtp-port", type=int, action="append", default=[], help="将已知 UDP 端口按 RTP 解码，可重复")
    cmd.add_argument("--clock-rate", action="append", default=[], metavar="PT=HZ", help="动态 RTP payload type 的时钟频率，例如 111=48000")
    cmd.add_argument("--max-packets", type=int, default=250000, help="每份 PCAP 最大分析包数，超限标注部分结果")
    cmd.add_argument("--threshold-db", type=float, default=-45)
    cmd.add_argument("--tshark", default="tshark", help="本机 tshark 路径")
    cmd.add_argument("--include-audio", action="store_true", help="将录音复制到报告目录，支持离线试听")
    cmd.add_argument("--title", default="通话媒体分析报告")
    cmd.add_argument("--out", required=True, type=Path)
    cmd.set_defaults(run=run)


def run(args):
    from .service import build
    rates = {}
    for value in args.clock_rate:
        try:
            pt, rate = (int(part) for part in value.split("="))
            if not 0 <= pt <= 127 or not 1 <= rate <= 384000:
                raise ValueError()
        except ValueError:
            raise ValueError("clock-rate 须为 PT=HZ，PT 为 0–127，HZ 为 1–384000") from None
        rates[pt] = rate
    result = build(args.out, args.audio, args.pcap, args.capture, args.rtp_port, rates,
                   args.include_audio, args.threshold_db, args.max_packets, args.tshark, args.title,
                   session_exports=args.session_export, correlations=args.correlation)
    from voice_tools.core.command import artifacts, emit_result
    summary = {"audio": len(result["audio"]), "pcaps": len(result["pcaps"]), "errors": result["errors"], "partial": result["partial"]}
    return emit_result(args, summary,
                       f"报告：{args.out / 'report.html'}；录音 {summary['audio']}，PCAP {summary['pcaps']}，错误 {summary['errors']}",
                       artifacts(args.out, "report.html", "report.json"), 3 if result["partial"] else 0)
