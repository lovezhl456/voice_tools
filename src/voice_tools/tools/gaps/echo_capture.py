"""Observe selected RTP flows on the recording's wall-clock timeline."""
from bisect import bisect_left, bisect_right
import csv
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from threading import Timer

import numpy as np

from voice_tools.audio.rtp import decode_g711, parse_rtp
from voice_tools.core.files import sha256
from voice_tools.tools.report.pcap import FIELDS
from .echo import MATCH_RATE, WINDOW_S, match_source, observation_times, resample, summarize_matches
from .evidence import packet_metrics

MAX_PACKETS = 250000
MAX_GAPS = 1000
CODECS = {0: "PCMU", 8: "PCMA"}


@dataclass
class Packet:
    time_s: float
    sequence: int
    timestamp: int
    pt: int
    payload: bytes


def capture_options(pcap, recording_start_epoch, flows, plaintext_flows, tshark):
    if pcap is None:
        if recording_start_epoch is not None or flows or plaintext_flows:
            raise ValueError("RTP 参数需要 --pcap")
        return None
    if recording_start_epoch is None or not isfinite(recording_start_epoch) or recording_start_epoch < 0:
        raise ValueError("请提供已核实的 --recording-start-epoch")
    if not 1 <= len(flows) <= 8:
        raise ValueError("PCAP 需要明确指定 1–8 条 --flow")
    selectors = {}
    for value in flows:
        match = re.fullmatch(r"([A-Za-z0-9_-]{1,48})=(\d{1,5}):(\d{1,5})", value)
        if not match:
            raise ValueError("--flow 格式为 名称=源UDP端口:目标UDP端口")
        name, source, destination = match.groups()
        pair = (int(source), int(destination))
        if any(not 1 <= port <= 65535 for port in pair) or name in selectors or pair in selectors.values():
            raise ValueError("流向名称和端口方向不能重复，端口须为 1–65535")
        selectors[name] = pair
    if set(plaintext_flows) - selectors.keys():
        raise ValueError("--plaintext-flow 须使用 --flow 已声明的名称")
    return {"pcap": pcap, "origin": recording_start_epoch, "selectors": selectors,
            "plaintext": set(plaintext_flows), "tshark": tshark}


def read_packets(path, options, duration):
    executable = shutil.which(options["tshark"])
    if not executable:
        raise ValueError("PCAP 分析需要本机 tshark")
    origin = options["origin"]
    names = {pair: name for name, pair in options["selectors"].items()}
    terms = [f"(udp.srcport == {a} && udp.dstport == {b})" for a, b in names]
    expression = "(rtp && !rtcp) && (" + " || ".join(terms) + ")"
    args = [executable, "-n", "-r", str(path), "-Y", expression, "-T", "fields",
            "-E", "separator=/t", "-E", "occurrence=f"]
    for port in sorted({port for pair in names for port in pair}):
        args.extend(["-d", f"udp.port=={port},rtp"])
    fields = (*FIELDS, "udp.payload") if options["plaintext"] else FIELDS
    for field in fields:
        args.extend(["-e", field])
    packets = {name: [] for name in names.values()}
    identities = {}
    truncated = {name: 0 for name in names.values()}
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, text=True)
        timer = Timer(180, process.kill)
        timer.daemon = True
        timer.start()
        try:
            for count, row in enumerate(csv.reader(process.stdout, delimiter="\t", quoting=csv.QUOTE_NONE), 1):
                if count > MAX_PACKETS:
                    raise ValueError("匹配的 RTP 超过 250000 包，请裁取单通窗口")
                if len(row) != len(fields):
                    raise ValueError("TShark 返回的 RTP 字段不完整")
                when = float(row[1]) - origin
                if not isfinite(when):
                    raise ValueError("PCAP 含无效时间戳")
                if not 0 <= when <= duration:
                    continue
                _, _, captured, length, src4, src6, source, dst4, dst6, dest, ssrc, seq, stamp, pt = row[:len(FIELDS)]
                if not (ssrc and seq and stamp and pt and (src4 or src6) and (dst4 or dst6)):
                    continue
                name = names.get((int(source), int(dest)))
                if name is None:
                    # A nested UDP header can match the filter without being the first occurrence.
                    continue
                identity = (src4 or src6, dst4 or dst6, ssrc)
                if identities.setdefault(name, identity) != identity:
                    raise ValueError(f"{name} 匹配到多个端点或 SSRC，请先按通话和媒体段裁取 PCAP")
                flow = packets[name]
                if flow and when < flow[-1].time_s:
                    raise ValueError(f"{name} 的抓包时间倒退，无法对齐")
                payload = b""
                if int(captured) < int(length):
                    truncated[name] += 1
                elif name in options["plaintext"] and int(pt) in CODECS and row[-1]:
                    parsed = parse_rtp(bytes.fromhex(row[-1].replace(":", "")))
                    if parsed and len(parsed["payload"]) <= 480:
                        payload = parsed["payload"]
                flow.append(Packet(when, int(seq), int(stamp), int(pt), payload))
            if process.wait():
                errors.seek(0)
                raise ValueError("TShark 读包失败或超时：" + errors.read(1500).decode("utf-8", "replace"))
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
    return packets, truncated


