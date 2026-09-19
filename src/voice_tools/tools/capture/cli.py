import json
from pathlib import Path


def register(commands):
    parser = commands.add_parser("capture", help="SSH 按 UUID、号码或时间窗抓包并取回")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "fetch"):
        cmd = actions.add_parser(action, help="定位并抓包" if action == "start" else "重试取回已完成的抓包")
        cmd.add_argument("--host", required=True, help="SSH 配置别名或 user@hostname")
        cmd.add_argument("--ssh-port", type=int, default=22)
        cmd.add_argument("--identity", type=Path)
        cmd.add_argument("--out", required=True, type=Path)
        if action == "fetch":
            cmd.add_argument("--remote-dir", required=True)
        else:
            source = cmd.add_mutually_exclusive_group(required=True)
            source.add_argument("--uuid", help="活动通话 UUID；自动尝试展开一条本机桥接腿")
            source.add_argument("--flow", nargs=4, action="append", metavar=("LOCAL_IP", "LOCAL_PORT", "REMOTE_IP", "REMOTE_PORT"),
                                help="明确的双向 UDP 四元组，可重复")
            cmd.add_argument("--fs-cli", default="fs_cli", help="远端 fs_cli 可执行文件路径")
            cmd.add_argument("--no-peer", action="store_true")
            cmd.add_argument("--interface", default="any")
            cmd.add_argument("--seconds", type=int, default=60)
            cmd.add_argument("--max-mib", type=int, default=64, help="文件大小额度；dumpcap 使用实际字节，tcpdump 使用保守包数")
            cmd.add_argument("--snaplen", type=int, default=65535)
            cmd.add_argument('--backend', choices=('dumpcap', 'tcpdump'), default='dumpcap', help='dumpcap 按文件字节限额；tcpdump 保留旧版保守包数上限')
            cmd.add_argument("--sudo", action="store_true", help="使用 sudo -n 执行远端限时抓包")
            cmd.add_argument("--dry-run", action="store_true", help="仅查询端点和保存计划；UUID 模式仍有只读 SSH 查询")
        cmd.set_defaults(run=run)
    batch = actions.add_parser('batch', help='按主机清单并发抓取一段时间内的 SIP/RTP，分片取回')
    batch.add_argument('--inventory', type=Path, required=True)
    batch.add_argument('--seconds', type=int, default=300)
    batch.add_argument('--segment-seconds', type=int, default=60)
    batch.add_argument('--max-mib', type=int, default=512, help='每主机全部分片的保守总额度')
    batch.add_argument('--snaplen', type=int, default=65535)
    batch.add_argument('--backend', choices=('dumpcap', 'tcpdump'), default='dumpcap')
    batch.add_argument('--fs-snapshot-seconds', type=int, default=10, help='0 关闭，5–30 秒；默认启用 UUID/Call-ID 取样')
    batch.add_argument('--max-snapshot-channels', type=int, default=100)
    batch.add_argument('--dry-run', action='store_true', help='离线生成各机 BPF 与计划，不连接主机')
    batch.add_argument('--out', type=Path, required=True)
    batch.set_defaults(run=run_batch)
    recover = actions.add_parser('fetch-batch', help='恢复取回某台主机的所有分片')
    recover.add_argument('--host', required=True)
    recover.add_argument('--ssh-port', type=int, default=22)
    recover.add_argument('--identity', type=Path)
    recover.add_argument('--remote-dir', required=True)
    recover.add_argument('--out', type=Path, required=True)
    recover.set_defaults(run=run_batch)
    from .ring_cli import register as register_ring
    register_ring(actions)
    from .number_cli import register as register_numbers
    register_numbers(actions)


def run_batch(args):
    from .batch import run_batch as collect, fetch_batch
    from .service import SSH
    from voice_tools.core.command import artifacts, emit_result
    if args.action == 'batch':
        if args.backend == 'dumpcap':
            from .ring import run_window as collect
        result = collect(args.inventory, args.out, args.seconds, args.segment_seconds, args.max_mib, args.snaplen,
                         args.fs_snapshot_seconds, args.max_snapshot_channels, args.dry_run)
        name = 'batch.json'
    else:
        result = fetch_batch(SSH(args.host, args.ssh_port, args.identity), args.remote_dir, args.out)
        name = 'host.json'
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2), artifacts(args.out, name),
                       0 if result['status'] in ('planned', 'complete', 'recovered') else 3)


def run(args):
    from .service import SSH, fetch, start
    ssh = SSH(args.host, args.ssh_port, args.identity)
    if args.action == "fetch":
        result = fetch(ssh, args.remote_dir, args.out)
    else:
        flows = [dict(zip(("local_ip", "local_port", "remote_ip", "remote_port"), item)) for item in args.flow or []]
        result = start(ssh, args.out, uuid=args.uuid, manual_flows=flows, fs_cli=args.fs_cli,
                       include_peer=not args.no_peer, interface=args.interface, seconds=args.seconds,
                       max_mib=args.max_mib, snaplen=args.snaplen, sudo=args.sudo, dry_run=args.dry_run, backend=args.backend)
    from voice_tools.core.command import artifacts, emit_result
    names = ["capture.json"] + (["session.pcap"] if "pcap" in result else [])
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2),
                       artifacts(args.out, *names), 0 if result["status"] in ("planned", "complete", "recovered") else 3)
