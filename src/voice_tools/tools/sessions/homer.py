"""通过现有只读 CLI 协议联动，不复制 HOMER 认证或 API 实现。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from voice_tools.core.files import new_output, read_json, write_json
from .store import epoch, show


def call_cli(argv, output):
    target = Path(output)
    errors = target.with_suffix('.stderr.json')
    command = [sys.executable, '-m', 'voice_tools', 'homer', *argv]
    with target.open('wb') as stdout, errors.open('wb') as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=300)
        except subprocess.TimeoutExpired:
            return {'exit_code': 124, 'status': 'failed', 'error': 'HOMER CLI 超过 300 秒；未自动重试', 'file': target.name}
    if result.returncode not in (0, 6):
        return {'exit_code': result.returncode, 'status': 'failed', 'error_file': errors.name, 'file': target.name}
    try:
        if target.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('HOMER 返回超过 128 MiB，文件已保留，请缩短查询窗口')
        data = read_json(target)
        if not isinstance(data, dict):
            raise ValueError('HOMER JSON 顶层须为对象')
    except (ValueError, OSError) as error:
        return {'exit_code': result.returncode, 'status': 'failed', 'error': str(error), 'file': target.name}
    return {'exit_code': result.returncode, 'status': 'partial' if result.returncode == 6 else 'ok',
            'file': target.name, 'messages': data.get('count'), 'completeness': data.get('completeness', {}),
            'warnings': data.get('warnings', [])}


def time_args(start, end, profile, nodes):
    a, b = epoch(start), epoch(end)
    if not 0 <= a < b or b - a > 31 * 86400:
        raise ValueError('HOMER 查询窗口须大于 0 且不超过 31 天')
    args = ['--from', datetime.fromtimestamp(a, timezone.utc).isoformat(),
            '--to', datetime.fromtimestamp(b, timezone.utc).isoformat(), '--profile', profile]
    for node in nodes:
        args += ['--node', node]
    return args


def search_remote(output, start, end, caller=None, callee=None, call_id=None, profile='1_call', nodes=(), max_requests=64):
    if not 1 <= max_requests <= 1000:
        raise ValueError('HOMER 请求预算为 1–1000')
    args = ['search', *time_args(start, end, profile, nodes), '--all', '--max-requests', str(max_requests)]
    for flag, value in (('--caller', caller), ('--callee', callee), ('--call-id', call_id)):
        if value is not None:
            args += [flag, value]
    output = new_output(output)
    output.chmod(0o700)
    result = call_cli(args, output / 'homer-search.json')
    summary = {'schema_version': '1.0', 'tool': 'sessions-homer-search', 'result': result,
               'partial': result['status'] != 'ok', 'notice': '结果条数是 SIP 报文数，HOMER 未证明抓包完整。'}
    write_json(output / 'query.json', summary)
    return summary


def correlate(index, call_id, output, padding=30, profile='1_call', nodes=(), max_requests=64):
    if not 0 <= padding <= 3600 or not 1 <= max_requests <= 1000:
        raise ValueError('时间扩展为 0–3600 秒，请求预算为 1–1000')
    local = show(index, call_id)
    times = [item['epoch'] for item in local['observations'] if item['epoch'] is not None]
    if not times:
        raise ValueError('本地没有可靠时间戳；请使用 homer-search 显式指定时间')
    start, end = max(0, min(times) - padding), max(times) + max(1, padding)
    interval = time_args(start, end, profile, nodes)
    output = new_output(output)
    output.chmod(0o700)
    search = call_cli(['search', *interval, '--call-id', call_id, '--all', '--max-requests', str(max_requests)], output / 'homer-search.json')
    # A partial or failed search must not discard a separately available exact transaction.
    trace_ids = [call_id, *sorted({leg['call_id'] for leg in local['related_legs']})]
    trace_limited = len(trace_ids) > 20
    trace_ids = trace_ids[:20]
    trace_args = [arg for cid in trace_ids for arg in ('--call-id', cid)]
    trace = call_cli(['trace', *interval, *trace_args, '--include-raw'], output / 'homer-trace.json')
    ids = set()
    if trace['status'] != 'failed':
        for row in read_json(output / 'homer-trace.json').get('messages', []):
            if row.get('sid') or row.get('callid'):
                ids.add(row.get('sid') or row.get('callid'))
    summary = {'schema_version': '1.0', 'tool': 'sessions-correlation', 'call_id': call_id,
               'window': {'from_epoch': start, 'to_epoch': end}, 'local_observations': len(local['observations']),
               'local_hosts': sorted({item['host'] for item in local['observations']}),
               'local_related_legs': local['related_legs'], 'trace_requested_call_ids': trace_ids,
               'search': search, 'trace': trace, 'related_call_ids_from_homer': sorted(ids - {call_id}),
               'partial': search['status'] != 'ok' or trace['status'] != 'ok' or trace_limited or local['partial'],
               'local_index_status': local['index_status'],
               'warnings': ['以精确 Call-ID 和时间窗关联，不按电话号码自动合并。',
                            'HOMER trace 可能包含服务端关联的其他 Call-ID 和窗口外报文；不等于已证明业务同一性或完整性。',
                            'HOMER 导出的 PCAP 是 SIP 报文重建，不能替代原始 RTP 抓包。']}
    if trace_limited:
        summary['warnings'].append('本地关联 Call-ID 超过 HOMER 单次 20 个上限，trace 仅查询前 20 个。')
    write_json(output / 'correlation.json', summary)
    return summary
