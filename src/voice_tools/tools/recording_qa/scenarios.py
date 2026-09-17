"""合成声学夹具；预期手工声明，禁止调用检测器生成答案。"""
import numpy as np

from voice_tools.audio.io import write_wav
from voice_tools.core.files import new_output, sha256, write_json

EXPECTED = {
    "normal": ["OUTPUT_NEEDS_REVIEW"],
    "missing": ["NO_OUTPUT_CANDIDATE"],
    "late": ["LATE_OUTPUT_CANDIDATE"],
    "long_user": [],
    "hangup": ["CENSORED"],
    "tool_wait": ["EXCLUDED"],
    "interrupt": ["CENSORED"],
    "spike": ["NO_OUTPUT_CANDIDATE"],
    "noise": ["OUTPUT_NEEDS_REVIEW"],
    "dc": ["NO_OUTPUT_CANDIDATE"],
    "quiet": ["NO_OUTPUT_CANDIDATE"],
    "unknown_role": ["NO_OUTPUT_CANDIDATE"],
    "unknown_phase": ["NO_OUTPUT_CANDIDATE"],
    "duplicate": ["INSUFFICIENT_EVIDENCE"],
    "swapped": ["NO_OUTPUT_CANDIDATE"],
    "greeting": ["NO_OUTPUT_CANDIDATE"],
    "no_response_needed": ["EXCLUDED"],
    "multi_turn": ["OUTPUT_NEEDS_REVIEW", "NO_OUTPUT_CANDIDATE"],
    "no_events": ["NO_OUTPUT_CANDIDATE"],
    "mono": [],
}


def fixture(case, rate=8000, seed=20260916):
    if case not in EXPECTED:
        raise ValueError(f"未知场景：{case}")
    if rate not in (8000, 16000, 32000, 48000):
        raise ValueError("合成采样率必须为 8/16/32/48 kHz")
    data = np.zeros((18 * rate, 2), dtype=np.float32)

    def tone(channel, start, end, amplitude=0.12, hz=440):
        a, b = round(start * rate), round(end * rate)
        t = np.arange(b - a) / rate
        data[a:b, channel] += amplitude * np.sin(2 * np.pi * hz * t)

    tone(1, 0, 2, hz=330)  # IVR 提示音不代表 AI 应答。
    tone(0, 4, 6)
    events = {"schema_version": "1.0", "system_channel": 1, "channel_verified": True,
              "ai_start_s": 3, "ai_end_s": 18,
              "opportunities": [{"id": "turn-1", "at_s": 6, "expects_response": True}],
              "user_speech": [{"start_s": 4, "end_s": 6}], "exclusions": []}
    if case == "normal":
        tone(1, 7, 9, hz=330)
    elif case == "late":
        tone(1, 12, 14, hz=330)
    elif case == "long_user":
        tone(0, 6, 18)
        events["opportunities"] = []
        events["user_speech"] = [{"start_s": 4, "end_s": 18}]
    elif case == "hangup":
        events["ai_end_s"] = 9
    elif case == "tool_wait":
        events["exclusions"] = [{"start_s": 5.5, "end_s": 15, "reason": "已告知用户的工具等待"}]
    elif case == "interrupt":
        tone(0, 8, 10)
        events["user_speech"].append({"start_s": 8, "end_s": 10})
    elif case == "spike":
        tone(1, 8, 8.02)
    elif case == "noise":
        data[6 * rate:17 * rate, 1] += np.random.default_rng(seed).normal(0, 0.03, 11 * rate)
    elif case == "dc":
        data[6 * rate:, 1] += 0.1
    elif case == "quiet":
        tone(1, 7, 9, amplitude=0.001)
    elif case == "unknown_role":
        events["channel_verified"] = False
    elif case == "unknown_phase":
        events.pop("ai_start_s")
    elif case == "duplicate":
        data[:, 1] = data[:, 0]
    elif case == "swapped":
        data = data[:, ::-1].copy()
        events["system_channel"] = 0
    elif case == "greeting":
        data[3 * rate:] = 0
        events["opportunities"] = [{"id": "greeting", "at_s": 3}]
        events["user_speech"] = []
    elif case == "no_response_needed":
        events["opportunities"][0]["expects_response"] = False
    elif case == "multi_turn":
        tone(1, 7, 9, hz=330)
        tone(0, 10, 11)
        events["user_speech"].append({"start_s": 10, "end_s": 11})
        events["opportunities"].append({"id": "turn-2", "at_s": 11})
    elif case == "no_events":
        events = None
    elif case == "mono":
        data = data.mean(axis=1, keepdims=True)
    return data, events


def generate(output, rate=8000, seed=20260916):
    # 在创建输出前校验参数。
    fixture("normal", rate, seed)
    output = new_output(output)
    cases = []
    for case, expected in EXPECTED.items():
        samples, events = fixture(case, rate, seed)
        audio_path = output / (case + ".wav")
        write_wav(audio_path, samples, rate)
        if events is not None:
            write_json(output / (case + ".events.json"), events)
        cases.append({"id": case, "audio": audio_path.name, "audio_sha256": sha256(audio_path),
                      "expected_statuses": expected, "kind": "synthetic_acoustic"})
    manifest = {"schema_version": "1.0", "seed": seed, "sample_rate": rate,
                "notice": "正弦波/噪声不是人声；只验证工程逻辑，不是黄金集或生产准确率。", "cases": cases}
    write_json(output / "manifest.json", manifest)
    return manifest
