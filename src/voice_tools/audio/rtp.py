"""G.711 RTP reconstruction, independent of audioop (removed in Python 3.13)."""
from array import array
import sys
import wave


def decode_g711(payload, codec):
    values = array('h')
    for byte in payload:
        if codec == 'PCMU':
            value = (~byte) & 255
            sample = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
            sample -= 132
            values.append(-sample if value & 128 else sample)
        elif codec == 'PCMA':
            value = byte ^ 85
            sample = (value & 15) << 4
            exponent = (value >> 4) & 7
            sample += 8 if exponent == 0 else 264
            if exponent > 1:
                sample <<= exponent - 1
            values.append(sample if value & 128 else -sample)
        else:
            raise ValueError('仅支持 PCMU/PCMA G.711 重建')
    if sys.byteorder != 'little':
        values.byteswap()
    return values.tobytes()


def parse_rtp(raw):
    import struct
    if len(raw) < 12 or raw[0] >> 6 != 2:
        return None
    offset = 12 + (raw[0] & 15) * 4
    if offset > len(raw):
        return None
    if raw[0] & 16:
        if offset + 4 > len(raw):
            return None
        offset += 4 + int.from_bytes(raw[offset + 2:offset + 4], 'big') * 4
    end = len(raw)
    if raw[0] & 32:
        if not raw[-1] or raw[-1] > end - offset:
            return None
        end -= raw[-1]
    if offset > end:
        return None
    seq, timestamp, ssrc = struct.unpack('!HII', raw[2:12])
    return {'sequence': seq, 'timestamp': timestamp, 'ssrc': ssrc, 'pt': raw[1] & 127, 'payload': raw[offset:end]}


def segments(packets):
    result, current, highest = [], [], None
    for packet in packets:
        seq = packet['sequence']
        extended = seq if highest is None else highest + ((seq - highest + 32768) % 65536 - 32768)
        if highest is not None and abs(extended - highest) > 3000:
            result.append(current)
            current = []
            highest = None
            extended = seq
        current.append({**packet, 'extended_sequence': extended})
        highest = extended if highest is None else max(highest, extended)
    if current:
        result.append(current)
    return result


def reconstruct(packets, output, stem, budget_bytes, max_seconds=3600):
    """Write payload and RTP-timestamp WAVs; silence is zero fill, never PLC."""
    unique = {}
    duplicates = conflicting = unsupported = 0
    for packet in packets:
        key = packet['extended_sequence']
        if key in unique:
            duplicates += 1
            conflicting += any(unique[key].get(field) != packet.get(field)
                               for field in ('payload', 'timestamp', 'pt', 'encrypted', 'codec'))
        else:
            unique[key] = packet
    decoded = []
    codecs = set()
    for packet in sorted(unique.values(), key=lambda p: p['extended_sequence']):
        codec = packet.get('codec', {})
        if codec.get('name') not in ('PCMU', 'PCMA') or codec.get('clock_rate') != 8000 or codec.get('channels', 1) != 1 or packet.get('encrypted'):
            unsupported += 1
            continue
        codecs.add(codec['name'])
        decoded.append((packet, decode_g711(packet['payload'], codec['name'])))
    info = {'sample_rate': 8000, 'channels': 1, 'codecs': sorted(codecs), 'duplicates_ignored': duplicates,
            'conflicting_duplicates': conflicting, 'unsupported_packets': unsupported, 'files': [],
            'evidence': 'decoded_assuming_plaintext_rtp', 'warnings': [
                'payload 模式移除时间间隙；timestamp 模式按 RTP 时间戳补零，不模拟终端 PLC、抖动缓冲或真实播放。']}
    if not decoded:
        info.update(status='unsupported', reason='没有可解码的明文单声道 G.711；不把 SRTP 或未知编码伪装为音频')
        return info, 0
    first = decoded[0][0]['timestamp']
    placements = [(((p['timestamp'] - first + 2**31) % 2**32 - 2**31), pcm) for p, pcm in decoded]
    if any(pos < 0 for pos, _ in placements) or any(b[0] < a[0] for a, b in zip(placements, placements[1:])):
        info.update(status='unsupported', reason='RTP 时间戳回退，不能可靠重建时间轴')
        return info, 0
    samples = max(pos + len(pcm) // 2 for pos, pcm in placements)
    payload_bytes = sum(len(pcm) for _, pcm in placements)
    required = samples * 2 + payload_bytes + 88
    if samples > max_seconds * 8000 or required > budget_bytes:
        info.update(status='limited', reason='音频重建超过时长或报告总字节预算')
        return info, 0
    output.mkdir(exist_ok=True)
    silence, overlap = 0, 0
    for mode in ('payload', 'timestamp'):
        target = output / f'{stem}-{mode}.wav'
        with wave.open(str(target), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            cursor = 0
            for pos, pcm in placements:
                if mode == 'payload':
                    stream.writeframesraw(pcm)
                    continue
                if pos > cursor:
                    gap = pos - cursor
                    silence += gap
                    while gap:
                        size = min(gap, 8000)
                        stream.writeframesraw(b'\x00' * (size * 2))
                        gap -= size
                    cursor = pos
                if pos < cursor:
                    cut = min(cursor - pos, len(pcm) // 2)
                    overlap += cut
                    pcm = pcm[cut * 2:]
                stream.writeframesraw(pcm)
                cursor += len(pcm) // 2
        target.chmod(0o600)
        info['files'].append({'file': target.name, 'mode': mode, 'bytes': target.stat().st_size,
                              'duration_s': (payload_bytes // 2 if mode == 'payload' else samples) / 8000})
    info.update(status='partial' if unsupported or conflicting else 'ok', inserted_silence_samples=silence,
                trimmed_overlap_samples=overlap, first_rtp_timestamp=first)
    return info, required
