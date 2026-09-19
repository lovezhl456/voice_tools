import json
from pathlib import Path


def register(actions):
    start = actions.add_parser('ring-start', help='启动有时限、有磁盘额度的远端环形 SIP/RTP/RTCP 采集')
    start.add_argument('--inventory', type=Path, required=True)
    start.add_argument('--out', type=Path, required=True)
    start.add_argument('--seconds', type=int, default=86400)
    start.add_argument('--segment-seconds', type=int, default=60)
    start.add_argument('--max-mib', type=int, default=512)
    start.add_argument('--ring-files', type=int, default=32)
    start.add_argument('--snaplen', type=int, default=65535)
    start.add_argument('--fs-snapshot-seconds', type=int, default=10)
    start.add_argument('--max-snapshot-channels', type=int, default=100)
    start.add_argument('--dry-run', action='store_true')
    start.set_defaults(run=run)
    for name, help_text in (('ring-status', '查看各机状态与已关闭分片'), ('ring-stop', '请求停止此任务的采集'),
                            ('ring-fetch', '冻结指定历史窗口的已关闭分片并校验取回'),
                            ('ring-release', '删除指定已取回冻结副本，释放冻结额度；不删除环形原始包')):
        p = actions.add_parser(name, help=help_text)
        p.add_argument('--job', type=Path, required=True)
        if name == 'ring-fetch':
            p.add_argument('--from', dest='start', required=True)
            p.add_argument('--to', dest='end', required=True)
            p.add_argument('--out', type=Path, required=True)
        if name == 'ring-release':
            p.add_argument('--host', required=True, help='主机清单 name')
            p.add_argument('--freeze-id', required=True)
        p.set_defaults(run=run)


def run(args):
    from . import ring
    from voice_tools.core.command import emit_result, artifacts
    files = {}
    if args.action == 'ring-start':
        result = ring.start(args.inventory, args.out, args.seconds, args.segment_seconds, args.max_mib,
                            args.snaplen, args.fs_snapshot_seconds, args.max_snapshot_channels,
                            args.dry_run, ring_files=args.ring_files)
        files = artifacts(args.out, 'job.json')
    elif args.action == 'ring-status':
        result = ring.status(args.job)
    elif args.action == 'ring-stop':
        result = ring.stop(args.job)
    elif args.action == 'ring-release':
        result = ring.release(args.job, args.host, args.freeze_id)
    else:
        result = ring.fetch(args.job, args.out, args.start, args.end)
        files = artifacts(args.out, 'batch.json')
    partial = result.get('partial') or result.get('status') in ('partial', 'failed', 'error')
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2), files, 3 if partial else 0)
