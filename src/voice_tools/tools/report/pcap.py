"""通过本机 tshark 提取 RTP 头；不把抓包点序号缺口等同于网络丢包。"""
import csv
import math
from pathlib import Path
import shutil
import subprocess
import tempfile


STATIC_CLOCKS = {0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000, 7: 8000,
                 8: 8000, 9: 8000, 10: 44100, 11: 44100, 12: 8000, 13: 8000, 18: 8000}
FIELDS = ("frame.number", "frame.time_epoch", "frame.cap_len", "frame.len", "ip.src", "ipv6.src",
          "udp.srcport", "ip.dst", "ipv6.dst", "udp.dstport", "rtp.ssrc", "rtp.seq", "rtp.timestamp", "rtp.p_type")


class RTPStream:
    def __init__(self, key, clocks):
        self.key, self.clocks = key, clocks
        self.payload_types = set()
        self.previous_rate = None
        self.count = self.duplicates = self.reordered = self.resets = 0
        self.start = self.end = self.previous_time = self.previous_stamp = None
        self.max_gap_ms = self.jitter = self.max_jitter = 0.0
        self.seen = set()
        self.low = self.high = None
        self.missing = self.unique = 0
        self.timeline = {}

    def close_segment(self):
        if self.seen:
            self.missing += self.high - self.low + 1 - len(self.seen)
            self.unique += len(self.seen)
        self.seen.clear()
        self.low = self.high = None

    def add(self, time, seq, stamp, pt):
        if not math.isfinite(time) or not 0 <= seq <= 65535 or not 0 <= stamp <= 0xffffffff:
            raise ValueError("RTP 包包含无效时间、序号或时间戳")
        if self.start is None:
            self.start = time
        self.end = time
        self.count += 1
        self.payload_types.add(pt)
        rate = self.clocks.get(pt)
        bucket = max(0, int(time - self.start))
        if bucket < 86400:
            self.timeline[bucket] = self.timeline.get(bucket, 0) + 1
        extended = seq if self.high is None else self.high + ((seq - self.high + 32768) % 65536 - 32768)
        if self.high is not None and abs(extended - self.high) > 3000:
            # A large jump may be a source restart; do not manufacture thousands of lost packets.
            self.close_segment()
            self.resets += 1
            self.previous_time = self.previous_stamp = None
            self.jitter = 0
            extended = seq
        if extended in self.seen:
            self.duplicates += 1
            return
        if self.high is not None and extended < self.high:
            self.reordered += 1
        self.seen.add(extended)
        self.high = extended if self.high is None else max(self.high, extended)
        self.low = extended if self.low is None else min(self.low, extended)
        if self.previous_time is not None:
            delta = time - self.previous_time
            self.max_gap_ms = max(self.max_gap_ms, delta * 1000)
            if rate and rate == self.previous_rate and delta >= 0:
                stamp_delta = ((stamp - self.previous_stamp + 2**31) % 2**32 - 2**31)
                d = abs(delta - stamp_delta / rate)
                self.jitter += (d - self.jitter) / 16
                self.max_jitter = max(self.max_jitter, self.jitter)
        self.previous_time, self.previous_stamp = time, stamp
        self.previous_rate = rate

    def result(self):
        missing, unique = self.missing, self.unique
        if self.seen:
            missing += self.high - self.low + 1 - len(self.seen)
            unique += len(self.seen)
        src, sport, dst, dport, ssrc = self.key
        rates = {self.clocks.get(pt) for pt in self.payload_types}
        rate = next(iter(rates)) if len(rates) == 1 and None not in rates else None
        return {"src": src, "src_port": sport, "dst": dst, "dst_port": dport, "ssrc": ssrc,
                "payload_types": sorted(self.payload_types), "clock_rate": rate, "packets": self.count,
                "unique_packets": unique, "sequence_gap_candidates": missing,
                "gap_fraction": missing / (unique + missing) if unique + missing else 0,
                "duplicate_candidates": self.duplicates, "reordered_packets": self.reordered,
                "sequence_discontinuities": self.resets, "first_epoch": self.start, "last_epoch": self.end,
                "duration_s": max(0, self.end - self.start), "max_interarrival_ms": round(self.max_gap_ms, 3),
                "jitter_last_ms": round(self.jitter * 1000, 3) if rate else None,
                "jitter_max_ms": round(self.max_jitter * 1000, 3) if rate else None,
                "packets_per_second": [[k, v] for k, v in sorted(self.timeline.items())]}


