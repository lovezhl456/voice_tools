"""Measure source audio age in a controlled echo recording."""
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np

from voice_tools import __version__
from voice_tools.audio.io import read_wav
from voice_tools.core.files import new_output, sha256, write_json

MATCH_RATE = 4000
WINDOW_S = .22
MIN_CORRELATION = .77
MIN_RMS = .003
MAX_WINDOWS = 2000


def resample(samples, rate):
    """Average time bins before decimation, including non-integer ratios."""
    if rate % MATCH_RATE == 0:
        width = rate // MATCH_RATE
        usable = len(samples) // width * width
        return samples[:usable].reshape(-1, width).mean(axis=1, dtype=np.float64)
    edges = np.arange(len(samples) * MATCH_RATE // rate + 1, dtype=np.float64) * (rate / MATCH_RATE)
    cumulative = np.r_[0, np.cumsum(samples, dtype=np.float64)]
    indices = np.minimum(edges.astype(np.intp), len(samples))
    fractional = (edges - indices) * samples[np.minimum(indices, len(samples) - 1)]
    return np.diff(cumulative[indices] + fractional) * (MATCH_RATE / rate)


def quiet_intervals(tracks, rate, pause_ms, silence_db):
    frame = round(rate * .005)
    usable = len(tracks) // frame
    if not usable:
        return []
    windows = tracks[:usable * frame].reshape(usable, frame, 2)
    levels = np.empty((usable, 2), dtype=np.float32)
    for start in range(0, usable, 1000):
        block = windows[start:start + 1000]
        levels[start:start + len(block)] = np.std(block, axis=1)
    quiet = levels[:, 1] < 10 ** (silence_db / 20)
    changes = np.flatnonzero(np.diff(np.r_[False, quiet, False]))
    pauses = []
    for start, end in changes.reshape(-1, 2):
        duration = (end - start) * frame * 1000 / rate
        if start == 0 or end == usable or duration < pause_ms:
            continue
        if float(np.median(levels[start:end, 0])) >= MIN_RMS:
            pauses.append({"start_s": round(start * frame / rate, 6), "end_s": round(end * frame / rate, 6),
                           "duration_ms": round(duration, 3)})
    return pauses


def match_source(reference, target, at_s, max_age_s):
    size = len(target)
    at = int(at_s * MATCH_RATE + 1e-7)
    last = min(at, len(reference) - size)
    first = max(0, at - round(max_age_s * MATCH_RATE))
    if size < 32 or last < first:
        return None
    target = target - target.mean()
    energy = float(target @ target)
    if energy < size * MIN_RMS ** 2:
        return None
    search = reference[first:last + size]
    padded = 1 << (len(search) + size - 1).bit_length()
    dots = np.fft.irfft(np.fft.rfft(search, padded) * np.fft.rfft(target[::-1], padded), padded)
    sums = np.r_[0., np.cumsum(search)]
    squares = np.r_[0., np.cumsum(search ** 2)]
    source_energy = squares[size:] - squares[:-size] - (sums[size:] - sums[:-size]) ** 2 / size
    scores = dots[size - 1:len(search)] / np.sqrt(energy * np.maximum(source_energy, 1e-10))
    scores[source_energy < size * MIN_RMS ** 2] = -1
    best = int(np.argmax(scores))
    score = float(scores[best])
    if score < MIN_CORRELATION:
        return None
    # Adjacent samples represent the same match. Distant near-equal peaks are ambiguous.
    radius = MATCH_RATE // 50
    second = max(float(np.max(scores[:max(0, best - radius)], initial=-1)),
                 float(np.max(scores[best + radius + 1:], initial=-1)))
    if second >= score - .035:
        return None
    source_s = (first + best) / MATCH_RATE
    return {"observed_s": round(at_s, 6), "source_s": round(source_s, 6),
            "age_ms": round((at_s - source_s) * 1000, 3), "correlation": round(min(1., score), 4)}


def observation_times(duration, edges=()):
    times = set(np.arange(.25, duration - WINDOW_S, .5))
    for end in edges:
        times.update(end + offset for offset in (.05, .25, .75, 1.5) if end + offset + WINDOW_S <= duration)
    ordered = sorted(times)
    if len(ordered) > MAX_WINDOWS:
        selector = np.linspace(0, len(ordered) - 1, MAX_WINDOWS, dtype=int)
        ordered = [ordered[i] for i in selector]
    return [float(at) for at in ordered]


def summarize_matches(matches, old_audio_ms, attempted):
    return {"status": "matched" if matches else "insufficient_match",
            "attempted_windows": attempted, "matched_windows": len(matches),
            "max_age_ms": max((item["age_ms"] for item in matches), default=None),
            "old_audio_windows": sum(item["age_ms"] >= old_audio_ms for item in matches), "matches": matches}



def align_path(playback, observed, old_audio_ms):
    """Join overlapping matched source windows, independent of packet timestamps."""
    by_source = sorted(playback, key=lambda row: row["source_s"])
    origins = [item["source_s"] for item in by_source]
    result = []
    for hop in observed:
        origin = hop["source_s"]
        tolerance = WINDOW_S / 2
        first = bisect_left(origins, origin - tolerance)
        last = bisect_right(origins, origin + tolerance)
        for heard in by_source[first:last]:
            source_s = max(origin, heard["source_s"])
            capture_s = hop["observed_s"] + source_s - origin
            playback_s = heard["observed_s"] + source_s - heard["source_s"]
            if capture_s > playback_s or heard["age_ms"] < old_audio_ms:
                continue
            result.append({"source_s": round(source_s, 6), "captured_s": round(capture_s, 6),
                           "played_s": round(playback_s, 6), "age_at_capture_ms": hop["age_ms"],
                           "age_at_playback_ms": heard["age_ms"]})
    return sorted(result, key=lambda row: -row["age_at_playback_ms"])[:20]

def analyze(path, output, reference_channel, pause_ms, silence_db, old_audio_ms, max_age_ms, capture=None):
    if not 50 <= pause_ms <= 10000 or not -90 <= silence_db <= -20:
        raise ValueError("暂停须为 50–10000 ms，近静音门限须为 −90 至 −20 dBFS")
    if not 50 <= old_audio_ms <= max_age_ms <= 60000 or max_age_ms < 250:
        raise ValueError("旧音门限须不大于搜索窗口，搜索窗口须为 250–60000 ms")
    path = Path(path)
    digest = sha256(path)
    audio = read_wav(path)
    if audio.samples.shape[1] != 2 or audio.duration_s < .5:
        raise ValueError("回声积压分析需要至少 0.5 秒的真实双声道 PCM16 WAV")
    if np.array_equal(audio.samples[:, 0], audio.samples[:, 1]):
        raise ValueError("左右轨完全相同，无法确认是独立原声和实际返听")
    tracks = audio.samples if reference_channel == "left" else audio.samples[:, ::-1]
    pauses = quiet_intervals(tracks, audio.sample_rate, pause_ms, silence_db)
    reference = resample(tracks[:, 0], audio.sample_rate)
    playback = resample(tracks[:, 1], audio.sample_rate)
    times = observation_times(audio.duration_s, (pause["end_s"] for pause in pauses))
    matches = []
    for at in times:
        start = int(at * MATCH_RATE + 1e-7)
        sample = playback[start:start + round(WINDOW_S * MATCH_RATE)]
        if len(sample) != round(WINDOW_S * MATCH_RATE):
            continue
        match = match_source(reference, sample, at, max_age_ms / 1000)
        if match:
            matches.append(match)
    result = {"schema_version": "1.0", "tool_version": __version__, "kind": "echo_audit",
              "audio": str(path.resolve()), "audio_sha256": digest,
              "duration_s": audio.duration_s, "reference_channel": reference_channel,
              "settings": {"pause_ms": pause_ms, "silence_db": silence_db, "old_audio_ms": old_audio_ms,
                           "max_age_ms": max_age_ms, "minimum_correlation": MIN_CORRELATION},
              "quiet_intervals": pauses, "playback": summarize_matches(matches, old_audio_ms, len(times))}
    if capture:
        from .echo_capture import observe_capture
        try:
            result["capture"] = observe_capture(reference, audio.duration_s, pause_ms, old_audio_ms, max_age_ms, capture)
            for flow in result["capture"]["flows"].values():
                if "content" in flow:
                    flow["aligned_old_audio"] = align_path(matches, flow["content"]["matches"], old_audio_ms)
        except (OSError, ValueError) as error:
            result["capture"] = {"status": "error", "error": str(error)}
    if sha256(path) != digest:
        raise ValueError("录音在分析期间发生变化")
    output = new_output(output)
    write_json(output / "echo.json", result)
    (output / "echo.md").write_text(render(result), encoding="utf-8")
    return result


def render(result):
    playback = result["playback"]
    lines = ["# 回声拨测积压分析", "", f"原声在{('左' if result['reference_channel'] == 'left' else '右')}轨。",
             f"实际返听找到 {playback['matched_windows']}/{playback['attempted_windows']} 段唯一波形匹配。"]
    if playback["matched_windows"]:
        worst = max(playback["matches"], key=lambda match: match["age_ms"])
        lines.append(f"匹配到的最旧返听在 {worst['observed_s']:.3f} 秒播出，来自原声 {worst['source_s']:.3f} 秒，"
                     f"相差 {worst['age_ms']:.1f} ms。")
    if result["quiet_intervals"]:
        lines.extend(["", "## 返听近静音", "", "| 起秒 | 止秒 | 时长（ms） |", "| ---: | ---: | ---: |"])
        for item in sorted(result["quiet_intervals"], key=lambda gap: -gap["duration_ms"])[:20]:
            lines.append(f"| {item['start_s']:.3f} | {item['end_s']:.3f} | {item['duration_ms']:.1f} |")
    capture = result.get("capture", {})
    if capture.get("error"):
        lines.extend(["", "## RTP 采集点观察", "", "抓包分析失败，录音结果已保留。错误明细见 echo.json。"])
    if "flows" in capture:
        lines.extend(["", "## RTP 采集点观察", "",
                      "| 流向 | RTP 包数 | 最长包间隔（ms） | 匹配到的最旧音频（ms） |",
                      "| --- | ---: | ---: | ---: |"])
        for name, flow in capture["flows"].items():
            age = flow.get("content", {}).get("max_age_ms")
            age_text = "未测得" if age is None else f"{age:.1f}"
            maximum = flow["max_interarrival_ms"]
            maximum_text = "未覆盖" if maximum is None else f"{maximum:.1f}"
            lines.append(f"| {name} | {flow['packets']} | {maximum_text} | {age_text} |")
        for name, flow in capture["flows"].items():
            if flow["gaps"]:
                gap = flow["gaps"][0]
                continuing = "、".join(f"{peer} {metrics['observed_packets']} 包"
                                      for peer, metrics in gap["other_flows"].items() if metrics["full_window"])
                lines.append(f"- {name} 最长间隔在 {gap['start_s']:.3f}–{gap['end_s']:.3f} 秒，"
                             f"相邻包序号增量 {gap['sequence_delta']}。"
                             + (f"同期 {continuing}。" if continuing else ""))
            if flow.get("aligned_old_audio"):
                item = flow["aligned_old_audio"][0]
                lines.append(f"- {name} 在 {item['captured_s']:.3f} 秒已看到来自原声 "
                             f"{item['source_s']:.3f} 秒的声音，当时已过 {item['age_at_capture_ms']:.1f} ms。"
                             f"实际返听在 {item['played_s']:.3f} 秒播放这一段。")
        lines.append("各流间隔与同期其他流到包数量见 echo.json。")
    lines.extend(["", "波形用已知原声匹配，未匹配时段的音频年龄未知。"
                  "RTP 到包间隔和序号是采集点观察，仍需线程或进程证据定位内部触发原因。"])
    return "\n".join(lines) + "\n"
