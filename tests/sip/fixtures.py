"""Synthetic, non-personal SIP/RTP/WAV fixtures."""
import math
import socket
import struct
import wave
from pathlib import Path


def tone(path, seconds=0.4):
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        out.writeframes(b"".join(struct.pack("<h", round(10000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(round(seconds * 8000))))
    return Path(path)


def rtp(seq, stamp, payload, pt=8, ssrc=1234):
    return struct.pack("!BBHII", 0x80, pt, seq & 65535, stamp & 0xffffffff, ssrc) + payload


def udp_packet(src, sport, dst, dport, payload):
    udp = struct.pack("!HHHH", sport, dport, len(payload) + 8, 0) + payload
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, len(udp) + 20, 1, 0, 64, 17, 0, socket.inet_aton(src), socket.inet_aton(dst))
    checksum = sum(struct.unpack("!10H", header))
    while checksum >> 16:
        checksum = (checksum & 65535) + (checksum >> 16)
    header = header[:10] + struct.pack("!H", (~checksum) & 65535) + header[12:]
    return header + udp


class CaptureWriter:
    def __init__(self, path):
        self.file = Path(path).open("wb")
        # DLT_RAW = 101, IPv4 packets. Microsecond PCAP, no personal network data.
        self.file.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 101))

    def write(self, when, src, sport, dst, dport, payload):
        packet = udp_packet(src, sport, dst, dport, payload)
        sec, usec = int(when), round((when % 1) * 1000000)
        if usec >= 1000000:
            sec, usec = sec + 1, 0
        self.file.write(struct.pack("<IIII", sec, usec, len(packet), len(packet)) + packet)
        self.file.flush()

    def close(self):
        self.file.close()


def capture(path):
    out = CaptureWriter(path)
    seq = 1
    for i in range(10):
        if i != 5:  # One missing 20-ms packet.
            out.write(1000 + i * .02, "127.0.0.1", 4000, "127.0.0.1", 5000, rtp(seq, i * 160, b"\xd5" * 160))
        seq += 1
    for i, (duration, end) in enumerate(((400, 0), (800, 0), (1280, 1), (1280, 1), (1280, 1))):
        payload = struct.pack("!BBH", 1, 10 | (end << 7), duration)
        out.write(1000.2 + i * .04, "127.0.0.1", 4000, "127.0.0.1", 5000, rtp(seq, 1600, payload, 101))
        seq += 1
    for i in range(10):
        out.write(1000.4 + i * .02, "127.0.0.1", 4000, "127.0.0.1", 5000, rtp(seq, 2880 + i * 160, b"\x55" * 160))
        seq += 1
    out.write(1000.6, "127.0.0.1", 5000, "127.0.0.1", 4000, rtp(1, 0, b"\xab" * 160, ssrc=9876))
    out.close()
    return Path(path)
