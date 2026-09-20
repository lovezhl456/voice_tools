"""Offline, evidence-bounded assertions for a single received call leg."""
import wave
from pathlib import Path

import numpy as np


KINDS = {
    "response_code": {"codes"},
    "received_rtp": {"min_packets"},
    "effective_audio": {"min_duration_s", "threshold_dbfs"},
    "dtmf": {"digits", "match"},
    "tone": {"frequencies_hz", "min_duration_s", "threshold_dbfs", "min_power_ratio"},
}


def validate_assertions(value):
    # Import locally to keep scenario validation independent of the evaluator.
    from .scenario import DIGITS, number, object_fields, text
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("assertions 必须是最多 64 项的列表")
    result, ids = [], set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or raw["type"] not in KINDS:
            raise ValueError(f"assertions[{index}].type 无效")
        kind = raw["type"]
        item = dict(object_fields(raw, KINDS[kind] | {"id", "type"}, f"assertions[{index}]"))
        item["id"] = text(item.get("id", f"assertion_{index + 1}"), "assertion.id", 128)
        if item["id"] in ids:
            raise ValueError("assertion.id 不得重复")
        ids.add(item["id"])
        if kind == "response_code":
            codes = item.get("codes")
            if not isinstance(codes, list) or not 1 <= len(codes) <= 500:
                raise ValueError("response_code.codes 必须是非空 SIP 最终应答码列表")
            item["codes"] = sorted(set(number(c, 200, 699, "response code", True) for c in codes))
        elif kind == "received_rtp":
            item["min_packets"] = number(item.get("min_packets", 1), 1, 100000000, "min_packets", True)
        elif kind == "dtmf":
            digits = text(item.get("digits"), "assertion.digits", 128)
            if any(d not in DIGITS for d in digits):
                raise ValueError("assertion.digits 只能使用 0–9、*、#、A–D")
            item["match"] = item.get("match", "exact")
            if item["match"] not in ("exact", "contains"):
                raise ValueError("dtmf.match 必须为 exact 或 contains")
        else:
            item["min_duration_s"] = number(item.get("min_duration_s"), .02 if kind == "effective_audio" else .04, 900, "min_duration_s")
            item["threshold_dbfs"] = number(item.get("threshold_dbfs", -40), -90, 0, "threshold_dbfs")
            if kind == "tone":
                freqs = item.get("frequencies_hz")
                if not isinstance(freqs, list) or not 1 <= len(freqs) <= 4:
                    raise ValueError("tone.frequencies_hz 必须含 1–4 个同时出现的频率")
                item["frequencies_hz"] = sorted(number(f, 100, 3500, "frequency") for f in freqs)
                if any(b - a < 25 for a, b in zip(item["frequencies_hz"], item["frequencies_hz"][1:])):
                    raise ValueError("tone 频率间隔至少为 25 Hz")
                item["min_power_ratio"] = number(item.get("min_power_ratio", .6), .5, 1, "min_power_ratio")
        result.append(item)
    return result


def audio_frames(path, size):
    """Stream bounded PCM frames; a broken tail invalidates the entire measurement."""
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getcomptype()) != (1, 2, 8000, "NONE"):
            raise ValueError("需要单声道 8 kHz PCM16 接收录音")
        remaining = reader.getnframes()
        if not 0 < remaining <= 1020 * 8000:
            raise ValueError("接收录音为空或超出时长上限")
        while remaining:
            count = min(size, remaining)
            data = reader.readframes(count)
            if len(data) != count * 2:
                raise ValueError("接收录音截断")
            remaining -= count
            yield np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768


