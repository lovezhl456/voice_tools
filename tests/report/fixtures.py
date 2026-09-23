"""完全合成的离线 RTP/音频，不包含真实通话。"""
import socket
import struct
from pathlib import Path
import numpy as np
from voice_tools.audio.io import write_wav


def make_pcap(path, sequence=(65534, 65535, 0, 2, 1, 2, 4), pt=0):
    data = bytearray(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1))
    for index, seq in enumerate(sequence):
        stamp = ((seq - sequence[0]) % 65536) * 160
        rtp = struct.pack('!BBHII', 0x80, pt, seq, stamp, 0x12345678) + b'\xff' * 160
        udp = struct.pack('!HHHH', 16000, 24000, len(rtp) + 8, 0) + rtp
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(udp) + 20, index, 0, 64, 17, 0,
                         socket.inet_aton('192.0.2.1'), socket.inet_aton('192.0.2.2'))
        packet = bytes.fromhex('00112233445566778899aabb0800') + ip + udp
        data += struct.pack('<IIII', 1700000000, index * 20000, len(packet), len(packet)) + packet
    Path(path).write_bytes(data)


def make_audio(path, silent=False):
    rate = 8000
    t = np.arange(rate * 3) / rate
    a = (.15 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    a[:rate] = 0
    b = np.zeros_like(a) if silent else a * .5
    write_wav(path, np.column_stack([a, b]), rate)


def rtp(seq, stamp=None, pt=0, payload=None, ssrc=111):
    """Return one synthetic RTP packet for report and capture tests."""
    header = struct.pack('!BBHII', 0x80, pt, seq, seq * 160 if stamp is None else stamp, ssrc)
    return header + (b'\xff' * 160 if payload is None else payload)
