"""Offline benchmark contract. Optional VAD/native modules load only at runtime."""
import math

DEFAULTS = {"backend": "webrtcvad", "threshold_db": -45.0, "minimum_ms": 60,
            "join_gap_ms": 100, "stop_silence_ms": 200}


def fields(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"{name} 对象或字段无效")


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 须为 {low}–{high} 的有限数字")
    return value


def configuration(value):
    fields(value, {"case_id", "language", "tags", "detector", "windows", "expectations"}, "benchmark")
    case_id = value.get("case_id")
    if not isinstance(case_id, str) or not 1 <= len(case_id) <= 160:
        raise ValueError("benchmark.case_id 必须是非空文本")
    language = value.get("language", "zh-CN")
    if not isinstance(language, str) or not 1 <= len(language) <= 40:
        raise ValueError("benchmark.language 无效")
    tags = value.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or not 1 <= len(t) <= 80 for t in tags):
        raise ValueError("benchmark.tags 必须是最多30个短文本")
    detector = value.get("detector", {})
    fields(detector, DEFAULTS, "benchmark.detector")
    detector = dict(DEFAULTS, **detector)
    if detector["backend"] not in ("energy", "webrtcvad"):
        raise ValueError("检测器须为 energy 或 webrtcvad")
    number(detector["threshold_db"], -100, -1, "threshold_db")
    number(detector["minimum_ms"], 20, 1000, "minimum_ms")
    number(detector["join_gap_ms"], 0, 1000, "join_gap_ms")
    number(detector["stop_silence_ms"], 20, 2000, "stop_silence_ms")
    windows = value.get("windows", [])
    if not isinstance(windows, list) or len(windows) > 100:
        raise ValueError("benchmark.windows 须为最多100个时间窗")
    ids = set()
    for window in windows:
        fields(window, {"id", "start_s", "end_s"}, "window")
        if not isinstance(window.get("id"), str) or not window["id"] or window["id"] in ids:
            raise ValueError("window.id 缺失或重复")
        ids.add(window["id"])
        start = number(window.get("start_s"), 0, 1020, "window.start_s")
        end = number(window.get("end_s"), 0, 1020, "window.end_s")
        if end <= start:
            raise ValueError("window.end_s 必须晚于 start_s")
    expectations = value.get("expectations", {})
    fields(expectations, {"first_audio_max_ms", "response_max_ms", "stop_max_ms", "expect_interrupt"}, "expectations")
    for key, val in expectations.items():
        if key == "expect_interrupt":
            if type(val) is not bool:
                raise ValueError("expect_interrupt 必须是布尔值")
        else:
            number(val, 0, 900000, key)
    return {"case_id": case_id, "language": language, "tags": tags, "detector": detector,
            "windows": windows, "expectations": expectations}
