import ipaddress
import json
from pathlib import Path
import subprocess

from voice_tools.core.files import new_output, sha256, write_json
from .pcap import base_command
from .store import connect, show


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
    terminal = any(row['data'].get('method') in ('BYE', 'CANCEL') for row in call['observations'])
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
        if source['end'] is None or source['end'] < first - padding:
            continue
        if terminal and source['start'] > last + padding:
            continue
        media, ports = [], set()
        if include_media:
            until = min(source['end'], last + padding) if terminal else source['end']
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
            command = base_command(source['path'], tshark, meta.get('sip_ports', [5060]))
            command += ['-c', str(meta['packets']), '-Y', selected, '-w', str(target), '-P', '-T', 'fields', '-e', 'frame.number']
            if not meta['packets']:
                continue
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError('tshark 会话导出失败：' + result.stderr[-2000:])
            if not result.stdout.strip():
                target.unlink()
                continue
            manifest['files'].append({'file': target.name, 'sha256': sha256(target), 'host': source['host'],
                                      'packets': len(result.stdout.splitlines()),
                                      'source': source['path'], 'source_sha256': source['sha256'], 'display_filter': selected,
                                      'rtp_ports': sorted(ports), 'media_evidence': 'candidate' if media else 'signaling_only',
                                      'source_index_limited': meta.get('limited', False)})
            manifest['partial'] |= meta.get('limited', False)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            manifest['errors'].append({'source': source['path'], 'error': str(error)})
    manifest['partial'] |= bool(manifest['errors']) or not manifest['files']
    if not terminal:
        manifest['warnings'].append('没有观测到 BYE/CANCEL；SDP 媒体窗口延至各源文件结尾，端口重用误关联风险更高。')
    write_json(output / 'session.json', manifest)
    return manifest
