"""CLI declaration, including offline machine discovery through voice-tools schema."""
import json
from pathlib import Path


def register(commands):
    parser = commands.add_parser("sip", help="轻量 SIP UDP 拨测、PCAP 音频／按键导入与独立 SIPp 回放")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("doctor", help="离线检查 PJSUA2、tshark 和 SIPp").set_defaults(run=run)
    init = actions.add_parser("init", help="生成可编辑的单通话策略模板")
    init.add_argument("--out", type=Path, required=True)
    init.set_defaults(run=run)
    for action in ("validate", "run"):
        cmd = actions.add_parser(action, help="离线校验策略与素材" if action == "validate" else "执行一通呼叫并持续录制接收音频")
        cmd.add_argument("scenario", type=Path)
        if action == "run":
            cmd.add_argument("--out", type=Path, required=True)
            cmd.add_argument("--dry-run", action="store_true", help="保存计划；不导入 PJSUA2、不发 SIP")
        cmd.set_defaults(run=run)
    for action in ("pcap-inspect", "pcap-import", "sipp-prepare"):
        cmd = actions.add_parser(action, help={"pcap-inspect": "列出 PCAP／PCAPNG 单向 RTP 流", "pcap-import": "G.711 RTP 转 WAV 和去重的 DTMF 时间表", "sipp-prepare": "离线生成只含选定发送流的 SIPp 回放包"}[action])
        cmd.add_argument("pcap", type=Path)
        cmd.add_argument("--rtp-port", type=int, action="append", default=[], help="无 SDP 抓包时指定 Decode As RTP 端口，可重复")
        cmd.add_argument("--tshark", default="tshark")
        if action != "pcap-inspect":
            cmd.add_argument("--stream", required=True, help="pcap-inspect 返回的流 ID；必须显式选择方向")
            cmd.add_argument("--codec", choices=("PCMA", "PCMU"))
            cmd.add_argument("--audio-pt", type=int, help="音频 RTP payload type；静态 PT 0/8 可推断")
            cmd.add_argument("--dtmf-pt", type=int, help="telephone-event payload type；按原 SDP 指定，不猜测 101")
            cmd.add_argument("--out", type=Path, required=True)
        if action == "sipp-prepare":
            cmd.add_argument("--target", required=True, help="sip:user@IPv4:port；第一版不支持 SIPp Digest／REGISTER")
            cmd.add_argument("--local-ip", required=True)
            cmd.add_argument("--sip-port", type=int, default=5062)
            cmd.add_argument("--rtp-port-local", type=int, default=6000)
        cmd.set_defaults(run=run)
    replay = actions.add_parser("sipp-run", help="用独立 SIPp 进程执行一次已准备的回放包")
    replay.add_argument("package", type=Path)
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument("--sipp", default="sipp")
    replay.add_argument("--capture-interface", help="可选：同时用 tshark 抓本地 RTP 端口，不自动提权")
    replay.add_argument("--tshark", default="tshark")
    replay.add_argument("--dry-run", action="store_true")
    replay.set_defaults(run=run)


def run(args):
    from voice_tools.core.command import emit_result
    from . import service
    code = 0
    if args.action == "doctor":
        result = service.doctor()
        code = 0 if result["ready_for_calls"] else 3
    elif args.action == "init":
        result = service.initialize(args.out)
    elif args.action == "validate":
        from .scenario import load_scenario
        result = {"status": "valid", "network_accessed": False, "plan": load_scenario(args.scenario)}
    elif args.action == "run":
        result = service.run(args.scenario, args.out, args.dry_run)
        code = 0 if result["status"] in ("planned", "completed") else 3
    elif args.action == "sipp-run":
        from .sipp import run_package
        result = run_package(args.package, args.out, args.sipp, args.capture_interface, args.tshark, args.dry_run)
        code = 0 if result["status"] in ("planned", "completed") else 3
    else:
        from .pcap import import_capture, inspection, read_capture
        capture = read_capture(args.pcap, args.rtp_port, args.tshark)
        if args.action == "pcap-inspect":
            result = inspection(capture)
        elif args.action == "pcap-import":
            result = import_capture(capture, args.stream, args.out, args.codec, args.audio_pt, args.dtmf_pt)
        else:
            from .sipp import prepare
            result = prepare(capture, args.stream, args.out, args.target, args.local_ip, args.sip_port,
                             args.rtp_port_local, args.codec, args.audio_pt, args.dtmf_pt)
    artifacts = {}
    if getattr(args, "out", None) and args.out.is_dir():
        artifacts = {p.name: p for p in args.out.iterdir() if p.is_file()}
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2), artifacts, code)


def main(argv=None):
    """Independent voice-sip entrypoint with the same schema and JSON envelope."""
    import sys
    from voice_tools.cli import main as root
    argv = list(sys.argv[1:] if argv is None else argv)
    return root((["--json", "sip"] + argv[1:]) if argv and argv[0] == "--json" else ["sip"] + argv)