def summarize_flow(packets, times, start_s, end_s):
    first = max(0, bisect_left(times, start_s) - 1)
    last = min(len(times), bisect_right(times, end_s) + 1)
    rows = [(p.time_s, p.sequence, p.timestamp) for p in packets[first:last]]
    return {**packet_metrics(rows, start_s, end_s),
            "full_window": bool(times and times[0] <= start_s and times[-1] >= end_s)}


def content_match(reference, packets, times, duration, gaps, old_audio_ms, max_age_ms):
    matches = []
    attempted = 0
    indices = {bisect_left(times, at) for at in observation_times(duration, (g["end_s"] for g in gaps))}
    for first in sorted(indices):
        wanted = round(WINDOW_S * 8000)
        payloads = bytearray()
        previous = None
        for packet in packets[first:first + 100]:
            if not packet.payload:
                break
            if previous and (packet.pt != previous.pt or (packet.sequence - previous.sequence) % 65536 != 1
                    or (packet.timestamp - previous.timestamp) % 2**32 != len(previous.payload)
                    or packet.time_s - previous.time_s > len(previous.payload) / 8000 + .020):
                break
            payloads.extend(packet.payload)
            if len(payloads) >= wanted:
                break
            previous = packet
        if len(payloads) < wanted:
            continue
        head = packets[first]
        samples = np.frombuffer(decode_g711(payloads[:wanted], CODECS[head.pt]), dtype="<i2") / 32768.
        attempted += 1
        match = match_source(reference, resample(samples, 8000), head.time_s, max_age_ms / 1000)
        if match:
            matches.append(match)
    return summarize_matches(matches, old_audio_ms, attempted)


def observe_capture(reference, duration, pause_ms, old_audio_ms, max_age_ms, options):
    path = Path(options["pcap"])
    if path.stat().st_size > 2 * 1024 ** 3:
        raise ValueError("PCAP 超过 2 GiB，请裁取同一通话的时间窗口")
    digest = sha256(path)
    packets, truncated = read_packets(path, options, duration)
    if not any(packets.values()):
        raise ValueError("录音窗口内未找到指定流向的 RTP，请检查端口与录音时钟")
    times = {name: [p.time_s for p in flow] for name, flow in packets.items()}
    result = {}
    for name, flow in packets.items():
        intervals = []
        maximum = None
        for a, b in zip(flow, flow[1:]):
            delta_ms = (b.time_s - a.time_s) * 1000
            maximum = delta_ms if maximum is None else max(maximum, delta_ms)
            if delta_ms >= pause_ms:
                intervals.append({"start_s": a.time_s, "end_s": b.time_s, "duration_ms": round(delta_ms, 3),
                                  "sequence_delta": (b.sequence - a.sequence) % 65536,
                                  "rtp_timestamp_delta": (b.timestamp - a.timestamp) % 2**32})
        largest = sorted(intervals, key=lambda row: -row["duration_ms"])[:MAX_GAPS]
        for gap in largest:
            start, end = gap["start_s"], gap["end_s"]
            gap["other_flows"] = {other: summarize_flow(rows, times[other], start, end)
                                  for other, rows in packets.items() if other != name}
        entry = {"ports": list(options["selectors"][name]), "packets": len(flow),
                 "coverage_s": [flow[0].time_s, flow[-1].time_s] if flow else None,
                 "truncated_packets": truncated[name], "gap_count": len(intervals),
                 "max_interarrival_ms": None if maximum is None else round(maximum, 3), "gaps": largest}
        if name in options["plaintext"]:
            entry["content"] = content_match(reference, flow, times[name], duration, largest, old_audio_ms, max_age_ms)
        result[name] = entry
    if sha256(path) != digest:
        raise ValueError("抓包在分析期间发生变化")
    complete = all(len(flow) >= 2 for flow in packets.values()) and not any(truncated.values())
    return {"status": "observed" if complete else "partial", "path": str(path.resolve()), "sha256": digest,
            "recording_start_epoch": options["origin"], "flows": result}
