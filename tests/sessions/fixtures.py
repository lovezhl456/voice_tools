import socket
import struct
from pathlib import Path

T0 = 1789459200
CALL_A, CALL_B = 'call-a@example.net', 'call-b@example.net'


def sip(call_id, method='INVITE', port=16000, ip='192.0.2.1'):
    sdp = f'v=0\r\no=- 1 1 IN IP4 {ip}\r\ns=demo\r\nc=IN IP4 {ip}\r\nt=0 0\r\nm=audio {port} RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\n' if port else ''
    first = 'SIP/2.0 200 OK' if method == '200' else f'{method} sip:1002@example.net SIP/2.0'
    return (first + f'\r\nVia: SIP/2.0/UDP 192.0.2.1:5060;branch=z9hG4bK-demo\r\nCall-ID: {call_id}\r\n'
            'From: <sip:1001@example.net>;tag=a\r\nTo: <sip:1002@example.net>;tag=b\r\n'
            f'CSeq: 1 {"INVITE" if method == "200" else method}\r\nContent-Type: application/sdp\r\n'
            f'Content-Length: {len(sdp.encode())}\r\n\r\n{sdp}').encode()


def packet(payload, sport=5060, dport=5060, src='192.0.2.1', dst='192.0.2.2', tcp=False):
    if tcp:
        segment = struct.pack('!HHIIBBHHH', sport, dport, 1, 1, 5 << 4, 0x18, 65535, 0, 0) + payload
    else:
        segment = struct.pack('!HHHH', sport, dport, len(payload)+8, 0) + payload
    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(segment)+20, 1, 0, 64, 6 if tcp else 17, 0,
                         socket.inet_aton(src), socket.inet_aton(dst))
    return bytes.fromhex('00112233445566778899aabb0800') + header + segment


def pcap(path, rows=None):
    if rows is None:
        rows = [(0, packet(sip(CALL_A))), (.01, packet(sip(CALL_B, port=16002))),
                (.02, packet(sip(CALL_A, '200', 24000, '192.0.2.2'), src='192.0.2.2', dst='192.0.2.1')),
                (.03, packet(sip(CALL_B, '200', 24002, '192.0.2.2'), src='192.0.2.2', dst='192.0.2.1'))]
        for i, seq in enumerate((1, 2, 4)):
            rtp = struct.pack('!BBHII', 0x80, 0, seq, seq*160, 111) + b'\xff'*160
            rows.append((.1+i*.02, packet(rtp, 16000, 24000)))
            other = struct.pack('!BBHII', 0x80, 0, i+1, (i+1)*160, 222) + b'\xff'*160
            rows.append((.11+i*.02, packet(other, 16002, 24002)))
        rows.extend([(1, packet(sip(CALL_A, 'BYE', 0))), (1.2, packet(sip(CALL_B, 'BYE', 0)))])
    data = bytearray(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
    for offset, payload in sorted(rows):
        whole = int(offset)
        data += struct.pack('<IIII', T0+whole, round((offset-whole)*1e6), len(payload), len(payload)) + payload
    Path(path).write_bytes(data)
    return bytes(data)
