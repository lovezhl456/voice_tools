"""逐报文解析 SIP/SDP；保留每个 TCP 帧中多个 SIP 消息的边界。"""
import ipaddress
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def execute(argv, output, timeout=180):
    with Path(output).open('wb') as stream, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(argv, stdout=stream, stderr=errors, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ValueError("tshark 操作超时，请缩小时间窗口或分片") from error
        errors.seek(0)
        message = errors.read(4000).decode('utf-8', errors='replace')
    if result.returncode:
        raise ValueError(f"tshark 操作失败 ({result.returncode})：{message}")


def base_command(path, tshark, sip_ports):
    executable = shutil.which(tshark)
    if not executable:
        raise ValueError("会话 PCAP 索引需要本机 tshark")
    args = [executable, '-n', '-r', str(path)]
    for port in sorted(set(sip_ports)):
        if not 1 <= int(port) <= 65535:
            raise ValueError("SIP 端口无效")
        for protocol in ('udp', 'tcp'):
            args += ['-d', f'{protocol}.port=={int(port)},sip']
    return args


def value(node, name):
    return next((f.get('show', '') for f in node.iter('field') if f.get('name') == name), '')


def sdp_endpoints(sip):
    endpoints = []
    for proto in sip.iter('proto'):
        if proto.get('name') != 'sdp':
            continue
        session_ip, current, media = None, None, []
        for f in proto.iter('field'):
            name, show = f.get('name'), f.get('show', '')
            if name == 'sdp.media':
                parts = show.split()
                current = {'kind': parts[0] if parts else '', 'ip': session_ip, 'port': None}
                media.append(current)
            elif name == 'sdp.connection_info.address':
                try:
                    addr = ipaddress.ip_address(show.split('/')[0])
                    address = str(addr) if not addr.is_unspecified else None
                except ValueError:
                    address = None
                if current is None:
                    session_ip = address
                else:
                    current['ip'] = address
            elif name == 'sdp.media.port' and current is not None:
                try:
                    current['port'] = int(show)
                except ValueError:
                    pass
        endpoints.extend({'ip': m['ip'], 'port': m['port'], 'basis': 'sdp_advertised'} for m in media
                         if m['kind'] == 'audio' and m['ip'] and m['port'] and 1 <= m['port'] <= 65535)
    return endpoints


def scan(path, consume, sip_ports=(5060,), max_packets=1000000, tshark='tshark'):
    path = Path(path)
    if not 1 <= max_packets <= 5000000 or path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("每份索引 PCAP 最大 512 MiB，包数上限 1–5000000；请缩短抓包分片")
    base = base_command(path, tshark, sip_ports) + ['-c', str(max_packets + 1)]
    with tempfile.TemporaryDirectory(prefix='voice-session-index-') as temp:
        timeline = Path(temp) / 'times.tsv'
        execute(base + ['-T', 'fields', '-e', 'frame.time_epoch'], timeline)
        first = last = None
        count = 0
        with timeline.open() as stream:
            for line in stream:
                count += 1
                if count > max_packets:
                    break
                timestamp = float(line.strip())
                first = timestamp if first is None else min(first, timestamp)
                last = timestamp if last is None else max(last, timestamp)
        summary = {'packets': min(count, max_packets), 'first_epoch': first, 'last_epoch': last,
                   'limited': count > max_packets, 'sip_messages': 0}
        xml = Path(temp) / 'sip.xml'
        execute(base_command(path, tshark, sip_ports) + ['-c', str(max_packets), '-Y', 'sip.Call-ID',
                                                       '-T', 'pdml', '-J', 'frame ip ipv6 tcp udp sip sdp'], xml)
        for _, packet in ET.iterparse(xml, events=('end',)):
            if packet.tag != 'packet':
                continue
            epoch = float(value(packet, 'frame.time_epoch'))
            for sip in packet.iter('proto'):
                if sip.get('name') != 'sip':
                    continue
                call_id = value(sip, 'sip.Call-ID')
                if not call_id:
                    continue
                consume({'call_id': call_id, 'epoch': epoch, 'caller': value(sip, 'sip.from.user'),
                         'callee': value(sip, 'sip.to.user'), 'method': value(sip, 'sip.Method'),
                         'status_code': value(sip, 'sip.Status-Code'), 'frame': int(value(packet, 'frame.number')),
                         'src': value(packet, 'ip.src') or value(packet, 'ipv6.src'),
                         'dst': value(packet, 'ip.dst') or value(packet, 'ipv6.dst'),
                         'endpoints': sdp_endpoints(sip)})
                summary['sip_messages'] += 1
            packet.clear()
    return summary
