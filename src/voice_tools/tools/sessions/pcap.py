"""逐报文解析 SIP/SDP；保留每个 TCP 帧中多个 SIP 消息的边界。"""
import ipaddress
import math
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


def sdp_media(sip):
    result = []
    for proto in sip.iter('proto'):
        if proto.get('name') != 'sdp':
            continue
        session_ip, current, session_attrs, media = None, None, [], []
        for f in proto.iter('field'):
            name, show = f.get('name'), f.get('show', '')
            if name == 'sdp.media':
                parts = show.split()
                current = {'kind': parts[0] if parts else '', 'ip': session_ip, 'port': 0,
                           'protocol': parts[2] if len(parts) > 2 else '', 'payload_types': [],
                           'codecs': {}, 'attributes': list(session_attrs), 'direction': 'sendrecv'}
                if len(parts) > 3:
                    current['payload_types'] = [int(x) for x in parts[3:] if x.isdigit() and 0 <= int(x) <= 127]
                media.append(current)
            elif name == 'sdp.connection_info.address':
                try:
                    address = str(ipaddress.ip_address(show.split('/')[0]))
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
            elif name in ('sdp.media_attr', 'sdp.session_attr'):
                if current is None:
                    session_attrs.append(show)
                else:
                    current['attributes'].append(show)
        for item in media:
            if item['kind'] != 'audio' or not item['ip'] or not 0 <= item['port'] <= 65535:
                continue
            for attr in item.pop('attributes'):
                if attr in ('inactive', 'sendonly', 'recvonly', 'sendrecv'):
                    item['direction'] = attr
                if attr == 'rtcp-mux':
                    item['rtcp_mux'] = True
                if attr.startswith('rtpmap:'):
                    try:
                        pt, codec = attr[7:].split(None, 1)
                        parts = codec.split('/')
                        item['codecs'][pt] = {'name': parts[0].upper(), 'clock_rate': int(parts[1]),
                                             'channels': int(parts[2]) if len(parts) > 2 else 1}
                    except (ValueError, IndexError):
                        pass
                if attr.startswith('fmtp:'):
                    parts = attr[5:].split(None, 1)
                    if len(parts) == 2:
                        item.setdefault('fmtp', {})[parts[0]] = parts[1][:2048]
                if attr.startswith('ptime:'):
                    item['ptime'] = attr[6:][:16]
                if attr.startswith('rtcp:'):
                    parts = attr[5:].split()
                    try:
                        port = int(parts[0]); addr = str(ipaddress.ip_address(parts[3])) if len(parts) >= 4 else item['ip']
                        if 1 <= port <= 65535:
                            item['rtcp'] = {'ip': addr, 'port': port}
                    except (ValueError, IndexError):
                        pass
            if item.get('rtcp_mux'):
                item['rtcp'] = {'ip': item['ip'], 'port': item['port']}
            if ipaddress.ip_address(item['ip']).is_unspecified:
                item['direction'] = 'inactive'
            result.append(item)
    return result


def sdp_endpoints(sip):
    return [{'ip': m['ip'], 'port': m['port'], 'basis': 'sdp_advertised'} for m in sdp_media(sip)
            if m['port'] and not ipaddress.ip_address(m['ip']).is_unspecified]


def scan(path, consume, sip_ports=(5060,), max_packets=1000000, tshark='tshark'):
    path = Path(path)
    if not 1 <= max_packets <= 5000000 or path.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ValueError("每份索引 PCAP/连续分片组最大 2 GiB，包数上限 1–5000000；请缩短抓包分片")
    base = base_command(path, tshark, sip_ports) + ['-c', str(max_packets + 1)]
    with tempfile.TemporaryDirectory(prefix='voice-session-index-') as temp:
        timeline = Path(temp) / 'times.tsv'
        execute(base + ['-T', 'fields', '-e', 'frame.time_epoch', '-e', 'frame.cap_len', '-e', 'frame.len'], timeline)
        first = last = None
        count = truncated = 0
        with timeline.open() as stream:
            for line in stream:
                count += 1
                if count > max_packets:
                    break
                when, captured, wire = line.strip().split('\t')
                timestamp = float(when)
                if not math.isfinite(timestamp):
                    raise ValueError('PCAP 时间戳无效')
                truncated += int(captured) < int(wire)
                first = timestamp if first is None else min(first, timestamp)
                last = timestamp if last is None else max(last, timestamp)
        summary = {'packets': min(count, max_packets), 'first_epoch': first, 'last_epoch': last,
                   'limited': count > max_packets, 'sip_messages': 0, 'truncated_packets': truncated}
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
                         'endpoints': sdp_endpoints(sip), 'media': sdp_media(sip),
                         'from_tag': value(sip, 'sip.from.tag'), 'to_tag': value(sip, 'sip.to.tag'),
                         'cseq': value(sip, 'sip.CSeq.seq'), 'cseq_method': value(sip, 'sip.CSeq.method')})
                summary['sip_messages'] += 1
            packet.clear()
    return summary
