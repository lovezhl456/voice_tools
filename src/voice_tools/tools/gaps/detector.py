"""Independent acoustic symptoms, event filtering and stable interval identity."""
from dataclasses import asdict, dataclass
import hashlib
import json

import numpy as np

from voice_tools.audio.activity import detect_activity
from voice_tools.core.output_events import number, read_extension

MAX_GAPS = 2000


def identity(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def detection_metadata(metadata):
    """Hash only rule inputs; provenance paths and added evidence are not identity."""
    result = {key: metadata[key] for key in (
        "schema_version", "system_channel", "channel_verified", "ai_start_s", "ai_end_s",
        "user_speech", "exclusions") if key in metadata}
    extension = metadata.get("output_events")
    if extension is not None:
        fields = ("id", "type", "utterance_id", "start_s", "end_s", "at_s", "allows_silence")
        result["output_events"] = {
            "schema_version": extension["schema_version"], "audio_sha256": extension.get("audio_sha256"),
            "alignment_verified": extension.get("alignment_verified", False),
            "events": sorted(({k: event[k] for k in fields if k in event} for event in extension["events"]),
                             key=lambda event: event["id"])}
    return result


@dataclass(frozen=True)
class Config:
    system_channel: int = 1
    channels_verified: bool = False
    backend: str = "energy"
    threshold_db: float = -45
    dead_air_ms: float = 800
    micro_min_ms: float = 50
    micro_max_ms: float = 300
    near_silence_db: float = -60
    cluster_window_ms: float = 1500
    cluster_min_count: int = 3

    def validate(self):
        if type(self.system_channel) is not int or self.system_channel not in (0, 1):
            raise ValueError("system_channel 须为 0 或 1")
        if type(self.channels_verified) is not bool or self.backend not in ("energy", "webrtcvad"):
            raise ValueError("声道验证状态或活动检测器无效")
        number(self.threshold_db, "threshold_db", -100, -1)
        number(self.near_silence_db, "near_silence_db", -120, self.threshold_db - 1)
        number(self.dead_air_ms, "dead_air_ms", 100, 120000)
        number(self.micro_min_ms, "micro_min_ms", 10, 1000)
        number(self.micro_max_ms, "micro_max_ms", self.micro_min_ms, self.dead_air_ms - 1)
        number(self.cluster_window_ms, "cluster_window_ms", self.micro_max_ms, 10000)
        if type(self.cluster_min_count) is not int or not 2 <= self.cluster_min_count <= 100:
            raise ValueError("cluster_min_count 须为 2–100 的整数")


def fine_levels(track, rate):
    """5 ms centered RMS, bounded temporary storage instead of another full track."""
    frame = max(1, round(rate * .005))
    levels = []
    for offset in range(0, len(track), frame * 512):
        block = track[offset:offset + frame * 512]
        complete = len(block) // frame * frame
        if complete:
            parts = block[:complete].reshape(-1, frame)
            centered = parts - parts.mean(axis=1, keepdims=True)
            rms = np.sqrt(np.mean(centered ** 2, axis=1))
            levels.extend(20 * np.log10(np.maximum(rms, 1e-12)))
    return np.asarray(levels), frame / rate


def quiet_intervals(levels, frame_s, threshold):
    edges = np.diff(np.r_[False, levels < threshold, False].astype(np.int8))
    return [(int(a), int(b)) for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def spans(metadata, key, duration):
    values = metadata.get(key, [])
    if not isinstance(values, list):
        raise ValueError(f"{key} 须为区间列表")
    for span in values:
        if not isinstance(span, dict):
            raise ValueError(f"{key} 区间无效")
        start = number(span.get("start_s"), key + ".start_s", 0, duration)
        end = number(span.get("end_s"), key + ".end_s", start, duration)
        if end <= start:
            raise ValueError(f"{key} 区间为空")
    return values


def raw_gaps(agent_spans, levels, frame_s, config):
    output = []
    for left, right in zip(agent_spans, agent_spans[1:]):
        if (right[0] - left[1]) * 1000 + 1e-6 >= config.dead_air_ms:
            output.append({"type": "dead_air", "start_s": left[1], "end_s": right[0]})
    flank = max(1, round(.03 / frame_s))
    for a, b in quiet_intervals(levels, frame_s, config.near_silence_db):
        duration = (b - a) * frame_s * 1000
        if not config.micro_min_ms - 1e-6 <= duration <= config.micro_max_ms + 1e-6:
            continue
        before, after = levels[max(0, a - flank):a], levels[b:b + flank]
        if len(before) == flank and len(after) == flank and np.median(before) >= config.threshold_db and np.median(after) >= config.threshold_db:
            output.append({"type": "micro_dropout", "start_s": a * frame_s, "end_s": b * frame_s})
    return output


def terminal_gaps(agent_spans, events, duration, threshold):
    output = []
    for expected in (e for e in events if e["type"] == "tts_expected"):
        start, end = expected["start_s"], expected["end_s"]
        for event in events:
            if event["type"] in ("tts_cancel", "tts_end") and event["utterance_id"] == expected["utterance_id"]:
                end = min(end, event["at_s"])
        audible = [(a, min(b, end)) for a, b in agent_spans if b > start and a < end]
        if not audible:
            continue  # Missing initial output belongs to response-opportunity QA.
        last = max(b for _, b in audible)
        stop = min(end, duration)
        if stop - last <= 0:
            continue
        status = "CANDIDATE" if end <= duration and (stop - last) * 1000 + 1e-6 >= threshold else "CENSORED"
        output.append({"type": "terminal_interruption", "start_s": last, "end_s": stop,
                       "status": status, "utterance_id": expected["utterance_id"],
                       "reason_code": "expected_output_missing" if status == "CANDIDATE" else "incomplete_expected_window"})
    return output


def exclusions_for(gap, callers, metadata, events, trusted, duration):
    excluded = []
    start, end = gap["start_s"], gap["end_s"]
    for a, b in callers:
        if b > start and a < end:
            onset = max(a, start)
            # Once the user takes the turn, later silence is an answer-delay question.
            excluded.append((start if onset - start <= .15 else onset, end, "user_turn_changed"))
    if not trusted:
        return excluded
    excluded.extend((e["start_s"], e["end_s"], "business_exclusion") for e in metadata.get("exclusions", []))
    for e in events:
        if e["type"] == "tool_wait" and e.get("allows_silence", False):
            excluded.append((e["start_s"], e["end_s"], "allowed_tool_wait"))
        elif e["type"] == "user_interrupt" and e["start_s"] < end and e["end_s"] > start:
            excluded.append((e["start_s"], end, "explicit_user_interrupt"))
    expected = [e for e in events if e["type"] == "tts_expected"]
    if expected:
        cursor = start
        for window in sorted(expected, key=lambda e: e["start_s"]):
            a, b = max(start, window["start_s"]), min(end, window["end_s"])
            if a >= b:
                continue
            if a > cursor:
                excluded.append((cursor, a, "outside_output_turn"))
            for event in events:
                if event["type"] in ("tts_cancel", "tts_end") and event["utterance_id"] == window["utterance_id"]:
                    excluded.append((max(a, event["at_s"]), b, "output_turn_finished"))
            cursor = b
        if cursor < end:
            excluded.append((cursor, end, "outside_output_turn"))
    if metadata.get("ai_start_s") is not None:
        excluded.append((0, metadata["ai_start_s"], "before_ai_takeover"))
    excluded.append((metadata.get("ai_end_s", duration), duration, "after_ai_exit"))
    return excluded


def split_gap(gap, exclusions, fingerprint, trusted, config, windows=()):
    start, end = gap["start_s"], gap["end_s"]
    boundaries = [v for a, b, _ in exclusions for v in (a, b)]
    boundaries.extend(v for window in windows for v in (window["start_s"], window["end_s"]))
    cuts = sorted({start, end, *(max(start, min(end, v)) for v in boundaries)})
    rows = []
    for a, b in zip(cuts, cuts[1:]):
        if b <= a:
            continue
        reasons = sorted({why for x, y, why in exclusions if x < b and y > a})
        minimum = config.micro_min_ms if gap["type"] == "micro_dropout" else config.dead_air_ms
        status = gap.get("status", "CANDIDATE")
        reason = gap.get("reason_code", "bounded_acoustic_gap")
        if reasons:
            status, reason = "EXCLUDED", ";".join(reasons)
        elif (b - a) * 1000 + 1e-6 < minimum:
            status, reason = "CENSORED", "remaining_interval_below_threshold"
        row = {**gap, "start_s": round(a, 6), "end_s": round(b, 6),
               "original_start_s": round(start, 6), "original_end_s": round(end, 6),
               "duration_ms": round((b-a)*1000, 3), "status": status, "reason_code": reason,
               "evidence_level": "event_aligned" if trusted else "acoustic_only"}
        matching = [window for window in windows if window["start_s"] <= a and window["end_s"] >= b]
        if len(matching) == 1:
            row["utterance_id"] = matching[0]["utterance_id"]
        row["id"] = identity([fingerprint, row])[:24]
        rows.append(row)
    return rows


def clusters(rows, fingerprint, config):
    micros = sorted((g for g in rows if g["type"] == "micro_dropout" and g["status"] == "CANDIDATE"), key=lambda g: g["start_s"])
    result, index = [], 0
    while index < len(micros):
        end = index + 1
        while end < len(micros) and (micros[end]["start_s"] - micros[index]["start_s"]) * 1000 <= config.cluster_window_ms:
            if micros[end].get('utterance_id') != micros[index].get('utterance_id'):
                break
            if any(g['status'] == 'EXCLUDED' and g['start_s'] < micros[end]['start_s'] and g['end_s'] > micros[end-1]['end_s'] for g in rows):
                break
            end += 1
        members = micros[index:end]
        if len(members) >= config.cluster_min_count:
            first, last = members[0], members[-1]
            result.append({"id": identity([fingerprint, [g["id"] for g in members]])[:24],
                "type": "clustered_short_gaps", "start_s": first["start_s"], "end_s": last["end_s"],
                "original_start_s": first["start_s"], "original_end_s": last["end_s"],
                "duration_ms": round((last["end_s"]-first["start_s"])*1000, 3), "members": [g["id"] for g in members],
                "status": "CANDIDATE", "reason_code": "repeated_short_gaps", "evidence_level": first["evidence_level"]})
            index = end
        else:
            index += 1
    return result


def analyze(audio, audio_sha256, metadata=None, config=None):
    config = config or Config()
    config.validate()
    samples = audio.samples
    if (not isinstance(samples, np.ndarray) or samples.ndim != 2 or samples.shape[1] not in (1, 2)
            or not len(samples) or not 8000 <= audio.sample_rate <= 48000
            or audio.duration_s > 3600 or samples.nbytes > 512 * 1024**2):
        raise ValueError("音频须为 8–48 kHz 单/双轨，时长不超过 3600 秒，样本不超过 512 MiB")
    for offset in range(0, len(samples), 65536):
        if not np.isfinite(samples[offset:offset + 65536]).all():
            raise ValueError("音频样本含非有限值")
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict) or metadata.get("schema_version", "1.0") != "1.0":
        raise ValueError("事件文件必须是 schema_version=1.0 的对象")
    if "system_channel" in metadata and (type(metadata["system_channel"]) is not int or metadata["system_channel"] != config.system_channel):
        raise ValueError("事件与 system-channel 不一致")
    verified = metadata.get("channel_verified", config.channels_verified)
    if type(verified) is not bool:
        raise ValueError("channel_verified 须为布尔值")
    for key in ("ai_start_s", "ai_end_s"):
        if key in metadata:
            number(metadata[key], key, 0, audio.duration_s)
    if metadata.get("ai_start_s", 0) > metadata.get("ai_end_s", audio.duration_s):
        raise ValueError("AI 接管和退出时间倒置")
    spans(metadata, "user_speech", audio.duration_s)
    spans(metadata, "exclusions", audio.duration_s)
    events, aligned, warnings = read_extension(metadata, audio.duration_s, audio_sha256)
    trusted = aligned and verified
    activity, health = detect_activity(audio, threshold_db=config.threshold_db, minimum_s=.1, gap_s=.03, backend=config.backend)
    sampled = samples[::max(1, len(samples) // 100000)]
    health["correlation"] = None
    if samples.shape[1] == 2 and np.min(np.std(sampled, axis=0)) > 1e-6:
        health["correlation"] = round(float(np.corrcoef(sampled.T)[0, 1]), 6)
        if abs(health["correlation"]) > .98:
            warnings.append("双轨同步相关性高；仅供复核，不能确认串音、角色或故障概率")
    fingerprint = identity({"algorithm": "output-gaps-1", "audio": audio_sha256,
                            "events": detection_metadata(metadata), "config": asdict(config)})
    result = {"duration_s": audio.duration_s, "sample_rate": audio.sample_rate, "channels": audio.samples.shape[1],
              "config": asdict(config), "fingerprint": fingerprint, "channel_verified": verified,
              "health": health, "warnings": warnings, "gaps": [], "candidate_count": 0, "cluster_count": 0,
              "status": "NO_CANDIDATES", "interpretation": "仅为声学候选和旁证，不证明故障根因或业务通过。"}
    if not verified:
        warnings.append("声道角色未核实")
    if metadata.get("output_events") and not trusted:
        warnings.append("输出事件未完成录音时间/角色验证，仅作旁证，不应用事件过滤或永久中断检测")
    if audio.samples.shape[1] != 2 or health["duplicate_channels"] or not any(activity):
        result["status"] = "INSUFFICIENT_EVIDENCE"
        warnings.append("单轨、全静音或重复声道无法建立双方独立时间轴")
        return result
    system, caller = activity[config.system_channel], activity[1-config.system_channel]
    if not verified:
        caller = []  # An unverified role cannot silently remove an acoustic candidate.
    if trusted and "user_speech" in metadata:
        caller = [(s["start_s"], s["end_s"]) for s in metadata["user_speech"]]
    levels, frame_s = fine_levels(audio.samples[:, config.system_channel], audio.sample_rate)
    raw = raw_gaps(system, levels, frame_s, config)
    if trusted:
        for terminal in terminal_gaps(system, events, audio.duration_s, config.dead_air_ms):
            # A bounded acoustic interval already describes this missing output.
            if not any(g["start_s"] <= terminal["start_s"] and g["end_s"] >= terminal["end_s"] for g in raw):
                raw.append(terminal)
    elif system and audio.duration_s-system[-1][1] >= config.dead_air_ms/1000:
        warnings.append("输出结束后存在尾部静音，但缺少有效播放预期，无法判定永久中断")
    rows = []
    windows = [event for event in events if event["type"] == "tts_expected"] if trusted else []
    truncated = len(raw) > MAX_GAPS
    for gap in sorted(raw, key=lambda g: (g["start_s"], g["end_s"]))[:MAX_GAPS]:
        if trusted and 'utterance_id' not in gap:
            matches = [e for e in events if e['type'] == 'tts_expected' and e['start_s'] <= gap['start_s'] and e['end_s'] >= gap['end_s']]
            if len(matches) == 1:
                gap['utterance_id'] = matches[0]['utterance_id']
        rows.extend(split_gap(gap, exclusions_for(gap, caller, metadata, events, trusted, audio.duration_s), fingerprint, trusted, config, windows))
        if len(rows) > MAX_GAPS:
            truncated = True
            rows = rows[:MAX_GAPS]
            break
    if truncated:
        result["processing_error"] = f"间隙区间超过 {MAX_GAPS} 条额度，仅保留前部结果；请分段处理"
        warnings.append(result["processing_error"])
    grouped = clusters(rows, fingerprint, config)
    rows.extend(grouped)
    result.update(gaps=sorted(rows, key=lambda g:(g["start_s"], g["type"], g["end_s"])),
                  candidate_count=sum(g["status"] == "CANDIDATE" and "members" not in g for g in rows), cluster_count=len(grouped))
    if rows:
        result["status"] = "REVIEW_REQUIRED"
    if not system:
        warnings.append("AI 轨未检出活动；无首次回答应使用 qa 的应答机会检测")
    return result
