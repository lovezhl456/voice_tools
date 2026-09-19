"""Offline tshark extraction; explicit one-direction stream selection, G.711 only."""
import array
import hashlib
import ipaddress
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from voice_tools.core.files import new_output, sha256, write_json
from .scenario import MAX_SECONDS, VERSION, template, validate_dtmf_events

FIELDS = ("frame.number", "frame.time_epoch", "frame.cap_len", "frame.len", "ip.src", "ipv6.src", "udp.srcport",
          "ip.dst", "ipv6.dst", "udp.dstport", "rtp.ssrc", "rtp.seq", "rtp.timestamp", "rtp.p_type", "rtp.payload", "sip.Method")
MAX_PACKETS = 100000
MAX_BYTES = 128 * 1024 * 1024


def decode_options(ports):
    result = []
    for port in sorted(set(ports)):
        if not 1 <= port <= 65535:
            raise ValueError("rtp-port 必须为 1–65535")
        result.extend(["-d", f"udp.port=={port},rtp"])
    return result


def executable(name):
    result = shutil.which(name)
    if not result:
        raise ValueError(f"缺少 {name}；PCAP 操作需要 Wireshark 的 tshark，呼叫执行不需要它")
    return result


def read_capture(path, ports=(), tshark="tshark"):
    path = Path(path).resolve()
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("PCAP 必须非空且不超过 128 MiB，请先按通话分段")
    cmd = [executable(tshark), "-n", "-r", str(path), "-c", str(MAX_PACKETS + 1)] + decode_options(ports)
    cmd += ["-T", "fields", "-E", "occurrence=f"]
    for field in FIELDS:
        cmd.extend(["-e", field])
    streams, count, info_count = {}, 0, 0
    with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(mode="w+b") as errors:
        try:
            result = subprocess.run(cmd, stdout=output, stderr=errors, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("tshark 解析超过 120 秒；请缩小抓包") from exc
        if result.returncode:
            errors.seek(0)
            raise ValueError("tshark 解析失败：" + errors.read(4096).decode("utf-8", "replace"))
        if output.tell() > MAX_BYTES * 4:
            raise ValueError("tshark 输出超过分析上限")
        output.seek(0)
        for raw in output:
            count += 1
            if count > MAX_PACKETS:
                raise ValueError("抓包超过 100000 包；拒绝把截断分析当作完整素材")
            row = raw.decode("utf-8", "replace").rstrip("\r\n").split("\t")
            if len(row) != len(FIELDS):
                raise ValueError("tshark 字段格式不完整")
            n, epoch, captured, wire, ip4, ip6, sport, dst4, dst6, dport, ssrc, seq, stamp, pt, payload, method = row
            info_count += int(method == "INFO")
            if not all((ssrc, seq, stamp, pt, sport, dport, ip4 or ip6, dst4 or dst6)):
                continue
            src, dst = str(ipaddress.ip_address(ip4 or ip6)), str(ipaddress.ip_address(dst4 or dst6))
            key = (src, int(sport), dst, int(dport), int(ssrc, 0))
            stream_id = "rtp-" + hashlib.sha256(repr(key).encode()).hexdigest()[:12]
            if stream_id not in streams:
                if len(streams) >= 128:
                    raise ValueError("RTP 流超过 128 条，请先按通话筛选")
                streams[stream_id] = {"id": stream_id, "src": src, "src_port": int(sport), "dst": dst,
                                      "dst_port": int(dport), "ssrc": key[-1], "packets": []}
            try:
                packet = {"number": int(n), "epoch": float(epoch), "seq": int(seq), "timestamp": int(stamp),
                          "pt": int(pt), "payload": bytes.fromhex(payload.replace(":", "")), "truncated": int(captured) < int(wire)}
            except ValueError as exc:
                raise ValueError("无效 RTP 字段或载荷") from exc
            if not math.isfinite(packet["epoch"]):
                raise ValueError("无效抓包时间戳")
            streams[stream_id]["packets"].append(packet)
    return {"path": str(path), "sha256": sha256(path), "packets_examined": count, "sip_info_packets": info_count,
            "streams": streams, "rtp_ports": list(ports), "tshark": tshark}


def inspection(capture):
    output = []
    for stream in capture["streams"].values():
        packets = stream["packets"]
        output.append({**{k: v for k, v in stream.items() if k != "packets"}, "packets": len(packets),
                       "payload_types": sorted({p["pt"] for p in packets}),
                       "capture_duration_s": max(p["epoch"] for p in packets) - min(p["epoch"] for p in packets),
                       "truncated_packets": sum(p["truncated"] for p in packets)})
    return {"schema_version": VERSION, "source": capture["path"], "sha256": capture["sha256"],
            "packets_examined": capture["packets_examined"], "sip_info_packets": capture["sip_info_packets"], "streams": output,
            "selection_required": True, "notes": ["每条流是一个方向和一个 SSRC；只选择准备模拟的发送方向。",
            "若没有 RTP，请按实际媒体端口加 --rtp-port；动态 payload type 需显式指定映射。",
            "SIP INFO 不会自动转为 DTMF；本工具不解密 SRTP。"]}


def select(capture, stream_id):
    if stream_id not in capture["streams"]:
        raise ValueError("stream 不存在；先 pcap-inspect，再使用其返回的流 ID")
    stream = capture["streams"][stream_id]
    if any(p["truncated"] for p in stream["packets"]):
        raise ValueError("所选 RTP 包被 snaplen 截断，不能作为完整回放素材")
    return stream


def g711_sample(byte, codec):
    if codec == "PCMU":
        value = (~byte) & 255
        sample = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
        return 132 - sample if value & 128 else sample - 132
    value = byte ^ 0x55
    sample, segment = (value & 15) << 4, (value >> 4) & 7
    sample += 8 if segment == 0 else 264
    if segment > 1:
        sample <<= segment - 1
    return sample if value & 128 else -sample


def media_from_stream(stream, codec=None, audio_pt=None, dtmf_pt=None):
    packets = stream["packets"]
    pts = {p["pt"] for p in packets}
    if dtmf_pt is not None and not 0 <= dtmf_pt <= 127:
        raise ValueError("dtmf-pt 须为 0–127")
    if audio_pt is None:
        candidates = pts & {0, 8}
        if len(candidates) != 1:
            raise ValueError("无法唯一确定 G.711 payload type，请指定 --audio-pt 和 --codec")
        audio_pt = candidates.pop()
    if not 0 <= audio_pt <= 127 or audio_pt not in pts or audio_pt == dtmf_pt:
        raise ValueError("audio-pt 不存在、越界或与 dtmf-pt 冲突")
    standard = {0: "PCMU", 8: "PCMA"}.get(audio_pt)
    codec = codec or standard
    if codec not in ("PCMA", "PCMU") or (standard and codec != standard):
        raise ValueError("codec 与静态 payload type 不一致，或未指定动态 G.711 codec")
    unknown = pts - {audio_pt, dtmf_pt}
    if unknown:
        raise ValueError(f"所选流包含未映射 payload type {sorted(unknown)}；先筛选或明确 --dtmf-pt")
    first = packets[0]["timestamp"]
    def offset(stamp):
        return ((stamp - first + (1 << 31)) % (1 << 32)) - (1 << 31)
    origin = min(offset(p["timestamp"]) for p in packets)
    decoded, events, seen, duplicates = [], {}, {}, 0
    maximum = 0
    for packet in packets:
        key = (packet["seq"], packet["timestamp"], packet["pt"])
        payload = packet["payload"]
        if key in seen:
            if seen[key] != payload:
                raise ValueError("同一 RTP 序号／时间戳出现冲突载荷")
            duplicates += 1
            continue
        seen[key] = payload
        start = offset(packet["timestamp"]) - origin
        if not 0 <= start <= MAX_SECONDS * 8000:
            raise ValueError("RTP 时间轴超过 900 秒或发生不支持的时间戳重置")
        if packet["pt"] == audio_pt:
            if not payload:
                raise ValueError("音频 RTP 载荷为空，无法解码")
            decoded.append((start, payload))
            maximum = max(maximum, start + len(payload))
        else:
            if len(payload) != 4:
                raise ValueError("DTMF 载荷必须是 4 字节 telephone-event；不支持加密或复合事件")
            digit, flags, duration = struct.unpack("!BBH", payload)
            if digit > 15 or flags & 0x40:
                raise ValueError("不支持的 telephone-event 编号或保留位")
            ekey = (packet["timestamp"], digit)
            event = events.setdefault(ekey, {"at_s": start / 8000, "digit": "0123456789*#ABCD"[digit], "duration_ms": 0, "end_observed": False})
            event["duration_ms"] = max(event["duration_ms"], math.ceil(duration / 8))
            event["end_observed"] |= bool(flags & 0x80)
            maximum = max(maximum, start + math.ceil(duration / 8) * 8)
    if not decoded or not 0 < maximum <= MAX_SECONDS * 8000:
        raise ValueError("所选流没有可解码音频或时长超限")
    event_list = sorted(events.values(), key=lambda x: x["at_s"])
    validate_dtmf_events(event_list, maximum / 8000)
    audio, covered = array.array("h", [0]) * maximum, bytearray(maximum)
    table = [g711_sample(b, codec) for b in range(256)]
    for start, payload in sorted(decoded):
        for i, byte in enumerate(payload, start):
            value = table[byte]
            if covered[i] and audio[i] != value:
                raise ValueError("音频 RTP 时间轴有内容冲突的重叠，拒绝猜测覆盖顺序")
            audio[i], covered[i] = value, 1
    warnings = ["缺失的音频采样填零；该 WAV 是内容重建，不复现原网络抖动。", "时间零点为所选流最早 RTP 时间戳，不是 SIP 接通时刻。"]
    if any(not e["end_observed"] for e in event_list):
        warnings.append("有 DTMF 未捕获结束包，时长是已观察到的最大值。")
    if sys.byteorder != "little":
        audio.byteswap()
    return audio.tobytes(), {"codec": codec, "audio_pt": audio_pt, "dtmf_pt": dtmf_pt,
                             "duration_s": maximum / 8000, "dtmf": event_list, "duplicates_removed": duplicates,
                             "silence_fill_samples": maximum - sum(covered), "warnings": warnings}


def import_capture(capture, stream_id, output, codec=None, audio_pt=None, dtmf_pt=None):
    stream = select(capture, stream_id)
    pcm, details = media_from_stream(stream, codec, audio_pt, dtmf_pt)
    if capture["sip_info_packets"]:
        details["warnings"].append("抓包中存在 SIP INFO，本次未转换；需核对本通话按键并显式加入策略。")
    output = new_output(output)
    wav = output / "audio.wav"
    with wave.open(str(wav), "wb") as writer:
        writer.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    origin = {"pcap_sha256": capture["sha256"], "stream": {k: v for k, v in stream.items() if k != "packets"},
              "codec": details["codec"], "audio_pt": details["audio_pt"], "dtmf_pt": dtmf_pt,
              "silence_fill_samples": details["silence_fill_samples"], "duplicates_removed": details["duplicates_removed"]}
    bundle = {"schema_version": VERSION, "audio": "audio.wav", "audio_sha256": sha256(wav),
              "duration_s": details["duration_s"], "dtmf": details["dtmf"], "source": origin, "warnings": details["warnings"]}
    write_json(output / "media.json", bundle)
    write_json(output / "dtmf.json", {"schema_version": VERSION, "time_origin": "media_start", "events": details["dtmf"]})
    scenario = template("media.json")
    scenario["codec"] = details["codec"]
    # Preserve the duration cap even for a full-length import.
    scenario["steps"] = [{"action": "play_media", "file": "media.json"}, {"action": "hangup"}]
    write_json(output / "scenario.json", scenario)
    return {"status": "imported", "stream_id": stream_id, **details}


def export_stream(capture, stream, output):
    family = "ip" if ipaddress.ip_address(stream["src"]).version == 4 else "ipv6"
    selection = (f"rtp && {family}.src == {stream['src']} && {family}.dst == {stream['dst']} && "
                 f"udp.srcport == {stream['src_port']} && udp.dstport == {stream['dst_port']} && rtp.ssrc == {stream['ssrc']}")
    cmd = [executable(capture["tshark"]), "-n", "-r", capture["path"], "-Y", selection, "-F", "pcap", "-w", str(output)] + decode_options(capture["rtp_ports"])
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("导出选中 RTP 流超时") from exc
    if result.returncode or sha256(capture["path"]) != capture["sha256"]:
        raise ValueError("RTP 导出失败或源 PCAP 在处理期间发生变化")
