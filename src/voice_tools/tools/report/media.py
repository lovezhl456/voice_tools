"""RTP payload and RTCP analysis on one sensor's continuous packet timeline."""
import csv
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from voice_tools.audio.rtp import parse_rtp, reconstruct, segments
from voice_tools.core.files import sha256
from .rtcp import parse_compound, enrich

FIELDS = ('frame.time_epoch', 'ip.src', 'ipv6.src', 'udp.srcport', 'ip.dst', 'ipv6.dst',
          'udp.dstport', 'udp.payload', 'rtp.ssrc', 'rtcp.pt')


def codec_for(packet, src, sport, dst, dport, when, mappings):
    result = {'name': {0: 'PCMU', 8: 'PCMA'}.get(packet['pt'], 'unknown'), 'clock_rate': 8000, 'channels': 1}
    encrypted = False
    matches = []
    for item in mappings:
        media = item.get('media', {})
        if when < item.get('from_epoch', 0) or (item.get('until_epoch') is not None and when >= item['until_epoch']):
            continue
        # An observed SAVP endpoint protects both directions. The other SDP leg
        # may be missing; its absence is not evidence that those bytes are plain.
        endpoint = (media.get('ip'), media.get('port'))
        if endpoint in ((src, sport), (dst, dport)) and 'SAVP' in media.get('protocol', '').upper():
            encrypted = True
        # Codec maps still describe the receiving endpoint, not every stream.
        if endpoint != (dst, dport):
            continue
        matches.append(item)
    if matches:
        selected = max(matches, key=lambda item: item.get('from_epoch', 0))
        media = selected['media']
        result = media.get('codecs', {}).get(str(packet['pt']), result)
    return result, encrypted


def analyze(path, rtp_ports=(), rtcp_ports=(), mappings=(), output=None, prefix='rtp', max_packets=250000,
            tshark='tshark', audio_budget=256*1048576, clocks=None):
    executable = shutil.which(tshark)
    if not executable:
        raise ValueError('需要 tshark')
    args = [executable, '-n', '-r', str(path), '-c', str(max_packets), '-Y', 'udp',
            '-T', 'fields', '-E', 'separator=/t', '-E', 'occurrence=f']
    # Wireshark's RTP dissector recognizes RTCP on a negotiated mux port.
    for port in sorted(set(rtp_ports)):
        args += ['-d', f'udp.port=={port},rtp']
    for port in sorted(set(rtcp_ports) - set(rtp_ports)):
        args += ['-d', f'udp.port=={port},rtcp']
    for field in FIELDS:
        args += ['-e', field]
    streams, reports, malformed, bytes_kept, limited = {}, [], 0, 0, False
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, text=True)
        timer = threading.Timer(180, process.kill); timer.daemon = True; timer.start()
        try:
            for row in csv.reader(process.stdout, delimiter='\t', quoting=csv.QUOTE_NONE):
                if len(row) != len(FIELDS):
                    raise ValueError('TShark 媒体字段不完整')
                when, src4, src6, sport, dst4, dst6, dport, payload, rtp_ssrc, rtcp_type = row
                if not payload or not sport or not dport:
                    continue
                try:
                    raw = bytes.fromhex(payload.replace(':', ''))
                    when, sport, dport = float(when), int(sport), int(dport)
                except ValueError:
                    malformed += 1; continue
                src, dst = src4 or src6, dst4 or dst6
                if rtcp_type or (len(raw) >= 4 and raw[0] >> 6 == 2 and 192 <= raw[1] <= 223):
                    try:
                        parsed = parse_compound(raw, when)
                        if len(reports) + len(parsed) <= 10000:
                            reports.extend(dict(item, src=src, src_port=sport, dst=dst, dst_port=dport) for item in parsed)
                        else:
                            limited = True
                        continue
                    except ValueError:
                        if rtcp_type or sport in rtcp_ports or dport in rtcp_ports:
                            malformed += 1; continue
                if output is None or not (rtp_ssrc or sport in rtp_ports or dport in rtp_ports):
                    continue
                packet = parse_rtp(raw)
                if not packet:
                    continue
                key = (src, sport, dst, dport, packet['ssrc'])
                if (key not in streams and len(streams) >= 32) or bytes_kept + len(raw) > 64*1048576:
                    limited = True; continue
                packet['codec'], packet['encrypted'] = codec_for(packet, src, sport, dst, dport, when, mappings)
                packet['epoch'] = when
                streams.setdefault(key, []).append(packet); bytes_kept += len(raw)
            rc = process.wait()
            if rc:
                errors.seek(0)
                raise ValueError('TShark 媒体分析失败或超时：' + errors.read(2000).decode('utf-8', 'replace'))
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill(); process.wait()
            process.stdout.close()
    audio, used = [], 0
    if output is not None:
        for i, (key, packets) in enumerate(streams.items(), 1):
            for segment, group in enumerate(segments(packets), 1):
                info, consumed = reconstruct(group, Path(output), f'{prefix}-{i:03d}-{segment:03d}', audio_budget-used)
                used += consumed
                info.update(src=key[0], src_port=key[1], dst=key[2], dst_port=key[3], ssrc=key[4], segment=segment)
                for file in info['files']:
                    file['sha256'] = sha256(Path(output) / file['file'])
                    file['playback'] = 'rtp-audio/' + file['file']
                audio.append(info)
    return {'rtcp': enrich(reports, clocks or {}), 'rtp_audio': audio, 'audio_bytes': used,
            'malformed_control_packets': malformed,
            'partial': limited or bool(malformed) or any(a['status'] != 'ok' for a in audio),
            'warnings': ['RTCP 是端点报告，不等于抓包点实测；未知 RTP 时钟时 jitter 保留时间戳单位。',
                         'RTT 仅在同采集点匹配 SR/RR 后估算，不作为单向网络时延。',
                         'XR 中 MOS 是端点上报值，未在本工具内生成质量评分。']}
