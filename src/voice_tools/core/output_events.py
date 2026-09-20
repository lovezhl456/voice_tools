"""Versioned output events shared by audio preparation and gap detection."""
import copy
import math
import re

TYPES = {"tool_wait", "user_interrupt", "tts_expected", "tts_playback", "tts_cancel", "tts_end"}
POINTS = {"tts_cancel", "tts_end"}


def number(value, name, low=0, high=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 须为 {low}–{high} 的有限数字")
    return float(value)


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def read_extension(metadata, duration, audio_sha256):
    extension = metadata.get("output_events")
    if extension is None:
        return [], False, []
    if not isinstance(extension, dict) or extension.get("schema_version") != "1.0":
        raise ValueError("output_events 须为 schema_version=1.0 的对象")
    claimed = extension.get("audio_sha256")
    if claimed is not None and (not digest(claimed) or claimed != audio_sha256):
        raise ValueError("输出事件的录音摘要不匹配")
    verified = extension.get("alignment_verified", False)
    if type(verified) is not bool:
        raise ValueError("alignment_verified 须为布尔值")
    if verified and (not claimed or not isinstance(extension.get("source"), str) or not extension["source"].strip()):
        raise ValueError("已对齐输出事件需要录音摘要和 source")
    events = extension.get("events")
    if not isinstance(events, list) or len(events) > 100000:
        raise ValueError("output_events.events 须为不超过 100000 项的列表")
    seen, expected, terminals = set(), {}, set()
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in TYPES:
            raise ValueError("未知输出事件类型")
        ident = event.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError("输出事件 id 必须非空且唯一")
        seen.add(ident)
        kind = event["type"]
        if kind.startswith("tts_"):
            utterance = event.get("utterance_id")
            if not isinstance(utterance, str) or not utterance.strip():
                raise ValueError("TTS 事件缺少 utterance_id")
        if kind in POINTS:
            number(event.get("at_s"), "at_s", 0, duration)
            key = event["utterance_id"]
            if key in terminals:
                raise ValueError("同一 utterance 只能有一个取消或结束事件")
            terminals.add(key)
        else:
            start = number(event.get("start_s"), "start_s", 0, duration)
            end = number(event.get("end_s"), "end_s", start)
            if end <= start or (kind != "tts_expected" and end > duration):
                raise ValueError("输出事件区间为空或越出录音")
            if kind == "tts_expected":
                if event["utterance_id"] in expected:
                    raise ValueError("同一 utterance 只能有一个预期输出窗口")
                expected[event["utterance_id"]] = event
            if kind == "tool_wait" and type(event.get("allows_silence", False)) is not bool:
                raise ValueError("allows_silence 须为布尔值")
    warnings = []
    windows = sorted(expected.values(), key=lambda e: e['start_s'])
    if any(a['end_s'] > b['start_s'] for a, b in zip(windows, windows[1:])):
        warnings.append("TTS 预期窗口重叠；本文件不应用事件过滤")
    for event in events:
        if not event["type"].startswith("tts_") or event["type"] == "tts_expected":
            continue
        parent = expected.get(event["utterance_id"])
        at = event.get("at_s", event.get("start_s"))
        end = event.get("end_s", at)
        if parent is None or not parent["start_s"] <= at <= end <= parent["end_s"]:
            warnings.append("TTS 事件缺少匹配的预期窗口或时间冲突；本文件不应用事件过滤")
        if event["type"] == "tts_playback":
            if any(e["type"] in POINTS and e["utterance_id"] == event["utterance_id"]
                   and e["at_s"] < end for e in events):
                warnings.append("TTS 完成后仍有播放事件；本文件不应用事件过滤")
    return events, verified and not warnings, warnings


def rebind_prepared(metadata, source_hash, target_hash, event_hash):
    """Only a verified, identity time mapping permits rebinding the audio digest."""
    result = copy.deepcopy(metadata)
    extension = result.get("output_events")
    if extension is not None:
        if not isinstance(extension, dict) or extension.get("audio_sha256") not in (None, source_hash):
            raise ValueError("源输出事件摘要不匹配")
        extension["source_audio_sha256"] = source_hash
        extension["source_events_sha256"] = event_hash
        if extension.get("audio_sha256"):
            extension["audio_sha256"] = target_hash
    return result