def analyze_rows(rows, clock_rates=None, max_packets=250000):
    clocks = {**STATIC_CLOCKS, **(clock_rates or {})}
    streams = {}
    count = truncated = negative_time = 0
    first = last = None
    limited = False
    for row in rows:
        if count >= max_packets:
            limited = True
            break
        if len(row) != len(FIELDS):
            raise ValueError("tshark 字段输出格式不完整")
        number, when, cap_len, wire_len, src4, src6, sport, dst4, dst6, dport, ssrc, seq, stamp, pt = row
        when = float(when)
        if not math.isfinite(when):
            raise ValueError("PCAP 时间戳无效")
        if last is not None and when < last:
            negative_time += 1
        first = when if first is None else min(first, when)
        last = when
        count += 1
        truncated += int(int(cap_len) < int(wire_len))
        if not (ssrc and seq and stamp and pt and sport and dport and (src4 or src6) and (dst4 or dst6)):
            continue
        key = (src4 or src6, int(sport), dst4 or dst6, int(dport), ssrc)
        if key not in streams:
            if len(streams) >= 1000:
                raise ValueError("RTP 流超过 1000 条，请先按会话拆分 PCAP")
            streams[key] = RTPStream(key, clocks)
        streams[key].add(when, int(seq), int(stamp), int(pt))
    return {"packets_examined": count, "truncated_packets": truncated, "packet_limit_reached": limited,
            "out_of_order_capture_timestamps": negative_time, "first_epoch": first, "last_epoch": last,
            "streams": [stream.result() for stream in streams.values()]}


def analyze(path, rtp_ports=(), clock_rates=None, max_packets=250000, tshark="tshark"):
    if not 1 <= max_packets <= 1000000:
        raise ValueError("max-packets 须为 1–1000000")
    executable = shutil.which(tshark)
    if not executable:
        raise ValueError("PCAP 分析需要本机 tshark（Wireshark CLI）；录音分析不需要它")
    if Path(path).stat().st_size > 512 * 1024 * 1024:
        raise ValueError("单份 PCAP 超过 512 MiB，请先分段")
    args = [executable, "-n", "-r", str(path), "-c", str(max_packets + 1)]
    for port in sorted(set(rtp_ports)):
        if not 1 <= port <= 65535:
            raise ValueError("RTP 端口须为 1–65535")
        args += ["-d", f"udp.port=={port},rtp"]
    args += ["-T", "fields", "-E", "separator=/t", "-E", "occurrence=f"]
    for field in FIELDS:
        args += ["-e", field]
    with tempfile.TemporaryDirectory(prefix="voice-tools-tshark-") as temp:
        table, errors = Path(temp) / "packets.tsv", Path(temp) / "stderr.txt"
        with table.open("w") as stdout, errors.open("w") as stderr:
            try:
                result = subprocess.run(args, stdout=stdout, stderr=stderr, text=True, timeout=180)
            except subprocess.TimeoutExpired as error:
                raise ValueError("tshark 分析超过 180 秒，请分段后重试") from error
        warnings = errors.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
        # tshark can emit useful rows before failing; do not label a corrupt file successful.
        if result.returncode:
            raise ValueError(f"tshark 无法完整读取 PCAP ({result.returncode})：{warnings}")
        with table.open(encoding="utf-8") as stream:
            data = analyze_rows(csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE), clock_rates, max_packets)
    data["tshark_warnings"] = warnings
    data["decode_as_ports"] = sorted(set(rtp_ports))
    data["warnings"] = ["序号缺口是抓包点的缺失候选，可能来自网络、抓包丢弃、过滤或截断；不能直接等同网络丢包率。",
                        "未自动解密 SRTP，也不解码 RTP 音频；抖动只基于已知或显式指定的 RTP 时钟。",
                        "各 PCAP 独立统计，未跨抓包点去重；不同文件与录音未自动对齐。"]
    if not data["streams"]:
        data["warnings"].append("未识别 RTP：可能无媒体、缺少 SIP/SDP、端口未解码或媒体加密；可用 --rtp-port 指定已确认的媒体端口。")
    if data["packet_limit_reached"]:
        data["warnings"].append("达到分析包数上限，统计只覆盖文件前部。")
    if data["out_of_order_capture_timestamps"]:
        data["warnings"].append("抓包时间戳发生倒退，时序和抖动指标须谨慎解释。")
    return data