def measure_audio(path, spec):
    tone = spec["type"] == "tone"
    size = 320 if tone else 160  # 40 ms tone / 20 ms AC energy windows.
    floor = 10 ** (spec["threshold_dbfs"] / 20)
    active = longest = current = total = 0
    if tone:
        t = np.arange(size) / 8000
        basis = np.column_stack([fn(2 * np.pi * f * t) for f in spec["frequencies_hz"] for fn in (np.sin, np.cos)])
        inverse = np.linalg.pinv(basis)
    for samples in audio_frames(path, size):
        total += len(samples)
        samples -= samples.mean()  # DC offset is not effective audio.
        energy = float(np.mean(samples * samples))
        hit = energy >= floor * floor
        if tone:
            hit = hit and len(samples) == size
            if hit:
                coeff = inverse @ samples
                residual = samples - basis @ coeff
                ratio = max(0.0, 1 - float(np.mean(residual * residual)) / energy)
                powers = np.sum(coeff.reshape(-1, 2) ** 2, axis=1) / 2
                # Every configured frequency must be present, not merely their sum.
                hit = ratio >= spec["min_power_ratio"] and bool(np.all(powers >= energy * .1))
        if hit:
            active += len(samples)
            current += len(samples)
            longest = max(longest, current)
        else:
            current = 0
    return {"duration_s": total / 8000, "active_duration_s": active / 8000,
            "longest_tone_s": longest / 8000} if tone else {"duration_s": total / 8000, "active_duration_s": active / 8000}


def evaluate(specs, result, output):
    """Never infer receive evidence from transmitted actions or WAV file length."""
    rows = []
    call = result.get("call")
    evidence = call.get("assertion_evidence") if isinstance(call, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}
    complete = result.get("execution_status", result.get("status")) == "completed"
    for spec in specs:
        kind = spec["type"]
        row = {"id": spec["id"], "type": kind, "expected": {k: v for k, v in spec.items() if k not in ("id", "type")},
               "actual": None, "status": "insufficient_evidence", "evidence": [], "reason": "缺少可用接收证据"}
        passed = None
        if kind == "response_code":
            code = evidence.get("invite_final_code")
            row["actual"] = {"code": code}
            row["evidence"] = ["result.json:call.assertion_evidence.invite_final_code", "events.jsonl:invite_response"]
            if type(code) is int and 200 <= code <= 699:
                passed = code in spec["codes"]
        elif kind == "received_rtp":
            packets = evidence.get("rx_rtp_packets_lower_bound")
            row["actual"] = {"packets_lower_bound": packets}
            row["evidence"] = ["result.json:call.assertion_evidence.rx_rtp_packets_lower_bound", "PJSUA2 getStreamStat().rtcp.rxStat.pkt"]
            if type(packets) is int and packets >= 0:
                if packets >= spec["min_packets"]:
                    passed = True
                elif complete and evidence.get("rtp_final_sample"):
                    passed = False
        elif kind == "dtmf":
            digits = evidence.get("received_dtmf")
            row["actual"] = {"digits": digits}
            row["evidence"] = ["events.jsonl:dtmf_received", "result.json:call.assertion_evidence.received_dtmf"]
            if isinstance(digits, str):
                match = digits == spec["digits"] if spec["match"] == "exact" else spec["digits"] in digits
                # Exact matching requires a complete observation window.
                if complete or (spec["match"] == "contains" and match):
                    passed = match
        else:
            row["evidence"] = ["rx.wav", "20ms AC RMS" if kind == "effective_audio" else "40ms simultaneous sinusoid fit"]
            try:
                measured = measure_audio(Path(output) / "rx.wav", spec)
                row["actual"] = measured
                duration = measured["longest_tone_s" if kind == "tone" else "active_duration_s"]
                if duration + 1e-9 >= spec["min_duration_s"]:
                    passed = True
                elif complete:
                    passed = False
            except (OSError, EOFError, wave.Error, ValueError) as exc:
                row["reason"] = str(exc)
        if passed is not None:
            row.update(status="passed" if passed else "failed", reason="符合预期" if passed else "实测值不符合预期")
        rows.append(row)
    states = {row["status"] for row in rows}
    status = ("not_configured" if not rows else "failed" if "failed" in states else
              "insufficient_evidence" if "insufficient_evidence" in states else "passed")
    return {"schema_version": "1.0", "status": status, "items": rows,
            "counts": {state: sum(r["status"] == state for r in rows) for state in ("passed", "failed", "insufficient_evidence")}}


def apply_assertions(plan, result, output):
    result["execution_status"] = result["status"]
    result["assertions"] = evaluate(plan.get("assertions", []), result, output)
    if result["status"] == "completed" and result["assertions"]["status"] in ("failed", "insufficient_evidence"):
        result["status"] = "failed"
        result["error"] = {"code": "ASSERTIONS_NOT_PASSED", "message": "测试断言失败或证据不足；检查 assertions.items"}
