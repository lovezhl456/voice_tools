"""Bounded RFC 3550 SR/RR and RFC 3611 XR parsing; reports are endpoint statements."""
import struct


def parse_compound(raw, when):
    reports, offset = [], 0
    while offset < len(raw):
        if len(raw) - offset < 4 or raw[offset] >> 6 != 2:
            raise ValueError('Invalid RTCP header')
        first, kind, words = struct.unpack('!BBH', raw[offset:offset+4])
        size = (words + 1) * 4
        if not 192 <= kind <= 223 or size < 4 or offset + size > len(raw):
            raise ValueError('Invalid RTCP packet length')
        block = raw[offset:offset+size]
        if first & 32:
            if offset + size != len(raw) or not block[-1] or block[-1] > size - 4:
                raise ValueError('Invalid RTCP padding')
            block = block[:-block[-1]]
        row = {'packet_type': kind, 'epoch': when, 'report_blocks': []}
        count = first & 31
        if kind in (200, 201):
            minimum = 28 if kind == 200 else 8
            if len(block) < minimum + count * 24:
                raise ValueError('Truncated RTCP SR/RR')
            row['sender_ssrc'] = int.from_bytes(block[4:8], 'big')
            if kind == 200:
                msw, lsw, stamp, packets, octets = struct.unpack('!IIIII', block[8:28])
                row.update(ntp_seconds=msw + lsw / 2**32, ntp_unix_epoch=msw - 2208988800 + lsw / 2**32,
                           ntp_middle32=((msw & 65535) << 16) | (lsw >> 16), rtp_timestamp=stamp,
                           sender_packet_count=packets, sender_octet_count=octets)
            for i in range(count):
                part = block[minimum + i*24:minimum + (i+1)*24]
                source, highest, jitter, lsr, dlsr = (int.from_bytes(part[a:a+4], 'big') for a in (0, 8, 12, 16, 20))
                row['report_blocks'].append({'ssrc': source, 'fraction_lost': part[4] / 256,
                    'cumulative_lost': int.from_bytes(part[5:8], 'big', signed=True), 'extended_highest_sequence': highest,
                    'jitter_timestamp_units': jitter, 'last_sr': lsr, 'delay_since_last_sr_s': dlsr / 65536})
        elif kind == 207:
            if len(block) < 8:
                raise ValueError('Truncated RTCP XR')
            row['sender_ssrc'] = int.from_bytes(block[4:8], 'big'); row['xr_blocks'] = []
            at = 8
            while at < len(block):
                if at + 4 > len(block):
                    raise ValueError('Truncated XR block')
                bt, specific, length = struct.unpack('!BBH', block[at:at+4]); length = (length + 1) * 4
                if at + length > len(block):
                    raise ValueError('Invalid XR block length')
                x = {'block_type': bt, 'length': length}
                if bt == 7 and length == 36:
                    payload = block[at+4:at+36]
                    x.update(ssrc=int.from_bytes(payload[:4], 'big'), loss_rate=payload[4]/256,
                             discard_rate=payload[5]/256, round_trip_delay_ms=int.from_bytes(payload[12:14], 'big'),
                             endpoint_mos_lq=None if payload[22] == 127 else payload[22]/10,
                             endpoint_mos_cq=None if payload[23] == 127 else payload[23]/10)
                row['xr_blocks'].append(x); at += length
        else:
            row['description'] = {202: 'SDES', 203: 'BYE', 204: 'APP', 205: 'RTPFB', 206: 'PSFB'}.get(kind, 'other RTCP')
        reports.append(row); offset += size
    if not reports:
        raise ValueError('Empty RTCP compound')
    return reports


def enrich(reports, clocks):
    senders = {}
    for report in reports:
        direction = tuple(report.get(key) for key in ('src', 'src_port', 'dst', 'dst_port'))
        if report['packet_type'] == 200:
            senders[(report['sender_ssrc'], report['ntp_middle32'], direction)] = report['epoch']
        for row in report['report_blocks']:
            rate = clocks.get(row['ssrc'])
            row['jitter_ms'] = row['jitter_timestamp_units'] / rate * 1000 if rate else None
            reverse = direction[2:] + direction[:2]
            observed = senders.get((row['ssrc'], row['last_sr'], reverse))
            if observed is not None and row['last_sr']:
                estimate = report['epoch'] - observed - row['delay_since_last_sr_s']
                if 0 <= estimate <= 600:
                    row['capture_point_rtt_estimate_ms'] = estimate * 1000
    return reports
