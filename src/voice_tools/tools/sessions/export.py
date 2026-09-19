import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile

from voice_tools.core.files import new_output, sha256, write_json
from .pcap import base_command
from .store import connect, show


def dependency_filter(command, selected, packets):
    """Select frames plus the transitive IP/TCP reassembly dependencies.

    TShark's -2/-Y alone does not retain those frames in every supported version.
    Keep a bounded dependency graph and use compact frame ranges for the writer.
    """
    with tempfile.TemporaryFile(mode='w+') as table:
        def rows(display, fields):
            table.seek(0); table.truncate()
            args = command + ['-2', '-c', str(packets), '-Y', display, '-T', 'fields',
                              '-E', 'separator=/t', '-E', 'occurrence=a', '-E', 'aggregator=,']
            for field in fields:
                args += ['-e', field]
            result = subprocess.run(args, stdout=table, stderr=subprocess.PIPE, text=True, timeout=180)
            if result.returncode:
                raise ValueError('TShark 导出依赖解析失败：' + result.stderr[-2000:])
            table.seek(0)
            return table
        wanted = set()
        for line in rows(selected, ['frame.number']):
            wanted.add(int(line.strip()))
            if len(wanted) > 1000000:
                raise ValueError('会话导出超过 100 万帧，请缩短窗口')
        if not wanted:
            return None, 0
        dependencies, edges = {}, 0
        for line in rows('ip.fragment || ipv6.fragment || tcp.segment',
                         ['frame.number', 'ip.fragment', 'ipv6.fragment', 'tcp.segment']):
            parts = line.rstrip('\n').split('\t')
            values = {int(n) for part in parts[1:] for n in part.split(',') if n}
            values.discard(int(parts[0]))
            dependencies[int(parts[0])] = values
            edges += len(values)
            if edges > 1000000:
                raise ValueError('重组依赖超过 100 万条，请缩短窗口')
        pending = list(wanted)
        while pending:
            for frame in dependencies.get(pending.pop(), ()):
                if frame not in wanted:
                    wanted.add(frame); pending.append(frame)
        ranges = []
        first = last = None
        for frame in sorted(wanted):
            if last is not None and frame != last + 1:
                ranges.append(str(first) if first == last else f'{first}..{last}')
                first = None
            if first is None:
                first = frame
            last = frame
        ranges.append(str(first) if first == last else f'{first}..{last}')
        expression = 'frame.number in {' + ','.join(ranges) + '}'
        if len(expression) > 60000:
            raise ValueError('会话帧范围过于零散，请缩短窗口后导出')
        return expression, len(wanted)


def endpoint_filter(endpoint):
    address = ipaddress.ip_address(endpoint['ip'])
    port = int(endpoint['port'])
    if address.is_unspecified or not 1 <= port <= 65535:
        raise ValueError('无效媒体端点')
    protocol = 'ip' if address.version == 4 else 'ipv6'
    return f'(({protocol}.src == {address} && udp.srcport == {port}) || ({protocol}.dst == {address} && udp.dstport == {port}))'


