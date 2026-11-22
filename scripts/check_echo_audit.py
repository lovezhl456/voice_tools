#!/usr/bin/env python3
"""Exercise the installed voice-tools CLI on deterministic audio and packet inputs."""
import argparse
import ipaddress
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import wave

RATE = 8000
EPOCH = 1800000000
PACKET = 160


def source_samples():
    randomizer = random.Random(7259)
    result = []
    previous = 0
    for _ in range(9 * RATE):
        previous = (previous + randomizer.randint(-20000, 20000)) // 2
        result.append(previous)
    return result


def write_wav(path, source, playback):
    with wave.open(str(path), "wb") as w:
        w.setparams((2, 2, RATE, 0, "NONE", ""))
        w.writeframes(b"".join(struct.pack("<hh", a, b) for a, b in zip(source, playback)))


def mulaw(sample):
    magnitude = min(abs(sample), 32635) + 132
    exponent = magnitude.bit_length() - 8
    mantissa = (magnitude >> (exponent + 3)) & 15
    return (~((128 if sample < 0 else 0) | (exponent << 4) | mantissa)) & 255


def packet_bytes(sport, dport, marker, sequence, timestamp, ssrc, payload):
    rtp = struct.pack("!BBHII", 0x80, 128 if marker else 0, sequence, timestamp, ssrc) + payload
    udp = struct.pack("!HHHH", sport, dport, 8 + len(rtp), 0) + rtp
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0)
    address = ipaddress.IPv4Address("127.0.0.1").packed
    return ip + address * 2 + udp


def make_inputs(output):
    source = source_samples()
    right = [0] * len(source)
    for index in range(3200, int(3.30 * RATE)):
        right[index] = source[index - 3200]
    for index in range(int(4.50 * RATE), len(right)):
        right[index] = source[index - int(2.70 * RATE)]
    write_wav(output / "echo.wav", source, right)
    repeated = [round(12000 * ((index % 25) - 12) / 12) for index in range(len(source))]
    write_wav(output / "periodic.wav", repeated, [0] * 3200 + repeated[:-3200])

    with (output / "echo.pcap").open("wb") as f:
        f.write(struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 101))
        bridge_seq = 0
        for tick in range(450):
            offset = tick * 20000
            sec, subsec = divmod(offset, 1000000)
            start = tick * PACKET
            upstream = bytes(mulaw(v) for v in source[start:start + PACKET])
            packets = [packet_bytes(15000, 15002, tick == 0, tick, start, 1, upstream)]
            if 5 <= tick < 150 or tick >= 210:
                age = 120 if tick >= 210 else 5
                first = (tick - age) * PACKET
                downstream = bytes(mulaw(v) for v in source[first:first + PACKET])
                packets.append(packet_bytes(16000, 16002, bridge_seq == 0, bridge_seq, bridge_seq * PACKET, 2, downstream))
                bridge_seq += 1
            for packet in packets:
                f.write(struct.pack("<IIII", EPOCH + sec, subsec, len(packet), len(packet)))
                f.write(packet)


def run(output):
    make_inputs(output)
    runs = []
    base = [sys.executable, "-B", "-m", "voice_tools", "--json", "gaps", "echo"]

    def call(name, wav, extra=(), expected=0):
        args = [*base, str(output / wav), "--out", str(output / name), *extra]
        completed = subprocess.run(args, capture_output=True, text=True, timeout=90)
        assert completed.returncode == expected, (args, completed.stdout, completed.stderr)
        envelope = json.loads(completed.stdout)
        runs.append({"command": args, "tool_version": envelope["tool_version"], "exit_code": completed.returncode})
        return json.loads((output / name / "echo.json").read_text())

    streams = ("--pcap", str(output / "echo.pcap"), "--flow", "browser-in=15000:15002",
               "--flow", "fs-to-gateway=16000:16002", "--plaintext-flow", "fs-to-gateway")
    result = call("observed", "echo.wav", (*streams, "--recording-start-epoch", str(EPOCH)))
    pause, = result["quiet_intervals"]
    assert abs(pause["start_s"] - 3.30) < .015 and abs(pause["duration_ms"] - 1200) < 15, pause
    assert abs(result["playback"]["max_age_ms"] - 2700) < 20, result["playback"]["max_age_ms"]
    flows = result["capture"]["flows"]
    ingress = flows["browser-in"]
    bridge = flows["fs-to-gateway"]
    assert abs(ingress["max_interarrival_ms"] - 20) < .01 and "content" not in ingress
    pause, = bridge["gaps"]
    assert abs(pause["duration_ms"] - 1220) < .01 and pause["sequence_delta"] == 1 and pause["rtp_timestamp_delta"] == 160, pause
    continuing = pause["other_flows"]["browser-in"]
    assert continuing["full_window"] and continuing["observed_packets"] >= 60, continuing
    assert abs(bridge["content"]["max_age_ms"] - 2400) < 20, bridge["content"]["max_age_ms"]
    path_sample = bridge["aligned_old_audio"][0]
    assert abs(path_sample["age_at_capture_ms"] - 2400) < 20, path_sample
    assert abs(path_sample["age_at_playback_ms"] - 2700) < 20, path_sample
    missing = call("wrong-clock", "echo.wav", (*streams, "--recording-start-epoch", str(EPOCH + 500)), expected=3)
    assert missing["capture"]["status"] == "error" and missing["playback"]["matched_windows"] > 0
    periodic = call("periodic", "periodic.wav")
    assert periodic["playback"]["status"] == "insufficient_match" and periodic["playback"]["max_age_ms"] is None

    from voice_tools.core.files import sha256
    verification = {"runs": runs, "source": "合成波形及 PCAP",
                    "sha256": {p.name: sha256(p) for p in output.iterdir() if p.is_file()}}
    (output / "verification.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False) + "\n")
    print(f"通过 {len(runs)} 项回声 CLI 验收，产物 {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    run(args.out.resolve())
