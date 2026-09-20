"""Read-only file contracts; never invoke scoring, packet capture or other tools."""
from bisect import bisect_left, bisect_right
import json
from pathlib import Path

from voice_tools.core.files import read_json, sha256
from voice_tools.core.output_events import number

STREAM_FIELDS = ("src", "src_port", "dst", "dst_port", "ssrc")


def invalid_number(value):
    raise ValueError(f"证据 JSON 含非有限数字：{value}")


def object_file(path):
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError("证据 JSON 须为对象")
    return value


def rows(path, maximum=1000000):
    with Path(path).open(encoding="utf-8") as stream:
        count = 0
        for line in stream:
            if len(line) > 1024 * 1024:
                raise ValueError("证据 JSONL 单行超过 1 MiB")
            if not line.strip():
                continue
            count += 1
            if count > maximum:
                raise ValueError("证据记录超过读取额度")
            value = json.loads(line, parse_constant=invalid_number)
            if not isinstance(value, dict):
                raise ValueError("证据记录须为对象")
            yield value


def reference(base, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("证据引用须为相对文件路径")
    path = base / value
    if not path.is_file():
        raise ValueError(f"缺少证据文件：{value}")
    return path


def stream_key(row):
    if not isinstance(row, dict):
        raise ValueError("RTP 流须为对象")
    try:
        ssrc = row["ssrc"]
        ssrc = int(ssrc, 0) if isinstance(ssrc, str) else int(ssrc)
        return row["src"], int(row["src_port"]), row["dst"], int(row["dst_port"]), ssrc
    except (KeyError, ValueError, TypeError):
        raise ValueError("需要完整的 RTP 方向、地址、端口和 SSRC") from None


def nisqa(base, binding, record):
    path = reference(base, binding.get("results"))
    channel = binding.get("channel")
    expected_channel = "right" if record["result"]["config"]["system_channel"] else "left"
    if channel != expected_channel:
        raise ValueError("NISQA 声道与 AI 轨不匹配")
    original = binding.get("source_file")
    if not isinstance(original, str) or not original:
        raise ValueError("NISQA 绑定需明确 source_file（结果中的来源标识）")
    state = "unverified"
    if binding.get("provenance"):
        provenance = object_file(reference(base, binding["provenance"]))
        if provenance.get("kind") != "nisqa_provenance" or provenance.get("schema_version") != "1.0" or provenance.get("results_sha256") != sha256(path):
            raise ValueError("NISQA 溯源或结果摘要不匹配")
        sources = provenance.get("sources")
        if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
            raise ValueError("NISQA 溯源 sources 须为对象列表")
        matches = [s for s in sources if s.get("file") == original]
        if len(matches) != 1 or matches[0].get("sha256") != record["audio_sha256"] or matches[0].get("unchanged") is not True:
            raise ValueError("NISQA 源录音摘要或评分期间一致性不匹配")
        state = "aligned"
    offset = number(binding.get("offset_s", 0), "NISQA offset_s", -86400, 86400)
    # Matching bytes already defines the timeline; manual offsets require explicit verification.
    if offset and binding.get("alignment_verified") is not True:
        state = "unaligned"
    segments = []
    for row in rows(path):
        if row.get("file") != original or row.get("channel") != channel:
            continue
        if "start_seconds" not in row or "end_seconds" not in row:
            continue
        start = number(row["start_seconds"], "start_seconds") + offset
        end = number(row["end_seconds"], "end_seconds") + offset
        if end <= start:
            raise ValueError("NISQA 片段区间无效")
        segments.append({"start_s": start, "end_s": end, "segment_index": row.get("segment_index"),
                         "status": row.get("status"), "scores": row.get("scores"), "reason": row.get("reason")})
    intervals = {}
    if state != "unaligned":
        for gap in record["result"]["gaps"]:
            intervals[gap["id"]] = [s for s in segments if s["start_s"] < gap["end_s"] and s["end_s"] > gap["start_s"]]
    return {"kind": "nisqa", "status": state if segments else "no_matching_segments", "results_sha256": sha256(path),
            "intervals": intervals, "notice": "分段分数不是间隙级诊断；unverified 仅为人工绑定旁证。"}


def packet_metrics(packets, start, end):
    """Sequence jumps are observations, not final packet-loss counts."""
    count = reordered = duplicates = jumps = resets = 0
    maximum_gap = 0.0
    high, previous, previous_stamp = None, None, None
    timestamp_wraps = timestamp_backwards = sequence_wraps = 0
    seen = set()
    for when, sequence, stamp in packets:
        in_window = start <= when <= end
        count += int(in_window)
        extended = sequence if high is None else high + ((sequence-high+32768) % 65536-32768)
        if high is not None and abs(extended-high) > 3000:
            resets += int(in_window)
            high, previous, seen = None, None, set()
            extended = sequence
            previous_stamp = None
        if extended in seen:
            duplicates += int(in_window)
            continue
        if high is not None:
            reordered += int(in_window and extended < high)
            sequence_wraps += int(in_window and extended > high and sequence < high % 65536)
            if previous is not None and when >= start and previous <= end:
                jumps += max(0, extended-high-1)
        if previous_stamp is not None and (high is None or extended > high):
            delta = (stamp - previous_stamp + 2**31) % 2**32 - 2**31
            timestamp_wraps += int(in_window and stamp < previous_stamp and delta >= 0)
            timestamp_backwards += int(in_window and delta < 0)
        if high is None or extended > high:
            previous_stamp = stamp
        seen.add(extended)
        high = extended if high is None else max(high, extended)
        if previous is not None and previous <= end and when >= start:
            maximum_gap = max(maximum_gap, (when-previous)*1000)
        previous = when
    return {"observed_packets": count, "max_interarrival_ms": round(maximum_gap, 3),
            "forward_sequence_jump_candidates": jumps, "reordered": reordered,
            "duplicates": duplicates, "source_restart_candidates": resets,
            "sequence_wraps": sequence_wraps, "timestamp_wraps": timestamp_wraps,
            "timestamp_backwards_candidates": timestamp_backwards}


def rtp(base, binding, record):
    selected = stream_key(binding.get("stream", {}))
    if not binding.get("timeline"):
        report = object_file(reference(base, binding.get("report")))
        summaries = []
        for source in report.get("pcaps", []):
            if not isinstance(source, dict) or not isinstance(source.get("analysis"), dict):
                raise ValueError("旧 RTP 报告结构无效")
            if source.get("sensor") != binding.get("sensor"):
                continue
            summaries.extend(s for s in source.get("analysis", {}).get("streams", []) if stream_key(s) == selected)
        return {"kind": "rtp", "status": "legacy_summary", "summaries": summaries,
                "notice": "仅整流统计，无法定位到当前间隙。"}
    path = reference(base, binding["timeline"])
    manifest = object_file(path)
    if manifest.get("kind") != "rtp_timeline" or manifest.get("schema_version") != "1.0":
        raise ValueError("不支持的 RTP 时序清单")
    if not binding.get("sensor") or binding["sensor"] != manifest.get("sensor"):
        raise ValueError("RTP 采集点不匹配")
    if binding.get("alignment_verified") is not True or "recording_start_epoch" not in binding:
        return {"kind": "rtp", "status": "unaligned", "notice": "缺少录音零点到抓包时间的已核实映射"}
    origin = number(binding["recording_start_epoch"], "recording_start_epoch", 0, 1e12)
    if binding.get("clock_drift_detected"):
        return {"kind": "rtp", "status": "unaligned", "notice": "检测到时钟漂移，不能使用单一时间偏移"}
    packets, previous, backwards, total = [], None, False, 0
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or len(chunks) > 10000:
        raise ValueError("RTP 分片清单无效")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("RTP 分片条目须为对象")
        part = reference(path.parent, chunk.get("path"))
        if part.resolve().parent != path.parent.resolve() or part.stat().st_size > 8*1024*1024:
            raise ValueError("RTP 分片路径或大小无效")
        if part.stat().st_size != chunk.get("bytes") or sha256(part) != chunk.get("sha256"):
            raise ValueError("RTP 分片摘要或大小不匹配")
        count = 0
        for row in rows(part):
            count += 1
            total += 1
            if total > 1000000:
                raise ValueError("RTP 时序超过 1000000 包额度")
            if stream_key(row) != selected:
                continue
            when = number(row.get("epoch"), "epoch", 0, 1e12)
            seq = number(row.get("seq"), "seq", 0, 65535)
            stamp = number(row.get("timestamp"), "timestamp", 0, 2**32-1)
            if int(seq) != seq or int(stamp) != stamp:
                raise ValueError("RTP 序号或时间戳不是整数")
            backwards |= previous is not None and when < previous
            previous = when
            packets.append((when, int(seq), int(stamp)))
        if count != chunk.get("rows"):
            raise ValueError("RTP 分片行数不匹配")
    if backwards or manifest.get("out_of_order_capture_timestamps"):
        return {"kind": "rtp", "status": "unaligned", "notice": "抓包时间倒退，未做窗口关联"}
    times = [p[0] for p in packets]
    intervals = {}
    for gap in record["result"]["gaps"]:
        start, end = origin+gap["start_s"], origin+gap["end_s"]
        a, b = max(0, bisect_left(times, start)-1), min(len(times), bisect_right(times, end)+1)
        covered = bool(times and times[0] <= start and times[-1] >= end and manifest.get("complete"))
        intervals[gap["id"]] = {"coverage": "observed_window" if covered else "partial",
                               **packet_metrics(packets[a:b], start, end)}
    return {"kind": "rtp", "status": "aligned" if packets and manifest.get("complete") else "partial",
            "stream": binding["stream"], "sensor": binding["sensor"], "intervals": intervals,
            "notice": "到包间隔包含边界相邻包；序号跳跃可能由乱序、抓包或源重启造成，不能确定网络根因。"}


def associate(manifest_path, record):
    manifest_path = Path(manifest_path)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("kind") != "gap_evidence" or manifest.get("schema_version") != "1.0":
        raise ValueError("需要 gap_evidence 1.0 关联清单")
    recordings = manifest.get("recordings")
    if not isinstance(recordings, list):
        raise ValueError("关联清单缺少 recordings")
    matches = [r for r in recordings if isinstance(r, dict) and r.get("audio_sha256") == record["audio_sha256"]]
    if len(matches) > 1:
        raise ValueError("关联清单重复绑定同一录音")
    if not matches:
        return {"status": "not_bound"}
    result = {"status": "available", "sources": [], "manifest_sha256": sha256(manifest_path)}
    for kind, function in (("rtp", rtp), ("nisqa", nisqa)):
        bindings = matches[0].get(kind, [])
        if not isinstance(bindings, list) or len(bindings) > 100:
            raise ValueError("每类旁证须为不超过 100 项的列表")
        for binding in bindings:
            try:
                if not isinstance(binding, dict):
                    raise ValueError("旁证绑定须为对象")
                result["sources"].append(function(manifest_path.parent, binding, record))
            except (ValueError, OSError, KeyError, TypeError) as error:
                result["sources"].append({"kind": kind, "status": "error", "error": str(error)})
                result["status"] = "error"
    return result