def export(index, call_id, output, include_media=False, padding=2, tshark='tshark'):
    if not 0 <= padding <= 60:
        raise ValueError('媒体窗口扩展为 0–60 秒')
    call = show(index, call_id)
    times = [row['epoch'] for row in call['observations'] if row['epoch'] is not None]
    if not times:
        raise ValueError('会话没有可用时间戳')
    first, last = min(times), max(times)
    endpoints, flows = [], []
    terminal = any(row['data'].get('method') in ('BYE', 'CANCEL') or
                   row['data'].get('event') in ('CHANNEL_HANGUP_COMPLETE', 'CHANNEL_DESTROY')
                   for row in call['observations'])
    for row in call['observations']:
        endpoints.extend(row['data'].get('endpoints', []))
        if row['data'].get('flow'):
            flows.append((row['host'], row['epoch'], row['data']['flow'], row['data'].get('window_seconds', 10)))
    grouped = {}
    for host, when, f, window in flows:
        if when is None:
            continue
        key = (host, f['local_ip'], int(f['local_port']), f['remote_ip'], int(f['remote_port']))
        grouped.setdefault(key, []).append((when-window-padding, when+window+padding))
    flow_windows = []
    for key, windows in grouped.items():
        merged = []
        for a,b in sorted(windows):
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1],b)
            else:
                merged.append([a,b])
        flow_windows.extend((key,a,b) for a,b in merged)
    with connect(index) as db:
        sources = [dict(row) for row in db.execute("SELECT * FROM sources WHERE kind='pcap' AND status='ok' ORDER BY path")]
    output = new_output(output)
    output.chmod(0o700)
    manifest = {'schema_version': '1.0', 'tool': 'sessions-export', 'call_id': call_id, 'include_media': include_media,
                'files': [], 'errors': [], 'partial': call['partial'], 'index_status': call['index_status'],
                'warnings': ['SIP 以精确 Call-ID 选择。媒体由 SDP 声明端点或 FS 取样四元组与时间窗匹配，仅为候选。',
                             '端口重用、NAT、加密 SIP、漏掉 BYE、时钟偏差可能混入或遗漏媒体；未按号码合并或跨主机去重。']}
    # Signaling uses the indexed packet prefix, never unseen packets beyond a partial index.
    signal = 'sip.Call-ID == ' + json.dumps(call_id, ensure_ascii=False)
    for source in sources:
        stages = [stage for stage in call.get('media_timeline', []) if stage['host'] == source['host']]
        if source['end'] is None or (not stages and source['end'] < first - padding):
            continue
        if terminal and not stages and source['start'] > last + padding:
            continue
        media, ports = [], set()
        if include_media:
            until = min(source['end'], last + padding) if terminal else source['end']
            for stage in stages:
                begin = max(source['start'], stage['from_epoch'] - padding)
                finish = min(source['end'], stage['until_epoch'] + padding if stage['until_epoch'] is not None else source['end'])
                if begin > finish:
                    continue
                expressions = []
                if stage.get('flow'):
                    f = stage['flow']
                    a = endpoint_filter({'ip': f['local_ip'], 'port': f['local_port']})
                    b = endpoint_filter({'ip': f['remote_ip'], 'port': f['remote_port']})
                    expressions.append(f'({a} && {b})')
                    ports.update((f['local_port'], f['remote_port']))
                elif stage.get('media'):
                    m = stage['media']
                    expressions.append(endpoint_filter(m)); ports.add(m['port'])
                    if m.get('rtcp'):
                        expressions.append(endpoint_filter(m['rtcp']))
                for expression in expressions:
                    media.append(f'({expression} && frame.time_epoch >= {begin:.6f} && frame.time_epoch <= {finish:.6f})')
            if not stages:  # Explicit compatibility with an index written before media stages existed.
                for endpoint in endpoints:
                    media.append(f'({endpoint_filter(endpoint)} && frame.time_epoch >= {first-padding:.6f} && frame.time_epoch <= {until:.6f})')
                    ports.add(int(endpoint['port']))
                for (host, local_ip, local_port, remote_ip, remote_port), a_time, b_time in flow_windows:
                    if host != source['host']:
                        continue
                    a = endpoint_filter({'ip': local_ip, 'port': local_port})
                    b = endpoint_filter({'ip': remote_ip, 'port': remote_port})
                    media.append(f'({a} && {b} && frame.time_epoch >= {a_time:.6f} && frame.time_epoch <= {b_time:.6f})')
                    ports.update((local_port, remote_port))
        selected = '(' + signal + ')' + ((' || ' + ' || '.join(dict.fromkeys(media))) if media else '')
        if len(selected) > 100000:
            manifest['errors'].append({'source': source['path'], 'error': '端点或变化过多，请缩小索引时间段'})
            continue
        target = output / f'{len(manifest["files"])+1:04d}.pcapng'
        try:
            if sha256(source['path']) != source['sha256']:
                raise ValueError('源 PCAP 已变化，拒绝使用旧索引导出')
            meta = json.loads(source['metadata'])
            for original in meta.get('originals', []):
                if sha256(original['path']) != original['sha256']:
                    raise ValueError('源分片已变化，拒绝使用旧索引导出')
            command = base_command(source['path'], tshark, meta.get('sip_ports', [5060]))
            if not meta['packets']:
                continue
            frames, count = dependency_filter(command, selected, meta['packets'])
            if frames is None:
                continue
            command += ['-c', str(meta['packets']), '-Y', frames, '-w', str(target)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError('tshark 会话导出失败：' + result.stderr[-2000:])
            manifest['files'].append({'file': target.name, 'sha256': sha256(target), 'host': source['host'],
                                      'packets': count, 'reassembly_dependencies_included': True,
                                      'source': source['path'], 'source_sha256': source['sha256'], 'display_filter': selected,
                                      'rtp_ports': sorted(ports),
                                      'sensor_id': meta.get('sensor_id') or 'file:' + source['path'],
                                      'media_timeline': [s for s in call.get('media_timeline', []) if s['host'] == source['host']],
                                      'rtcp_ports': sorted({s['media']['rtcp']['port'] for s in call.get('media_timeline', []) if s['host'] == source['host'] and s.get('media', {}).get('rtcp')}),
                                      'original_sources': meta.get('originals', []), 'media_evidence': 'candidate' if media else 'signaling_only',
                                      'source_index_limited': meta.get('limited', False)})
            manifest['partial'] |= meta.get('limited', False)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            manifest['errors'].append({'source': source['path'], 'error': str(error)})
    manifest['partial'] |= bool(manifest['errors']) or not manifest['files']
    if not terminal or any(s['until_epoch'] is None for s in call.get('media_timeline', [])):
        manifest['warnings'].append('至少一个媒体阶段未观测到结束；其窗口延至源文件结尾，端口重用误关联风险更高。')
    write_json(output / 'session.json', manifest)
    return manifest
