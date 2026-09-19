import json
from pathlib import Path


def register(actions):
    p = actions.add_parser('by-number', help='按主叫/被叫限时抓包，服务器按会话拆分并打包下载')
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--caller', action='append', default=[], help='精确主叫，可重复；同维度 OR，主被叫之间 AND')
    p.add_argument('--callee', action='append', default=[], help='精确被叫，可重复；不自动处理国家码或前缀')
    p.add_argument('--seconds', type=int, default=300)
    p.add_argument('--segment-seconds', type=int, default=60)
    p.add_argument('--max-mib', type=int, default=512, help='每台主机原始抓包额度，最多 2048 MiB')
    p.add_argument('--snaplen', type=int, default=65535)
    p.add_argument('--fs-snapshot-seconds', type=int, default=10)
    p.add_argument('--max-snapshot-channels', type=int, default=100)
    p.add_argument('--max-sessions', type=int, default=1000, help='每台主机最多导出会话数，超限标 partial')
    p.add_argument('--bundle-mib', type=int, default=512, help='每台主机会话包及其展开内容额度')
    p.add_argument('--max-packets', type=int, default=1000000, help='每台主机索引包数上限，超限标 partial')
    p.add_argument('--processing-seconds', type=int, default=1800, help='采集结束后远端拆包与压缩最大秒数')
    p.add_argument('--dry-run', action='store_true', help='离线生成计划，不连接 SSH')
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(run=run)
    p = actions.add_parser('fetch-number', help='断线后重新下载号码抓包的会话压缩包')
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--wait', action='store_true', help='等待远端限时采集和拆包完成')
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(run=run)


def run(args):
    from . import numbers
    from voice_tools.core.command import artifacts, emit_result
    if args.action == 'fetch-number':
        result = numbers.fetch(args.job, args.out, args.wait)
    else:
        result = numbers.start(args.inventory, args.out, args.caller, args.callee, args.seconds,
            args.segment_seconds, args.max_mib, args.snaplen, args.fs_snapshot_seconds,
            args.max_snapshot_channels, args.max_sessions, args.bundle_mib, args.max_packets,
            args.processing_seconds, args.dry_run)
    files = artifacts(args.out, 'number.json', 'job.json')
    files.update({h['name'] + '/sessions.zip': args.out / h['archive'] for h in result['hosts'] if h.get('archive')})
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2), files,
                       0 if result['status'] in ('planned', 'complete') else 3)
