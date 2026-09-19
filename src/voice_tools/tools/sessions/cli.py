import json
from pathlib import Path


def register(commands):
    parser = commands.add_parser('sessions', help='多主机会话索引、检索、导出与 HOMER 联动')
    actions = parser.add_subparsers(dest='action', required=True)
    index = actions.add_parser('index', help='离线建立 Call-ID/号码/UUID/主机索引')
    for option in ('pcap', 'batch', 'homer-json', 'snapshots'):
        index.add_argument('--'+option, type=Path, action='append', default=[])
    index.add_argument('--pcap-group', nargs='+', action='append', default=[], metavar='SENSOR_OR_FILE', help='同一采集点的连续分片：名称 FILE FILE，可重复')
    index.add_argument('--events', type=Path, action='append', default=[], help='ESL 事件 JSONL')
    index.add_argument('--sip-port', type=int, action='append', default=[], help='非标准 SIP 端口；默认含 5060')
    index.add_argument('--max-packets', type=int, default=1000000)
    index.add_argument('--tshark', default='tshark')
    index.add_argument('--out', type=Path, required=True)
    index.set_defaults(run=run)
    search = actions.add_parser('search', help='按精确 Call-ID、号码、UUID、主机或时间检索')
    search.add_argument('--index', type=Path, required=True)
    for option in ('call-id', 'number', 'uuid', 'host'):
        search.add_argument('--'+option)
    search.add_argument('--from', dest='start')
    search.add_argument('--to', dest='end')
    search.add_argument('--limit', type=int, default=100)
    search.add_argument('--offset', type=int, default=0)
    search.set_defaults(run=run)
    for action in ('show', 'export', 'correlate'):
        cmd = actions.add_parser(action, help={'show': '查看证据与跨机观测', 'export': '从原始 PCAP 分离指定会话', 'correlate': '按本地 Call-ID/时间查询 HOMER search+trace'}[action])
        cmd.add_argument('--index', type=Path, required=True)
        cmd.add_argument('--call-id', required=True)
        if action != 'show':
            cmd.add_argument('--out', type=Path, required=True)
            cmd.add_argument('--padding', type=int, default=30 if action == 'correlate' else 2)
        if action == 'export':
            cmd.add_argument('--include-media', action='store_true', help='纳入端点/时间匹配的 RTP 候选，不宣称确定归属')
            cmd.add_argument('--tshark', default='tshark')
        if action == 'correlate':
            homer_options(cmd)
        cmd.set_defaults(run=run)
    remote = actions.add_parser('homer-search', help='先从 HOMER 按时间/号码发现 Call-ID，保存可导入 JSON')
    remote.add_argument('--from', dest='start', required=True)
    remote.add_argument('--to', dest='end', required=True)
    for option in ('caller', 'callee', 'call-id'):
        remote.add_argument('--'+option)
    remote.add_argument('--out', type=Path, required=True)
    homer_options(remote)
    remote.set_defaults(run=run)
    full = actions.add_parser('investigate', help='冻结远端窗口 → HOMER → 索引/导出 → HTML 报告，保留各步骤失败')
    full.add_argument('--job', type=Path, required=True)
    full.add_argument('--from', dest='start', required=True)
    full.add_argument('--to', dest='end', required=True)
    full.add_argument('--call-id', required=True)
    full.add_argument('--out', type=Path, required=True)
    full.add_argument('--audio', nargs='+', type=Path, default=[])
    full.add_argument('--skip-homer', action='store_true')
    full.add_argument('--include-audio', action='store_true')
    full.add_argument('--decode-rtp', action='store_true')
    full.add_argument('--timeout', type=int, default=600, help='每步骤最大秒数')
    full.add_argument('--dry-run', action='store_true', help='只保存调用计划，不连接 SSH/HOMER')
    homer_options(full)
    full.set_defaults(run=run)


def homer_options(cmd):
    cmd.add_argument('--profile', default='1_call')
    cmd.add_argument('--node', action='append', default=[])
    cmd.add_argument('--max-requests', type=int, default=64)


def run(args):
    import sqlite3
    try:
        return dispatch(args)
    except sqlite3.Error as error:
        raise ValueError(f'会话索引读写失败：{error}') from error


def dispatch(args):
    from voice_tools.core.command import artifacts, emit_result
    from . import store
    names = []
    if args.action == 'index':
        from voice_tools.core.packets import parse_groups
        result = store.build(args.out, args.pcap, args.batch, args.homer_json, args.snapshots,
                             list(set([5060, *args.sip_port])), args.max_packets, args.tshark,
                             pcap_groups=parse_groups(args.pcap_group), events=args.events)
        names = ['sessions.sqlite', 'index.json']
    elif args.action == 'search':
        result = store.search(args.index, args.call_id, args.number, args.uuid, args.host, args.start, args.end, args.limit, args.offset)
    elif args.action == 'show':
        result = store.show(args.index, args.call_id)
    elif args.action == 'export':
        from .export import export
        result = export(args.index, args.call_id, args.out, args.include_media, args.padding, args.tshark)
        names = ['session.json']
    elif args.action == 'correlate':
        from .homer import correlate
        result = correlate(args.index, args.call_id, args.out, args.padding, args.profile, args.node, args.max_requests)
        names = ['correlation.json', 'homer-search.json', 'homer-trace.json']
    elif args.action == 'investigate':
        from .workflow import investigate
        result = investigate(args.job, args.start, args.end, args.call_id, args.out, args.audio,
                             args.profile, args.node, args.skip_homer, args.include_audio, args.decode_rtp,
                             args.timeout, args.dry_run, args.max_requests)
        names = ['investigation.json']
    else:
        from .homer import search_remote
        result = search_remote(args.out, args.start, args.end, args.caller, args.callee, args.call_id, args.profile, args.node, args.max_requests)
        names = ['query.json', 'homer-search.json']
    return emit_result(args, result, json.dumps(result, ensure_ascii=False, indent=2),
                       artifacts(args.out, *names) if names else {}, 3 if result.get('partial') else 0)
