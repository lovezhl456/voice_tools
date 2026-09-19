"""按原始幅度度量，活动电平不是语音响度或音质评分。"""
import numpy as np


def waveform(audio, bins=1200):
    data = audio.samples
    width = max(1, int(np.ceil(len(data) / bins)))
    peaks = []
    for channel in range(data.shape[1]):
        peaks.append([[round(float(x.min()), 5), round(float(x.max()), 5)]
                      for i in range(0, len(data), width)
                      for x in [data[i:i + width, channel]]])
    return {"duration_s": audio.duration_s, "bin_s": width / audio.sample_rate, "channels": peaks}


def intervals(mask, frame_s, duration):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return [[round(int(a) * frame_s, 6), round(min(int(b) * frame_s, duration), 6)]
            for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def inspect_health(audio, threshold_db=-45):
    if not np.isfinite(threshold_db) or not -100 <= threshold_db <= -1:
        raise ValueError("活动门限须为 -100 至 -1 dBFS")
    data, rate = audio.samples, audio.sample_rate
    size = max(1, round(rate * .02))
    channels = []
    for channel in range(data.shape[1]):
        track = data[:, channel]
        levels, dc, clipping = [], [], []
        for i in range(0, len(track), size):
            frame = track[i:i + size]
            mean = float(frame.mean())
            levels.append(20 * np.log10(max(float(np.sqrt(np.mean((frame - mean) ** 2))), 1e-6)))
            dc.append(abs(mean) > .05)
            clipping.append(bool(np.any(np.abs(frame) >= .999)))
        levels = np.asarray(levels)
        active = levels >= threshold_db
        weights = np.minimum(size, len(track) - np.arange(len(levels)) * size)
        channels.append({"channel": channel, "peak_dbfs": round(float(20 * np.log10(max(float(np.max(np.abs(track))), 1e-6))), 2),
                         "median_dbfs": round(float(np.median(levels)), 2),
                         "active_median_dbfs": round(float(np.median(levels[active])), 2) if active.any() else None,
                         "silence_fraction": round(float(weights[~active].sum() / len(track)), 6),
                         "clipping_fraction": round(float(np.mean(np.abs(track) >= .999)), 6),
                         "clipping_spans": intervals(clipping, size / rate, audio.duration_s),
                         "dc_spans": intervals(dc, size / rate, audio.duration_s),
                         "silence_spans": intervals(~active, size / rate, audio.duration_s)})
    similarity = None
    duplicate = False
    if data.shape[1] == 2:
        duplicate = bool(np.max(np.abs(data[:, 0] - data[:, 1])) < 1e-5)
        # 同步相关性只用作复核线索；静音轨不产生虚假的相关系数。
        sampled = data[::max(1, len(data) // 100000)]
        if np.min(np.std(sampled, axis=0)) > 1e-6:
            similarity = round(float(np.corrcoef(sampled.T)[0, 1]), 6)
    return {"threshold_db": threshold_db, "channels": channels, "duplicate_channels": duplicate,
            "correlation": similarity, "similarity_needs_review": duplicate or (similarity is not None and abs(similarity) > .98),
            "notice": "能量活动可能是噪声；相关性不能确认串音或角色。压缩格式的指标基于解码后的 PCM16。"}
